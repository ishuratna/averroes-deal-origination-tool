"""
iXBRL accounts: exact, free, auditable financials from Companies House.

Digitally filed UK accounts are served by the CH Document API as machine-
tagged iXBRL when requested with Accept: application/xhtml+xml. Every figure
carries a taxonomy concept (TurnoverRevenue, CashBankOnHand,
AverageNumberEmployeesDuringPeriod...), a context (which financial period)
and scaling attributes - so the numbers are EXACT and cost nothing to read.

This replaces the Gemini PDF read for every digitally filed account (the
overwhelming majority). The PDF+Gemini path remains only as the fallback for
scanned paper filings, where there is genuinely nothing machine-readable.

Two honest limits, unchanged by format:
  * Filleted/micro accounts often contain NO P&L, so revenue is absent from
    the FILING itself - iXBRL cannot conjure it, and neither could Gemini.
  * The output shape matches _parse_accounts_pdf_with_gemini exactly, so
    everything downstream (scoring, IC memo, profile) is untouched.
"""
import logging
import os
import re
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_DOC_API = "https://document-api.company-information.service.gov.uk"

# taxonomy concept (lowercased local name) -> our field stem. FRS 101/102/105
# taxonomies vary the names slightly; every variant seen in real filings is
# listed. Order matters where concepts overlap: first match wins per period.
_CONCEPTS = {
    "revenue": ("turnoverrevenue", "turnover", "revenue"),
    "gross_profit": ("grossprofitloss", "grossprofit"),
    "profit": ("profitlossbeforetax", "profitlossonordinaryactivitiesbeforetax", "profitloss"),
    "total_assets": ("totalassets",),
    "net_assets": ("netassetsliabilities", "netassetsliabilitiesincludingpensionassetliability"),
    "cash": ("cashbankonhand", "cashbankinhand", "cashcashequivalents"),
    "employees": ("averagenumberemployeesduringperiod", "averagenumberofemployeesduringperiod",
                  "averagenumberofemployees"),
}


