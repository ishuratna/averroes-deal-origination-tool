"""
Companies House Financial Extraction Service

Robust 4-step pipeline:
1. Search CH API for the company → verify correct match
2. Get filing history → find latest accounts filing
3. Download the actual accounts PDF from Companies House
4. Feed PDF to Gemini → extract structured financial data from the real document

Requires: COMPANIES_HOUSE_API_KEY (free — register at developer.company-information.service.gov.uk)
          GEMINI_API_KEY (for PDF parsing)
"""

import os
import io
import json
import logging
import base64
import re as _re
import requests
from difflib import SequenceMatcher as _SequenceMatcher
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Companies House API base URLs
CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_DOC_API = "https://document-api.company-information.service.gov.uk"


def _ch_auth() -> Tuple[str, str]:
    """Return HTTP Basic auth tuple for CH API (key as username, blank password)."""
    key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    return (key, "")


# ── Step 1: Search for company ──────────────────────────────────────────────

def _search_company(company_name: str) -> List[dict]:
    """Search Companies House for a company by name. Returns top results."""
    auth = _ch_auth()
    if not auth[0]:
        return []

    try:
        resp = requests.get(
            f"{CH_API_BASE}/search/companies",
            params={"q": company_name, "items_per_page": 10},
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except Exception as e:
        logger.error(f"CH search failed for '{company_name}': {e}")
        return []


# Words that carry NO identity information — legal suffixes and generic
# industry terms. Overlap on these must never count as a name match.
# (Fix for false positives like "Vrinsoft Technology Inc" matching
#  "ALL EAT APP NETWORK TECHNOLOGY INCORPORATED LTD" via the word "technology".)
_GENERIC_NAME_TOKENS = {
    "ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "co", "corp",
    "corporation", "company", "group", "holdings", "holding", "uk", "gb",
    "the", "and", "of", "a", "an", "&",
    "technologies", "technology", "tech", "software", "solutions", "solution",
    "services", "service", "systems", "system", "digital", "global",
    "international", "app", "apps", "network", "networks", "labs", "lab",
    "studio", "studios", "consulting", "consultancy", "ventures", "partners",
    "media", "online", "platform", "platforms", "cloud", "it", "europe", "london",
}


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    name = _re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    return _re.sub(r"\s+", " ", name).strip()


def _core_name(name: str) -> str:
    """Normalized name with generic/legal tokens removed (order preserved)."""
    return " ".join(t for t in _normalize_name(name).split() if t not in _GENERIC_NAME_TOKENS)


def _name_gate(company_name: str, title: str) -> Optional[Tuple[str, int]]:
    """
    HARD GATE on name similarity. Returns (gate_level, name_score) if the two
    names plausibly refer to the same company, else None (candidate discarded —
    no amount of status/SIC/address bonus can revive it).
    """
    n1, n2 = _normalize_name(company_name), _normalize_name(title)
    if not n1 or not n2:
        return None
    if n1 == n2:
        return ("exact", 100)

    c1, c2 = _core_name(company_name), _core_name(title)

    # Same distinctive words (e.g. "vrinsoft" == "vrinsoft") ignoring suffixes
    if c1 and c1 == c2:
        return ("exact-core", 90)

    # One distinctive name contains the other (e.g. "monzo" in "monzo bank")
    if c1 and c2 and len(min(c1, c2, key=len)) >= 4 and (c1 in c2 or c2 in c1):
        return ("contains", 75)

    # Fuzzy: near-identical distinctive names (typos, spacing)
    if c1 and c2:
        ratio = _SequenceMatcher(None, c1, c2).ratio()
        if ratio >= 0.85:
            return ("fuzzy", 65)
        # Partial: at least one shared DISTINCTIVE word + strong overall similarity
        shared = set(c1.split()) & set(c2.split())
        if shared and ratio >= 0.6:
            return ("partial", 50)

    return None


def _pick_best_match(
    results: List[dict],
    company_name: str,
    sector: str = "",
    description: str = "",
) -> Optional[dict]:
    """
    Pick the best matching company from CH search results.

    STRINGENT: name similarity is a hard gate — candidates whose names don't
    plausibly refer to the same company are discarded outright. Status/SIC/
    address only rank candidates that already passed the gate.
    """
    if not results:
        return None

    context_lower = f"{sector} {description}".lower()

    scored = []
    for item in results:
        title = item.get("title") or ""

        gate = _name_gate(company_name, title)
        if gate is None:
            continue  # name doesn't match — discard regardless of other signals
        gate_level, score = gate

        # Small tie-breaker bonuses (can never rescue a failed name gate)
        status = (item.get("company_status") or "").lower()
        if status == "active":
            score += 10
        elif status == "dissolved":
            score -= 15

        sic_codes = item.get("sic_codes") or []
        snippet = (item.get("snippet") or "").lower()
        if any(kw in snippet or kw in " ".join(sic_codes) for kw in ["software", "tech", "data", "digital", "saas", "platform", "cloud"]):
            if any(kw in context_lower for kw in ["software", "tech", "data", "digital", "saas", "platform", "cloud"]):
                score += 5

        address = item.get("address", {})
        country = (address.get("country") or "").lower()
        if "united kingdom" in country or "england" in country or "wales" in country or "scotland" in country:
            score += 5

        item["_match_gate"] = gate_level
        scored.append((score, item))

    if not scored:
        logger.warning(f"[CH] No candidate passed the name gate for '{company_name}' — refusing to match.")
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    logger.info(f"[CH] Best match for '{company_name}': '{best.get('title')}' (gate={best.get('_match_gate')}, score={best_score})")
    return best


# ── Step 2: Get company profile + filing history ─────────────────────────────

def _get_company_profile(company_number: str) -> Optional[dict]:
    """Fetch the full company profile from CH API."""
    auth = _ch_auth()
    try:
        resp = requests.get(
            f"{CH_API_BASE}/company/{company_number}",
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"CH profile fetch failed for {company_number}: {e}")
        return None


def _get_accounts_filings(company_number: str, max_items: int = 10) -> List[dict]:
    """Fetch recent accounts filings from CH filing history."""
    auth = _ch_auth()
    try:
        resp = requests.get(
            f"{CH_API_BASE}/company/{company_number}/filing-history",
            params={"category": "accounts", "items_per_page": max_items},
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        # Filter to just accounts
        accounts = [
            item for item in items
            if item.get("category") == "accounts"
        ]
        return accounts
    except Exception as e:
        logger.error(f"CH filing history failed for {company_number}: {e}")
        return []


# ── Step 3: Download the accounts PDF ────────────────────────────────────────

def _download_accounts_pdf(filing: dict) -> Optional[bytes]:
    """
    Download the actual PDF document for an accounts filing.
    The filing dict contains links.document_metadata which gives us the doc ID.
    """
    auth = _ch_auth()

    # Get document metadata link
    links = filing.get("links", {})
    doc_meta_url = links.get("document_metadata")

    if not doc_meta_url:
        # Try constructing from self link
        self_link = links.get("self", "")
        # Filing self link looks like /company/12345678/filing-history/MzM1NTY4...
        # We need the transaction ID which is the last part
        logger.warning(f"No document_metadata link in filing: {filing.get('description', 'unknown')}")
        return None

    try:
        # Ensure we have the full URL
        if doc_meta_url.startswith("/"):
            doc_meta_url = f"{CH_API_BASE}{doc_meta_url}"

        # Step 1: Get document metadata
        meta_resp = requests.get(doc_meta_url, auth=auth, timeout=15)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        # The metadata contains links.document which points to the actual document content
        doc_links = meta.get("links", {})
        doc_url = doc_links.get("document")

        if not doc_url:
            logger.warning(f"No document download link in metadata: {doc_meta_url}")
            return None

        # Ensure full URL
        if doc_url.startswith("/"):
            doc_url = f"{CH_DOC_API}{doc_url}"

        # Step 2: Download the PDF (request PDF content type)
        pdf_resp = requests.get(
            doc_url,
            auth=auth,
            headers={"Accept": "application/pdf"},
            timeout=30,
            allow_redirects=True,
        )
        pdf_resp.raise_for_status()

        content_type = pdf_resp.headers.get("Content-Type", "")
        if "pdf" in content_type or len(pdf_resp.content) > 1000:
            logger.info(f"Downloaded accounts PDF: {len(pdf_resp.content)} bytes")
            return pdf_resp.content
        else:
            logger.warning(f"Document response was not PDF (content-type: {content_type})")
            return None

    except Exception as e:
        logger.error(f"PDF download failed: {e}")
        return None


# ── Step 4: Parse PDF with Gemini ─────────────────────────────────────────────

def _parse_accounts_pdf_with_gemini(
    pdf_bytes: bytes,
    company_name: str,
    company_number: str,
    filing_date: str = "",
) -> Dict:
    """
    Send the actual accounts PDF to Gemini and extract structured financial data.
    This is the key step — we're parsing the REAL document, not asking Gemini to guess.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return {"error": "GEMINI_API_KEY not set"}

    try:
        import google.generativeai as genai

        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Encode PDF as base64 for Gemini
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        prompt = f"""You are a UK chartered accountant. I am giving you the ACTUAL filed accounts PDF
for company "{company_name}" (Companies House #{company_number}, filing date: {filing_date}).

Extract ALL available financial data from this document. This is a real UK statutory filing.

EXTRACT THE FOLLOWING (set to null if not present in the document):

1. REVENUE / TURNOVER — Look for "Turnover", "Revenue", "Total revenue", "Net revenue"
   - Many micro-entity and abbreviated accounts do NOT include turnover — that's OK, set to null
   - If a Profit & Loss / Income Statement exists, turnover is usually the top line

2. PROFIT / LOSS BEFORE TAX — Look for "Profit/(loss) before taxation", "Profit before tax"

2b. GROSS PROFIT — Look for "Gross profit" in the P&L. IMPORTANT: many small/filleted
   accounts hide turnover but still show gross profit — extract it whenever present,
   it is our best path to estimating revenue.

3. TOTAL ASSETS — Look in the Balance Sheet for "Total assets"

4. NET ASSETS — "Net assets", "Total net assets", or "Total assets less current liabilities" minus long-term liabilities

5. CASH — "Cash at bank and in hand", "Cash and cash equivalents"

6. EMPLOYEES — "Average number of employees", "Employee information". UK accounts almost
   always show this for BOTH the current and the prior year side by side — extract both.

7. FILING TYPE — Is this: "full" accounts, "small" company accounts, "micro-entity" accounts,
   "abbreviated" accounts, "medium" accounts, "filleted" accounts, or "dormant" accounts?

8. PERIOD DATES — What financial period does this cover? (period start and end dates)

9. COMPARATIVE FIGURES — Many UK accounts show current year AND prior year side by side.
   Extract BOTH if available. Label clearly which is current vs prior year.

IMPORTANT RULES:
- Only extract numbers that ACTUALLY appear in the document. Do NOT estimate or calculate.
- All monetary values in raw GBP (e.g., 5000000 for £5M). Do NOT format as strings.
- If the accounts are micro-entity or abbreviated with no P&L, just extract the Balance Sheet figures.
- Negative numbers should be negative (e.g., -250000 for a £250K loss).
- Look for notes to accounts that might contain additional financial data.

Return ONLY valid JSON:
{{
    "revenue_current": null or number,
    "revenue_prior": null or number,
    "profit_current": null or number,
    "profit_prior": null or number,
    "gross_profit_current": null or number,
    "gross_profit_prior": null or number,
    "total_assets_current": null or number,
    "total_assets_prior": null or number,
    "net_assets_current": null or number,
    "net_assets_prior": null or number,
    "cash_current": null or number,
    "cash_prior": null or number,
    "employees": null or integer,
    "employees_prior": null or integer,
    "period_end_current": "YYYY-MM-DD or null",
    "period_end_prior": "YYYY-MM-DD or null",
    "filing_type": "full or small or micro-entity or abbreviated or medium or filleted or dormant",
    "currency": "GBP or other",
    "notes": "Brief summary of what's in the accounts, e.g. 'Micro-entity accounts, Balance Sheet only, no P&L'"
}}"""

        # Send PDF as file data to Gemini
        response = model.generate_content(
            [
                {"mime_type": "application/pdf", "data": pdf_b64},
                prompt,
            ],
            generation_config={"response_mime_type": "application/json"},
        )

        text = response.text
        if not text:
            return {"error": "Gemini returned empty response for PDF parsing"}

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        logger.info(f"Gemini extracted financials from PDF for {company_name}: "
                     f"revenue={result.get('revenue_current')}, assets={result.get('total_assets_current')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Gemini PDF parse JSON error for {company_name}: {e}")
        return {"error": f"Failed to parse Gemini response: {str(e)}"}
    except Exception as e:
        logger.error(f"Gemini PDF parsing failed for {company_name}: {e}")
        return {"error": str(e)}


# ── Main orchestrator ─────────────────────────────────────────────────────────

# ── Registry intelligence: PSC, officers, charges, share allotments ──────────

_INSTITUTIONAL_HINTS = ["ventures", "capital", "partners", "equity", "fund", " lp",
                        "investments limited", "holdings plc", "nominees", "trustees"]


def get_psc_summary(company_number: str) -> Dict:
    """
    Persons with Significant Control — who really owns the company.
    Returns {psc_summary, ownership_verified, psc_individuals: [names]}.
    """
    auth = _ch_auth()
    try:
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/persons-with-significant-control",
                            auth=auth, timeout=15)
        if resp.status_code == 404:
            return {"psc_summary": "", "ownership_verified": "", "psc_individuals": []}
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as e:
        logger.warning(f"[CH] PSC fetch failed for {company_number}: {e}")
        return {"psc_summary": "", "ownership_verified": "", "psc_individuals": []}

    def _band(natures: list) -> str:
        text = " ".join(natures or [])
        if "75-to-100" in text:
            return "75-100%"
        if "50-to-75" in text:
            return "50-75%"
        if "25-to-50" in text:
            return "25-50%"
        return "unknown %"

    individuals, corporates, lines = [], [], []
    for p in items:
        if p.get("ceased_on"):
            continue
        name = p.get("name", "")
        kind = p.get("kind", "")
        band = _band(p.get("natures_of_control"))
        if "individual" in kind:
            individuals.append((name, band))
            lines.append(f"{name} (individual, {band})")
        else:
            corporates.append((name, band))
            lines.append(f"{name} (entity, {band})")

    # Classify ownership from the register
    if individuals and not corporates:
        top_band = individuals[0][1]
        if any(b in ("75-100%",) for _, b in individuals):
            verified = "Founder/family-owned (75-100% individual control)"
        elif any(b in ("50-75%",) for _, b in individuals):
            verified = "Founder-controlled (50-75% individual control)"
        else:
            verified = f"Individually held ({top_band})"
    elif corporates:
        inst = [n for n, _ in corporates if any(h in n.lower() for h in _INSTITUTIONAL_HINTS)]
        if inst:
            verified = f"Institutional investor on register: {inst[0]}"
        else:
            verified = f"Held via entity: {corporates[0][0]}"
        if individuals:
            verified += f" + individual holders"
    else:
        verified = "No active PSC registered"

    return {
        "psc_summary": "; ".join(lines)[:500],
        "ownership_verified": verified,
        "psc_individuals": [n for n, _ in individuals],
    }


def get_officers_summary(company_number: str, max_officers: int = 6) -> Dict:
    """Active directors: names, roles, birth year. Returns {officers_summary, directors: [...]}."""
    auth = _ch_auth()
    try:
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/officers",
                            params={"items_per_page": 25}, auth=auth, timeout=15)
        if resp.status_code == 404:
            return {"officers_summary": "", "directors": []}
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as e:
        logger.warning(f"[CH] Officers fetch failed for {company_number}: {e}")
        return {"officers_summary": "", "directors": []}

    directors, lines = [], []
    for o in items:
        if o.get("resigned_on") or "director" not in (o.get("officer_role") or ""):
            continue
        name = o.get("name", "")
        dob = o.get("date_of_birth") or {}
        birth_year = dob.get("year")
        directors.append({"name": name, "birth_year": birth_year})
        lines.append(f"{name}" + (f" (b. {birth_year})" if birth_year else ""))
        if len(directors) >= max_officers:
            break

    return {"officers_summary": "; ".join(lines)[:400], "directors": directors}


def get_charges_summary(company_number: str) -> Dict:
    """Outstanding charges (secured debt). Returns {charges_count, charges_summary}."""
    auth = _ch_auth()
    try:
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/charges",
                            auth=auth, timeout=15)
        if resp.status_code == 404:
            return {"charges_count": 0, "charges_summary": ""}
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[CH] Charges fetch failed for {company_number}: {e}")
        return {"charges_count": None, "charges_summary": ""}

    outstanding = [c for c in data.get("items", []) if (c.get("status") or "") == "outstanding"]
    holders = []
    for c in outstanding[:4]:
        for p in c.get("persons_entitled", []) or []:
            nm = p.get("name", "")
            if nm and nm not in holders:
                holders.append(nm)
    summary = f"{len(outstanding)} outstanding" + (f" — held by {', '.join(holders[:3])}" if holders else "")
    return {"charges_count": len(outstanding), "charges_summary": summary if outstanding else ""}


def get_capital_events(company_number: str) -> Dict:
    """
    Share allotments (SH01 etc.) from filing history — a quiet-fundraise detector.
    Returns {last_share_allotment: 'YYYY-MM-DD' or ''}.
    """
    auth = _ch_auth()
    try:
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/filing-history",
                            params={"category": "capital", "items_per_page": 5},
                            auth=auth, timeout=15)
        if resp.status_code == 404:
            return {"last_share_allotment": ""}
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as e:
        logger.warning(f"[CH] Capital filings fetch failed for {company_number}: {e}")
        return {"last_share_allotment": ""}

    for f in items:
        if "allotment" in (f.get("description") or "") or (f.get("type") or "").startswith("SH01"):
            return {"last_share_allotment": f.get("date", "")}
    return {"last_share_allotment": items[0].get("date", "") if items else ""}


def extract_ch_financials(
    company_name: str,
    sector: str = "",
    region: str = "",
    description: str = "",
    gcs_handler=None,
) -> Dict:
    """
    Full pipeline: Search CH → Find company → Download accounts PDF → Parse with Gemini.

    Returns structured dict with CH company info + extracted financial data.
    """
    ch_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    if not ch_key:
        return {"error": "COMPANIES_HOUSE_API_KEY not set — register free at developer.company-information.service.gov.uk"}
    if not gemini_key:
        return {"error": "GEMINI_API_KEY not set"}

    # ── Step 1: Search Companies House ──
    logger.info(f"[CH] Step 1: Searching Companies House for '{company_name}'...")
    results = _search_company(company_name)
    if not results:
        return {"error": f"No results found on Companies House for '{company_name}'"}

    # Pick best match
    best = _pick_best_match(results, company_name, sector, description)
    if not best:
        return {"error": f"No confident match found on Companies House for '{company_name}'"}

    company_number = best.get("company_number", "")
    official_name = best.get("title", "")
    logger.info(f"[CH] Step 1 result: '{official_name}' (#{company_number})")

    # Match confidence derives from the name gate the candidate passed
    gate_level = best.get("_match_gate", "")
    if gate_level in ("exact", "exact-core"):
        match_confidence = "high"
    elif gate_level in ("contains", "fuzzy"):
        match_confidence = "medium"
    else:  # "partial"
        match_confidence = "low"

    # ── Step 2: Get company profile ──
    logger.info(f"[CH] Step 2: Fetching profile for #{company_number}...")
    profile = _get_company_profile(company_number)

    ch_status = "Unknown"
    ch_incorporated = None
    ch_sic_codes = None
    ch_accounts_next_due = None

    if profile:
        ch_status = profile.get("company_status", "unknown").replace("_", " ").title()
        date_of_creation = profile.get("date_of_creation")
        ch_incorporated = date_of_creation
        sic = profile.get("sic_codes", [])
        ch_sic_codes = ", ".join(sic) if sic else None
        ch_accounts_next_due = (profile.get("accounts", {}) or {}).get("next_due")

    # ── Step 2b: Registry intelligence (PSC ownership, charges, share allotments) ──
    logger.info(f"[CH] Step 2b: Fetching PSC / charges / capital events for #{company_number}...")
    psc = get_psc_summary(company_number)
    charges = get_charges_summary(company_number)
    capital = get_capital_events(company_number)
    health = get_company_health(company_number)
    filing_intel = get_filing_intel(company_number)
    registry_intel = {
        "ch_psc_summary": psc["psc_summary"],
        "ch_ownership_verified": psc["ownership_verified"],
        "ch_charges_count": charges["charges_count"],
        "ch_charges_summary": charges["charges_summary"],
        "ch_last_share_allotment": capital["last_share_allotment"],
        "ch_accounts_next_due": ch_accounts_next_due,
        "ch_accounts_overdue": health["ch_accounts_overdue"],
        "ch_insolvency_summary": health["ch_insolvency_summary"],
        "ch_last_resolution": filing_intel["ch_last_resolution"],
        "ch_accounts_regime": filing_intel["ch_accounts_regime"],
    }
    # Cap table (Document API + one ungrounded parse) — best-effort.
    # PSC summary passed through for the independent founder-% cross-check.
    try:
        cap = get_cap_table(company_number, psc_summary=psc.get("psc_summary", ""))
        if cap:
            registry_intel.update(cap)
    except Exception as e:
        logger.warning(f"[CH] cap table failed for #{company_number} (non-fatal): {e}")

    # ── Step 3: Get filing history (accounts only) ──
    logger.info(f"[CH] Step 3: Fetching accounts filing history for #{company_number}...")
    filings = _get_accounts_filings(company_number, max_items=5)

    if not filings:
        logger.warning(f"[CH] No accounts filings found for #{company_number}")
        return {
            "ch_company_number": company_number,
            "ch_official_name": official_name,
            "ch_status": ch_status,
            "ch_incorporated_date": ch_incorporated,
            "ch_sic_codes": ch_sic_codes,
            "ch_match_confidence": match_confidence,
            "filing_type": None,
            "notes": "No accounts filings found on Companies House",
            "error": None,
            **registry_intel,
        }

    # Try to download PDFs for up to the latest 3 filings
    logger.info(f"[CH] Found {len(filings)} accounts filings. Attempting PDF downloads...")

    all_financials = []
    filing_type = None

    saved_pdf_path = None

    for i, filing in enumerate(filings[:3]):
        filing_date = filing.get("date", "")
        filing_desc = filing.get("description", "")
        logger.info(f"[CH] Step 4: Downloading PDF for filing {i+1}: {filing_desc} ({filing_date})...")

        # Detect filing type from description
        desc_lower = filing_desc.lower()
        if "micro" in desc_lower:
            filing_type = filing_type or "micro-entity"
        elif "small" in desc_lower:
            filing_type = filing_type or "small"
        elif "medium" in desc_lower:
            filing_type = filing_type or "medium"
        elif "abbreviated" in desc_lower:
            filing_type = filing_type or "abbreviated"
        elif "dormant" in desc_lower:
            filing_type = filing_type or "dormant"
        elif "full" in desc_lower or "group" in desc_lower:
            filing_type = filing_type or "full"
        elif "filleted" in desc_lower:
            filing_type = filing_type or "filleted"

        pdf_bytes = _download_accounts_pdf(filing)
        if not pdf_bytes:
            logger.warning(f"[CH] Could not download PDF for filing {i+1}")
            continue

        # Save first PDF to GCS for later review
        if gcs_handler and pdf_bytes and not saved_pdf_path:
            try:
                safe_name = company_name.replace(" ", "_").replace("/", "_")[:50]
                gcs_path = f"ch-filings/{company_number}/{safe_name}_{filing_date}.pdf"
                if gcs_handler.storage_client:
                    bucket = gcs_handler.storage_client.bucket(gcs_handler.bucket_name)
                    blob = bucket.blob(gcs_path)
                    blob.upload_from_string(data=pdf_bytes, content_type="application/pdf")
                    saved_pdf_path = gcs_path
                    logger.info(f"[CH] Saved accounts PDF to GCS: {gcs_path}")
            except Exception as e:
                logger.warning(f"[CH] Could not save PDF to GCS: {e}")

        # Parse the PDF with Gemini
        parsed = _parse_accounts_pdf_with_gemini(
            pdf_bytes, company_name, company_number, filing_date
        )

        if parsed.get("error"):
            logger.warning(f"[CH] Gemini parse failed for filing {i+1}: {parsed['error']}")
            continue

        parsed["_filing_date"] = filing_date
        all_financials.append(parsed)
        logger.info(f"[CH] Successfully parsed filing {i+1} ({filing_date})")

    # ── Step 5: Assemble final result ──
    result = {
        "ch_company_number": company_number,
        "ch_official_name": official_name,
        "ch_status": ch_status,
        "ch_incorporated_date": ch_incorporated,
        "ch_sic_codes": ch_sic_codes,
        "ch_match_confidence": match_confidence,
        "filing_type": filing_type,
        "error": None,
        "ch_pdf_path": saved_pdf_path,
        **registry_intel,
    }

    if not all_financials:
        result["notes"] = "Accounts filings exist but could not download/parse PDFs"
        return result

    # Map parsed financials into y1 (most recent), y2, y3
    # Each parsed filing may have current + prior year data
    year_data = []  # List of (period_end_date, {revenue, profit, assets, ...})

    for parsed in all_financials:
        # Current year from this filing
        period_end = parsed.get("period_end_current")
        if period_end:
            year_data.append((period_end, {
                "revenue": parsed.get("revenue_current"),
                "profit": parsed.get("profit_current"),
                "total_assets": parsed.get("total_assets_current"),
                "net_assets": parsed.get("net_assets_current"),
                "cash": parsed.get("cash_current"),
                "gross_profit": parsed.get("gross_profit_current"),
                "employees": parsed.get("employees"),
            }))

        # Prior year from this filing (comparative figures)
        period_end_prior = parsed.get("period_end_prior")
        if period_end_prior:
            year_data.append((period_end_prior, {
                "revenue": parsed.get("revenue_prior"),
                "profit": parsed.get("profit_prior"),
                "total_assets": parsed.get("total_assets_prior"),
                "net_assets": parsed.get("net_assets_prior"),
                "cash": parsed.get("cash_prior"),
                "gross_profit": parsed.get("gross_profit_prior"),
                "employees": parsed.get("employees_prior"),
            }))

    # Deduplicate by period end date (prefer the current-year extraction)
    seen_dates = {}
    for date, data in year_data:
        if date and date not in seen_dates:
            seen_dates[date] = data

    # Sort by date descending (most recent first)
    sorted_years = sorted(seen_dates.items(), key=lambda x: x[0], reverse=True)

    # Map to y1 (most recent), y2, y3
    for i, (date, data) in enumerate(sorted_years[:3]):
        suffix = f"y{i+1}"
        result[f"revenue_{suffix}"] = data.get("revenue")
        result[f"revenue_{suffix}_date"] = date
        result[f"profit_{suffix}"] = data.get("profit")
        result[f"gross_profit_{suffix}"] = data.get("gross_profit")
        if i == 0:
            result["profit_y1_date"] = date
            result["total_assets_y1"] = data.get("total_assets")
            result["net_assets_y1"] = data.get("net_assets")
            result["cash_y1"] = data.get("cash")

    # Employees from most recent filing
    if all_financials:
        result["employees_ch"] = all_financials[0].get("employees")

    # ── Full multi-year history (v5): EVERY period we parsed, not just 3.
    # Feeds the profile's revenue chart, employee-development chart and the
    # multi-year P&L table. Stored as JSON on the row (ch_history).
    history = []
    for date, data in sorted_years[:6]:
        entry = {"period_end": date}
        for k in ("revenue", "gross_profit", "profit", "total_assets", "net_assets", "cash", "employees"):
            if data.get(k) is not None:
                entry[k] = data[k]
        if len(entry) > 1:
            history.append(entry)
    if history:
        result["ch_history"] = json.dumps({"v": 1, "years": history})

    # Filing type from most recent
    if all_financials and all_financials[0].get("filing_type"):
        result["filing_type"] = all_financials[0]["filing_type"]

    # Notes
    notes_parts = []
    if all_financials[0].get("notes"):
        notes_parts.append(all_financials[0]["notes"])
    notes_parts.append(f"Extracted from {len(all_financials)} filing PDF(s), covering {len(sorted_years)} financial periods")
    result["notes"] = ". ".join(notes_parts)

    logger.info(f"[CH] Complete for '{company_name}': {len(sorted_years)} years of data extracted from actual filings")
    return result


# ═══ v4 additions: distress flags, filing intelligence, cap table, watch ═════

def get_company_health(company_number: str) -> Dict:
    """
    Distress & hygiene flags from the profile + insolvency endpoint (free).
    Returns {ch_accounts_overdue: bool, ch_insolvency_summary: str}.
    An active gazette/strike-off or insolvency case is a hard red flag.
    """
    out = {"ch_accounts_overdue": False, "ch_insolvency_summary": ""}
    profile = _get_company_profile(company_number) or {}
    accounts = profile.get("accounts", {}) or {}
    out["ch_accounts_overdue"] = bool(accounts.get("overdue")) or bool(
        (accounts.get("next_accounts") or {}).get("overdue"))

    flags = []
    status = (profile.get("company_status") or "").lower()
    detail = (profile.get("company_status_detail") or "").lower()
    if status and status != "active":
        flags.append(f"company status: {status}")
    if detail:
        flags.append(detail)
    if profile.get("has_insolvency_history"):
        try:
            auth = _ch_auth()
            resp = requests.get(f"{CH_API_BASE}/company/{company_number}/insolvency",
                                auth=auth, timeout=10)
            if resp.status_code == 200:
                cases = (resp.json() or {}).get("cases", [])
                for c in cases[:3]:
                    ctype = (c.get("type") or "insolvency").replace("-", " ")
                    dates = c.get("dates", [])
                    d = dates[0].get("date") if dates else ""
                    flags.append(f"{ctype}{f' ({d})' if d else ''}")
            else:
                flags.append("insolvency history on record")
        except Exception as e:
            logger.warning(f"[CH] insolvency fetch failed for {company_number}: {e}")
            flags.append("insolvency history on record")
    out["ch_insolvency_summary"] = "; ".join(flags)
    return out


def _fetch_filing_history(company_number: str, category: str = "", items: int = 60) -> List[dict]:
    try:
        auth = _ch_auth()
        params = {"items_per_page": items}
        if category:
            params["category"] = category
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/filing-history",
                            auth=auth, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        return (resp.json() or {}).get("items", [])
    except Exception as e:
        logger.warning(f"[CH] filing history fetch failed for {company_number}: {e}")
        return []


_REGIME_KEYWORDS = [
    ("micro-entity", "micro-entity"), ("micro entity", "micro-entity"),
    ("total exemption full", "small (total exemption)"),
    ("total exemption small", "small (total exemption)"),
    ("abridged", "abridged"), ("dormant", "dormant"),
    ("small", "small"), ("medium", "medium"), ("group", "full (group)"), ("full", "full"),
]


def _accounts_regime(description: str) -> str:
    d = (description or "").lower().replace("-", " ")
    for kw, label in _REGIME_KEYWORDS:
        if kw in d:
            return label
    return ""


def get_filing_intel(company_number: str) -> Dict:
    """
    Filing-history intelligence (free metadata, no documents):
      ch_last_resolution   — most recent resolution/articles filing (funding-round radar)
      ch_accounts_regime   — accounts regime trajectory, e.g. "micro-entity -> small
                             (2024): crossed a size threshold" — a growth signal
                             companies never disclose directly.
    """
    out = {"ch_last_resolution": "", "ch_accounts_regime": ""}

    res_items = _fetch_filing_history(company_number, category="resolution", items=5)
    if not res_items:
        # new articles arrive under 'incorporation'/'constitution' categories on some records
        res_items = [i for i in _fetch_filing_history(company_number, items=40)
                     if (i.get("category") in ("resolution", "constitution"))
                     or "articles" in (i.get("description") or "")]
    if res_items:
        top = res_items[0]
        desc = (top.get("description") or "resolution").replace("-", " ")
        out["ch_last_resolution"] = f"{top.get('date', '')}: {desc}"[:180]

    acc_items = [i for i in _fetch_filing_history(company_number, category="accounts", items=12)]
    regimes = []  # oldest → newest (history is newest-first)
    for item in reversed(acc_items):
        label = _accounts_regime(item.get("description", ""))
        year = (item.get("date") or "")[:4]
        if label:
            if not regimes or regimes[-1][0] != label:
                regimes.append((label, year))
    if regimes:
        traj = " -> ".join(f"{l} ({y})" for l, y in regimes[-3:])
        out["ch_accounts_regime"] = traj
        if len(regimes) >= 2:
            order = ["dormant", "micro-entity", "small (total exemption)", "abridged", "small", "medium", "full", "full (group)"]
            try:
                if order.index(regimes[-1][0]) > order.index(regimes[-2][0]):
                    out["ch_accounts_regime"] += " — crossed a size threshold (growth signal)"
            except ValueError:
                pass
    return out


def _parse_cs01_pdf(pdf: bytes) -> Dict:
    """One CS01 PDF -> shareholder + share-class data (v3 prompt). {} on failure."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {}
    try:
        from google import genai
        from google.genai.types import Part
        client = genai.Client(api_key=api_key)
        prompt = """This is a UK Companies House confirmation statement (CS01) PDF. Extract the
shareholder information AND the statement of capital (share classes with their
prescribed particulars of rights) if present.

Return ONLY valid JSON:
{"has_shareholders": true/false,
 "statement_date": "YYYY-MM-DD",
 "shareholders": [{"name": "...", "shares": number, "share_class": "..."}],
 "share_classes": [{"class": "...", "total_shares": number or null,
                    "nominal_value": "e.g. GBP 0.001 or null",
                    "voting": "one line on voting rights, or null",
                    "dividend": "one line on dividend rights, or null",
                    "capital": "one line on capital/liquidation rights, or null"}],
 "treasury_shares": number or null,
 "notes": "one line, e.g. share class nuances"}

Rules:
- List EVERY shareholder line in the document, even tiny holdings — do not
  truncate, summarise or skip any. A holder appearing under several share
  classes gets one entry per class.
- shares = the exact number held as stated. Never invent or estimate.
- share_classes: copy the prescribed particulars of rights VERBATIM-condensed
  from the statement of capital section (voting / dividend / capital rights).
  If the document states the rights, summarise each in one line; if it does
  not, use null. NEVER invent rights.
- Do NOT compute percentages — the caller does that.
- If the statement contains no shareholder details, has_shareholders = false."""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[Part.from_bytes(data=pdf, mime_type="application/pdf"), prompt])
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1])
    except Exception as e:
        logger.warning(f"[CH] CS01 parse failed: {e}")
        return {}


