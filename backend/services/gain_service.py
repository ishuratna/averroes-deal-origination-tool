"""
Gain.pro (gain.ai) company export parser — detected by "gain" in the filename,
mirroring the PitchBook/Inven rule.

Format: xlsx with 'Key Data' (curated columns) + 'All Data' (full API fields,
same rows, joined by company name). Financial figures are in MILLIONS.
Percentage columns are percentage points already.

FX handling (everything lands in GBP, the master-universe convention):
  • Currency == GBP  -> local values used directly, zero FX error.
  • Anything else    -> Gain's own EUR-converted value x EUR_GBP_RATE
                        (env-overridable, default 0.85 — late-July 2026 spot).
  • The conversion applied is noted per row in financing_note.

Data-integrity rules (doctrine):
  • REPORTED revenue (Revenue AI-Est. = False) -> revenue_y1 (raw GBP,
    Companies House field convention, like the Inven parser).
  • Gain AI-ESTIMATED revenue -> revenue_estimate_m only, clearly labelled.
    Vendor estimates never masquerade as filings.
  • Same split for EBITDA: reported -> estimated_ebitda (GBP M);
    AI-estimated -> extra_data.
  • Everything unmapped but useful lands in extra_data JSON (fill-only field).
"""
import io
import json
import logging
import os
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

EUR_GBP_RATE = float(os.getenv("EUR_GBP_RATE", "0.85"))

_OWNERSHIP_LABELS = {
    "privateIndividual": "Founder-owned (private individuals)",
    "subsidiary": "Subsidiary",
    "ventureCapitalBacked": "VC-backed",
    "privateEquityMajority": "PE-backed (majority)",
    "privateEquityMinority": "PE-backed (minority)",
    "governmentOrSemiPublic": "Government/Semi-public",
    "bankrupt": "Bankrupt/Insolvent",
    "other": "Other",
}

_MARKET_LABELS = {
    "notInMarket": "Not in market",
    "inMarketPreparation": "In market (preparation)",
    "inMarketLaunch": "In market (launch)",
    "inMarketNonBindingOffers": "In market (non-binding offers)",
    "inMarketUnknownStage": "In market",
}