def fetch_ixbrl(filing: dict) -> Optional[str]:
    """The iXBRL document for one accounts filing, or None when only a scan
    exists. Same metadata walk as the PDF download, different Accept."""
    api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    if not api_key:
        return None
    auth = (api_key, "")
    doc_meta_url = (filing.get("links") or {}).get("document_metadata")
    if not doc_meta_url:
        return None
    try:
        if doc_meta_url.startswith("/"):
            doc_meta_url = f"{CH_API_BASE}{doc_meta_url}"
        meta = requests.get(doc_meta_url, auth=auth, timeout=15)
        meta.raise_for_status()
        meta = meta.json()
        # The metadata says which renditions exist - skip the request entirely
        # when no xhtml is offered (paper scans have only the PDF).
        resources = meta.get("resources") or {}
        if resources and not any("xhtml" in ct for ct in resources):
            return None
        doc_url = (meta.get("links") or {}).get("document")
        if not doc_url:
            return None
        if doc_url.startswith("/"):
            doc_url = f"{CH_DOC_API}{doc_url}"
        resp = requests.get(doc_url, auth=auth,
                            headers={"Accept": "application/xhtml+xml"},
                            timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("Content-Type", "")
        text = resp.text or ""
        if "xhtml" not in ctype and "<ix:" not in text.lower():
            return None
        return text
    except Exception as e:
        logger.warning(f"[iXBRL] fetch failed: {e}")
        return None


def _num(tag) -> Optional[float]:
    """The numeric value of an ix:nonFraction, honouring sign and scale."""
    raw = tag.get_text(" ", strip=True)
    raw = re.sub(r"[£$€,\s]", "", raw)
    if raw in ("", "-", "–"):
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    try:
        v *= 10 ** int(tag.get("scale") or 0)
    except (TypeError, ValueError):
        pass
    if (tag.get("sign") or "") == "-":
        v = -v
    return v


def _local(name: str) -> str:
    return (name or "").rsplit(":", 1)[-1].strip().lower()


def parse_ixbrl(xhtml: str, company_number: str = "") -> Dict:
    """Tagged facts -> the exact shape the Gemini PDF parse returns. Pure.

    Identity is verified IN the document: iXBRL accounts carry the company's
    own registered number (UKCompaniesHouseRegisteredNumber). If it disagrees
    with the number we asked for, the parse is refused - a wrong document must
    never become another company's financials.
    """
    from bs4 import BeautifulSoup
    if not (xhtml or "").strip():
        return {}
    soup = BeautifulSoup(xhtml, "html.parser")

    # context id -> period end date (duration endDate, or instant for
    # balance-sheet facts).
    ctx_end: Dict[str, str] = {}
    for ctx in soup.find_all(lambda t: t.name and t.name.endswith("context")):
        cid = ctx.get("id") or ""
        end = ctx.find(lambda t: t.name and t.name.endswith("enddate"))
        instant = ctx.find(lambda t: t.name and t.name.endswith("instant"))
        when = (end.get_text(strip=True) if end else "") or \
               (instant.get_text(strip=True) if instant else "")
        if cid and when:
            ctx_end[cid] = when

    # The document states whose accounts these are - verify before reading.
    if company_number:
        want = re.sub(r"[^0-9A-Z]", "", company_number.upper()).lstrip("0")
        for t in soup.find_all(lambda t: t.name and t.name.endswith("nonnumeric")):
            if _local(t.get("name") or "").endswith("companieshouseregisterednumber"):
                got = re.sub(r"[^0-9A-Z]", "", t.get_text(strip=True).upper()).lstrip("0")
                if got and want and got != want:
                    return {"error": f"iXBRL document is for company #{got}, not #{company_number} - refused"}
                break

    # field -> {period_end: value}; first fact wins per (field, period).
    facts: Dict[str, Dict[str, float]] = {k: {} for k in _CONCEPTS}
    for tag in soup.find_all(lambda t: t.name and t.name.endswith("nonfraction")):
        concept = _local(tag.get("name") or "")
        ref = tag.get("contextref") or ""
        when = ctx_end.get(ref, "")
        if not when:
            continue
        for field, names in _CONCEPTS.items():
            if concept in names and when not in facts[field]:
                v = _num(tag)
                if v is not None:
                    facts[field][when] = v
                break

    # Which period is current vs prior: the two newest end dates seen.
    ends = sorted({d for by in facts.values() for d in by}, reverse=True)
    if not ends:
        return {}
    cur = ends[0]
    prior = ends[1] if len(ends) > 1 else ""

    def pick(field, when):
        v = facts[field].get(when)
        return v if v is not None else None

    out = {
        "revenue_current": pick("revenue", cur), "revenue_prior": pick("revenue", prior),
        "profit_current": pick("profit", cur), "profit_prior": pick("profit", prior),
        "gross_profit_current": pick("gross_profit", cur), "gross_profit_prior": pick("gross_profit", prior),
        "total_assets_current": pick("total_assets", cur), "total_assets_prior": pick("total_assets", prior),
        "net_assets_current": pick("net_assets", cur), "net_assets_prior": pick("net_assets", prior),
        "cash_current": pick("cash", cur), "cash_prior": pick("cash", prior),
        "employees": int(facts["employees"][cur]) if facts["employees"].get(cur) is not None else None,
        "employees_prior": int(facts["employees"][prior]) if prior and facts["employees"].get(prior) is not None else None,
        "period_end_current": cur, "period_end_prior": prior or None,
        "filing_type": None,   # the orchestrator derives it from the filing description
        "currency": "GBP",
        "notes": "Parsed from iXBRL (machine-tagged filing): exact figures, zero AI.",
        "_source": "ixbrl",
    }
    # A parse with no substance is a failure - let the caller fall back.
    core = ("revenue_current", "gross_profit_current", "profit_current",
            "total_assets_current", "net_assets_current", "cash_current", "employees")
    if not any(out[k] is not None for k in core):
        return {}
    return out