def _psc_cross_check(founder_pct, psc_summary: str) -> str:
    """Independent sanity check: computed founder % vs the PSC control band."""
    if founder_pct is None:
        return ""
    bands = _re.findall(r"\(individual,\s*(\d+)-(\d+)%\)", psc_summary or "")
    if not bands:
        return "no individual PSC to compare"
    lo, hi = max(((int(a), int(b)) for a, b in bands), key=lambda x: x[0])
    if lo - 3 <= float(founder_pct) <= hi + 3:
        return "consistent with PSC register"
    return f"VERIFY: cap table says {founder_pct}% but PSC register band is {lo}-{hi}%"


def get_cap_table(company_number: str, company_name: str = "", stored_date: str = "",
                  psc_summary: str = "") -> Dict:
    """
    Cap table v3 from confirmation statements (CS01), Document API PDF ->
    Gemini parse (ungrounded), percentages computed in code.

    v3 upgrades:
      - WALK-BACK: if the newest CS01 carries no shareholder list (companies
        only file a full list periodically), step back through prior CS01s
        until one does (max 4 document parses).
      - SH01 ROLL-FORWARD: allotments filed AFTER the base statement dilute
        the table; allottee names + share counts are applied and percentages
        recomputed, labelled estimated.
      - SHARE-CLASS RIGHTS: voting / dividend / capital particulars per class.
      - PSC CROSS-CHECK: computed founder % validated against the PSC band.

    Returns {ch_cap_table: json-string, ch_cap_table_date, ch_founder_pct} or {}.
    """
    items = _fetch_filing_history(company_number, category="confirmation-statement", items=12)
    if not items:
        return {}
    if stored_date and (items[0].get("date") or "") <= stored_date:
        return {}  # nothing newer than what we already parsed

    # Prefer "with updates" statements, but WALK BACK until a full list appears
    ordered = sorted(items, key=lambda i: i.get("date") or "", reverse=True)
    ordered.sort(key=lambda i: 0 if ("with-updates" in (i.get("type") or "")
                                     or "with updates" in (i.get("description") or "")) else 1)
    ordered.sort(key=lambda i: i.get("date") or "", reverse=True)

    data, target = {}, None
    parses = 0
    for cand in ordered:
        if parses >= 4:
            break
        pdf = _download_accounts_pdf(cand)
        if not pdf:
            continue
        parses += 1
        parsed = _parse_cs01_pdf(pdf)
        if parsed.get("has_shareholders"):
            data, target = parsed, cand
            break
    if not data:
        return {}
    try:

        # Aggregate per HOLDER across share classes; percentages computed here
        # in code on one consistent basis (total of all listed shares), so the
        # table always sums to ~100%.
        raw = [h for h in data.get("shareholders", []) if h.get("name") and (h.get("shares") or 0) > 0]
        agg: Dict[str, dict] = {}
        for h in raw:
            key = _re.sub(r"[^a-z0-9]", "", h["name"].lower())
            entry = agg.setdefault(key, {"name": h["name"].strip().title(), "shares": 0, "classes": []})
            entry["shares"] += int(h["shares"])
            cls = (h.get("share_class") or "").strip().title()
            if cls and cls not in entry["classes"]:
                entry["classes"].append(cls)
        total = sum(e["shares"] for e in agg.values())
        if total <= 0:
            return {}
        base_date = data.get("statement_date") or target.get("date", "")

        # ── SH01 ROLL-FORWARD: apply allotments filed AFTER the base statement.
        # New shares dilute everyone; allottees are added/topped-up by name.
        # Percentages are then recomputed on the enlarged total.
        sh01_applied = []
        try:
            cap_filings = [f for f in _fetch_filing_history(company_number, category="capital", items=10)
                           if (f.get("date") or "") > base_date
                           and ("allotment" in (f.get("description") or "")
                                or (f.get("type") or "").upper().startswith("SH01"))]
            for filing in sorted(cap_filings, key=lambda f: f.get("date") or "")[:3]:
                pdf2 = _download_accounts_pdf(filing)
                if not pdf2:
                    continue
                parsed2 = _parse_sh01_pdf(pdf2, company_name)
                allots = [a for a in (parsed2.get("allottees") or [])
                          if isinstance(a, dict) and (a.get("name") or "").strip() and (a.get("shares") or 0) > 0]
                if not allots:
                    continue
                for a in allots:
                    key = _re.sub(r"[^a-z0-9]", "", a["name"].lower())
                    entry = agg.setdefault(key, {"name": a["name"].strip().title(), "shares": 0, "classes": []})
                    entry["shares"] += int(a["shares"])
                    total += int(a["shares"])
                sh01_applied.append(filing.get("date", ""))
        except Exception as e:
            logger.warning(f"[CH] SH01 roll-forward failed for {company_number} (base table kept): {e}")

        holders_all = sorted(agg.values(), key=lambda e: -e["shares"])
        top = holders_all[:15]
        rest = holders_all[15:]
        rows = [{"name": e["name"], "shares": e["shares"],
                 "share_class": " + ".join(e["classes"][:3]),
                 "pct": round(e["shares"] / total * 100, 1)} for e in top]
        if rest:
            rest_shares = sum(e["shares"] for e in rest)
            rows.append({"name": f"Others ({len(rest)} holders)", "shares": rest_shares,
                         "share_class": "", "pct": round(rest_shares / total * 100, 1)})

        # Largest individual (non-corporate) holder as founder proxy —
        # international corporate suffixes included
        _CORP = (" ltd", " limited", " llp", " lp", " llc", " plc", " inc", " gmbh", " bv",
                 " sa", " spa", " ab", " abp", " oy", " as", " aps", " scsp", " gp",
                 " ehf", " hf", " slhf", " ucits", "cooperatief", " ua", "nominees",
                 "fund", "capital", "ventures", "partners", "holdings", "trust",
                 "investments", "university", "chancellor", "college", "enterprises",
                 "stiftelsen", "striftelsen", "foundation", "bank", "sarl")
        founder_pct = None
        for r in rows:
            # Dots stripped so "Addition Three, L.P." matches " lp" — that
            # dotted suffix once crowned a FUND as the founder (PQShield).
            n = " " + (r.get("name") or "").lower().replace(".", "")
            if not r["name"].startswith("Others") and not any(t in n for t in _CORP):
                founder_pct = r["pct"]
                break

        notes = data.get("notes", "")
        if data.get("treasury_shares"):
            notes = (notes + " " if notes else "") + f"Treasury shares: {data['treasury_shares']:,} (excluded from percentages)."

        # Share classes with their prescribed particulars (economic/political rights)
        share_classes = []
        for sc in (data.get("share_classes") or [])[:8]:
            if isinstance(sc, dict) and (sc.get("class") or "").strip():
                share_classes.append({
                    "class": str(sc.get("class"))[:60],
                    "total_shares": sc.get("total_shares"),
                    "nominal_value": (sc.get("nominal_value") or "")[:40] or None,
                    "voting": (sc.get("voting") or "")[:180] or None,
                    "dividend": (sc.get("dividend") or "")[:180] or None,
                    "capital": (sc.get("capital") or "")[:180] or None,
                })

        effective_date = sh01_applied[-1] if sh01_applied else base_date
        return {
            "ch_cap_table": json.dumps({
                "v": 3,
                "fp": 2,  # founder-proxy rules v2 (dotted suffixes); forces one re-derive on older v3 tables
                "date": effective_date,
                "base_date": base_date,
                "rolled_forward": bool(sh01_applied),
                "sh01_applied": sh01_applied,
                "estimated": bool(sh01_applied),
                "total_shares": total,
                "shareholders": rows,
                "share_classes": share_classes,
                "psc_check": _psc_cross_check(founder_pct, psc_summary),
                "notes": notes,
            }),
            "ch_cap_table_date": effective_date,
            "ch_founder_pct": founder_pct,
        }
    except Exception as e:
        logger.warning(f"[CH] cap table parse failed for {company_number}: {e}")
        return {}