def _f(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _s(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    out = str(v).strip()
    return "" if out.lower() in ("nan", "none") else out


def _gbp_m(local, eur, currency: str) -> Optional[float]:
    """Millions in GBP: local when reported in GBP, else Gain's EUR x rate."""
    if currency == "GBP" and local is not None:
        return local
    if eur is not None:
        return eur * EUR_GBP_RATE
    return None


def parse_gain_excel(file_content: bytes) -> List[Dict]:
    xl = pd.ExcelFile(io.BytesIO(file_content))
    if "Key Data" not in xl.sheet_names:
        raise ValueError("Not a Gain export: no 'Key Data' sheet found")
    kd = xl.parse("Key Data")

    # All Data extras, joined by lowercase name (AI-estimated figures,
    # aliases, investors JSON live only there). Optional: Key Data alone
    # still parses if the sheet is missing.
    extras: Dict[str, Dict] = {}
    if "All Data" in xl.sheet_names:
        want = ["name", "revenueWithAiGenerated", "revenueWithAiGeneratedEur",
                "revenueYear", "aliases", "investors", "gainProUrl",
                "ownershipIsVerified", "netDebt", "netDebtEur", "netDebtYear",
                "ebit", "ebitEur", "grossMarginPctRevenue", "ceoName"]
        try:
            ad = xl.parse("All Data")
            cols = [c for c in want if c in ad.columns]
            for _, r in ad[cols].iterrows():
                nm = _s(r.get("name")).lower()
                if nm:
                    extras[nm] = r.to_dict()
        except Exception as e:
            logger.warning(f"[Gain] All Data sheet unreadable, using Key Data only: {e}")

    targets: List[Dict] = []
    for _, row in kd.iterrows():
        name = _s(row.get("Company"))
        if not name:
            continue
        ex = extras.get(name.lower(), {})
        currency = _s(row.get("Currency")).upper()

        country = _s(row.get("HQ Country")).upper()
        region = "Ireland" if country == "IE" else "UK"

        website = _s(row.get("Website"))
        if website and not website.startswith("http"):
            website = f"https://{website}"

        # ── Revenue: reported vs Gain-AI-estimated, never mixed ──────────
        rev_is_ai = bool(row.get("Revenue AI-Est.")) if not pd.isna(row.get("Revenue AI-Est.")) else False
        rev_reported_m = _gbp_m(_f(row.get("Revenue (m, local)")), _f(row.get("Revenue (EURm)")), currency)
        rev_ai_m = _gbp_m(_f(ex.get("revenueWithAiGenerated")), _f(ex.get("revenueWithAiGeneratedEur")), currency)
        rev_year = _s(row.get("Revenue Year")).replace(".0", "") or _s(ex.get("revenueYear")).replace(".0", "")

        # ── EBITDA: same reported/estimated split ─────────────────────────
        ebitda_is_ai = bool(row.get("EBITDA AI-Est.")) if not pd.isna(row.get("EBITDA AI-Est.")) else False
        ebitda_m = _gbp_m(_f(row.get("EBITDA (m, local)")), _f(row.get("EBITDA (EURm)")), currency)

        ownership_raw = _s(row.get("Ownership Type"))
        ownership = _OWNERSHIP_LABELS.get(ownership_raw, ownership_raw)
        owners = _s(row.get("Owners"))
        majority = _s(row.get("Majority Owner"))
        current_owners = owners if not majority or majority in owners else \
            (f"{owners}; {majority} (majority)" if owners else f"{majority} (majority)")

        # Owners of VC/PE-backed companies ARE investors; plus the raw
        # investors field from All Data when present.
        investors_bits = []
        if ownership_raw in ("ventureCapitalBacked", "privateEquityMajority", "privateEquityMinority") and owners:
            investors_bits.append(owners)
        inv_json = _s(ex.get("investors"))
        if inv_json and inv_json not in investors_bits:
            try:
                parsed = json.loads(inv_json)
                names = [x.get("name") for x in parsed if isinstance(x, dict) and x.get("name")] \
                    if isinstance(parsed, list) else []
                if names:
                    investors_bits.append("; ".join(names))
            except Exception:
                pass
        investors_raw = "; ".join(investors_bits)[:2000]

        funding_m = _f(row.get("Total Funding (EURm)"))
        round_size_m = _f(row.get("Latest Round Size (EURm)"))
        round_year = _s(row.get("Latest Round Year")).replace(".0", "")

        # Unmapped-but-useful -> extra_data (fill-only column)
        extra = {k: v for k, v in {
            "gain_gross_margin_pct_rev": _f(row.get("Gross Margin (% Rev)")),
            "gain_ebit_gbp_m": _gbp_m(_f(row.get("EBIT (m, local)")), _f(row.get("EBIT (EURm)")), currency),
            "gain_net_debt_gbp_m": _gbp_m(_f(ex.get("netDebt") if ex else row.get("Net Debt (m, local)")),
                                          _f(ex.get("netDebtEur")), currency),
            "gain_net_debt_year": _s(row.get("Net Debt Year")).replace(".0", "") or None,
            "gain_ebitda_ai_est_gbp_m": round(ebitda_m, 2) if (ebitda_is_ai and ebitda_m is not None) else None,
            "gain_reporting_currency": currency or None,
            "gain_revenue_year": rev_year or None,
            "gain_fte_year": _s(row.get("FTE Year")).replace(".0", "") or None,
            "gain_sector": _s(row.get("Sector")) or None,
            "gain_url": _s(ex.get("gainProUrl")) or None,
            "gain_ownership_verified": bool(ex.get("ownershipIsVerified")) if ex.get("ownershipIsVerified") not in (None, "") else None,
            "gain_last_deal": f"{_s(row.get('Last Deal Year')).replace('.0','')}-{_s(row.get('Last Deal Month')).replace('.0','')}"
                              if _s(row.get("Last Deal Year")) else None,
        }.items() if v not in (None, "", "nan", "-")}

        fx_used = currency and currency != "GBP" and any(
            x is not None for x in (rev_reported_m if rev_is_ai is False else None, rev_ai_m, ebitda_m, funding_m))
        note_bits = []
        if fx_used or funding_m is not None or round_size_m is not None:
            note_bits.append(f"Non-GBP figures converted via Gain EUR values at EUR/GBP {EUR_GBP_RATE} (Gain export).")

        target = {
            "name": name,
            "description": _s(row.get("Business Description")) or _s(row.get("Short Description")),
            "website": website,
            "sector": (_s(row.get("Subsector")) or _s(row.get("Sector"))).title(),
            "keywords": _s(row.get("Tags"))[:1000],
            "region": region,
            "hq_country": country,
            "hq_city": _s(row.get("HQ City")),
            "hq_location": ", ".join(x for x in (_s(row.get("HQ City")), region) if x),
            "year_founded": int(_f(row.get("Founded"))) if _f(row.get("Founded")) else None,
            "employees": int(_f(row.get("FTEs"))) if _f(row.get("FTEs")) else None,
            "employee_growth_1yr_pct": _f(row.get("FTE Growth 1Y (%)")),
            "employee_growth_3yr_pct": _f(row.get("FTE CAGR 3Y (%)")),
            "ownership": ownership,
            "current_owners": current_owners[:2000],
            "investors_raw": investors_raw,
            "active_investors": investors_raw[:1000],
            "contact_name": _s(row.get("CEO")) or _s(ex.get("ceoName")),
            "contact_title": "CEO" if (_s(row.get("CEO")) or _s(ex.get("ceoName"))) else "",
            "also_known_as": _s(ex.get("aliases"))[:500],
            # Reported revenue -> CH-convention raw GBP (like Inven)
            "revenue_y1": round(rev_reported_m * 1e6, 0) if (not rev_is_ai and rev_reported_m is not None) else None,
            "revenue_y1_date": f"FY{rev_year} (Gain, reported)" if (not rev_is_ai and rev_reported_m is not None) else None,
            # Gain AI estimate -> the estimate column, clearly labelled
            "revenue_estimate_m": round(rev_ai_m, 2) if (rev_is_ai and rev_ai_m is not None) else None,
            "revenue_source": "Gain AI estimate" if (rev_is_ai and rev_ai_m is not None) else None,
            "revenue_confidence": "vendor estimate" if (rev_is_ai and rev_ai_m is not None) else None,
            "revenue_growth_pct": _f(row.get("Rev Growth 1Y (%)")),
            "revenue_cagr_3yr_pct": _f(row.get("Rev CAGR 3Y (%)")),
            "estimated_ebitda": round(ebitda_m, 2) if (not ebitda_is_ai and ebitda_m is not None) else None,
            "ebitda_margin_pct": _f(row.get("EBITDA (% Rev)")),
            "total_raised_m": round(funding_m * EUR_GBP_RATE, 2) if funding_m is not None else None,
            "last_financing_type": _s(row.get("Latest Round Type")),
            "last_financing_date": round_year,
            "last_financing_size_m": round(round_size_m * EUR_GBP_RATE, 2) if round_size_m is not None else None,
            "financing_status": _MARKET_LABELS.get(_s(row.get("Market Status")), _s(row.get("Market Status"))),
            "financing_note": " ".join(note_bits),
            "extra_data": json.dumps(extra) if extra else None,
        }
        targets.append({k: v for k, v in target.items() if v not in (None, "")})

    logger.info(f"[Gain] Parsed {len(targets)} companies (EUR/GBP rate {EUR_GBP_RATE})")
    return targets