def get_filings_since(company_number: str, since_date: str) -> List[dict]:
    """Filing items strictly newer than since_date (YYYY-MM-DD) — for the watch job."""
    out = []
    for item in _fetch_filing_history(company_number, items=25):
        if (item.get("date") or "") > since_date:
            out.append({"date": item.get("date", ""), "category": item.get("category", ""),
                        "type": item.get("type", ""), "description": (item.get("description") or "").replace("-", " ")})
    return out


# ── CH max-extraction v6: SH01 allottees + officer networks ─────────────────
# Two more investor-discovery layers from the registry. SH01 documents name
# the allottees of newly issued shares (the newest investors, fresher than
# the last CS01); officer appointment graphs reveal fund partners and serial
# angels sitting on our targets' boards.

def _parse_sh01_pdf(pdf: bytes, company_name: str) -> Dict:
    """One SH01 PDF -> {"allottees": [{"name","shares"}], "names_listed": bool}. {} on failure."""
    import base64
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return {}
    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        pdf_b64 = base64.b64encode(pdf).decode()
        prompt = f"""This is a UK Companies House SH01 (return of allotment of shares) or similar
capital filing for {company_name}. Extract the names of the allottees (the people or
entities the new shares were allotted to) if the document lists them. Many SH01 forms
do NOT name allottees - in that case return an empty list. NEVER guess names.

Return ONLY valid JSON:
{{"allottees": [{{"name": "...", "shares": null or number}}], "names_listed": true or false}}"""
        response = model.generate_content(
            [{"mime_type": "application/pdf", "data": pdf_b64}, prompt],
            generation_config={"response_mime_type": "application/json"},
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as e:
        logger.warning(f"[CH] SH01 parse failed for {company_name}: {e}")
        return {}


def get_sh01_allottees(company_number: str, company_name: str, stored_date: str = "") -> Dict:
    """
    Parse the most recent SH01 (share allotment) document for allottee names.
    Skips work when the newest allotment filing is not newer than stored_date.
    Returns {"date": ..., "allottees": [{"name", "shares"}], "skipped": bool}.
    """
    import base64
    filings = _fetch_filing_history(company_number, category="capital", items=10)
    target = None
    for f in filings:
        if "allotment" in (f.get("description") or "") or (f.get("type") or "").upper().startswith("SH01"):
            target = f
            break
    if not target:
        return {"date": "", "allottees": [], "skipped": True}
    fdate = target.get("date", "")
    if stored_date and fdate and fdate <= stored_date[:10]:
        return {"date": fdate, "allottees": [], "skipped": True}

    pdf = _download_accounts_pdf(target)  # generic filing-document downloader
    if not pdf:
        return {"date": fdate, "allottees": [], "skipped": False}

    result = _parse_sh01_pdf(pdf, company_name)
    allottees = [a for a in (result.get("allottees") or [])
                 if isinstance(a, dict) and (a.get("name") or "").strip()]
    logger.info(f"[CH] SH01 {fdate} for '{company_name}': {len(allottees)} allottee(s) named")
    return {"date": fdate, "allottees": allottees[:12], "skipped": False}


def get_officer_network(company_number: str, exclude_names: List[str] = None,
                        max_officers: int = 4, min_other_seats: int = 3) -> Dict:
    """
    Officer appointment graph: for each active non-founder director, list their
    OTHER active board seats. A director with several other seats is very
    likely a fund partner or serial angel - identifying both the investor and
    their portfolio. Cost: 1 call for officers + 1 per director (bounded).
    Returns {"checked_at", "officers": [{"name","other_seats","companies":[...]}]}.
    """
    from datetime import date as _date
    auth = _ch_auth()
    exclude_low = {(_n or "").strip().lower() for _n in (exclude_names or [])}

    try:
        resp = requests.get(f"{CH_API_BASE}/company/{company_number}/officers",
                            params={"items_per_page": 20}, auth=auth, timeout=15)
        if resp.status_code != 200:
            return {"checked_at": str(_date.today()), "officers": []}
        items = resp.json().get("items", [])
    except Exception as e:
        logger.warning(f"[CH] officer network: officers fetch failed for {company_number}: {e}")
        return {"checked_at": str(_date.today()), "officers": []}

    results = []
    checked = 0
    for o in items:
        if checked >= max_officers:
            break
        if o.get("resigned_on") or "director" not in (o.get("officer_role") or ""):
            continue
        name = o.get("name", "")
        # CH officer names come as "SURNAME, First" - normalise for exclusion match
        flat = " ".join(reversed([p.strip() for p in name.split(",")])).strip().lower()
        if flat in exclude_low or name.strip().lower() in exclude_low:
            continue
        appt_link = ((o.get("links") or {}).get("officer") or {}).get("appointments", "")
        if not appt_link:
            continue
        checked += 1
        try:
            aresp = requests.get(f"{CH_API_BASE}{appt_link}",
                                 params={"items_per_page": 30}, auth=auth, timeout=15)
            if aresp.status_code != 200:
                continue
            appts = aresp.json().get("items", [])
        except Exception:
            continue
        others = []
        for a in appts:
            if a.get("resigned_on"):
                continue
            comp = (a.get("appointed_to") or {}).get("company_name", "")
            comp_no = (a.get("appointed_to") or {}).get("company_number", "")
            if comp_no == company_number or not comp:
                continue
            if comp not in others:
                others.append(comp)
        if len(others) >= min_other_seats:
            results.append({"name": name, "other_seats": len(others), "companies": others[:8]})

    return {"checked_at": str(_date.today()), "officers": results}
