"""
Inven CSV parser — custom transformation for Inven.ai company exports
(detected by "inven" in the filename, mirroring the PitchBook rule).

Key handling:
  • Mixed currencies: revenue/EBITDA/net income arrive in USD, assets and
    operating profit in GBP. Everything is converted to GBP at ingest using
    USD_GBP_RATE (env-overridable) and the conversion is noted per row.
  • Revenue years: Latest -> revenue_y1, 2024 -> revenue_y2, 2023 -> revenue_y3
    (raw GBP, matching the Companies House field convention).
  • Growth metrics land in dedicated columns and feed the fit score locally
    (no web search needed for these companies).
"""
import csv
import io
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

USD_GBP_RATE = float(os.getenv("USD_GBP_RATE", "0.74"))

_OWNERSHIP_LABELS = {
    "private_unknown": "Private (ownership unverified)",
    "private_equity": "PE-backed",
    "venture_capital": "VC-backed",
    "family_owned": "Family/Founder-owned",
    "angel": "Angel-backed",
}


def _f(val: str) -> Optional[float]:
    try:
        v = float(str(val).replace(",", "").strip())
        return v
    except (ValueError, TypeError):
        return None


def _to_gbp(val: Optional[float], currency: str) -> Optional[float]:
    if val is None:
        return None
    cur = (currency or "").strip().upper()
    if cur == "USD":
        return val * USD_GBP_RATE
    return val  # GBP or unstated (Inven defaults to GBP entities)


def parse_inven_csv(file_content: bytes) -> List[Dict]:
    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    targets: List[Dict] = []
    converted_note = f"Financials converted USD->GBP at {USD_GBP_RATE} where sourced in USD (Inven export)."

    for row in reader:
        name = (row.get("Company") or "").strip()
        if not name:
            continue

        g = lambda k: (row.get(k) or "").strip()

        # ── Currency-aware financials (raw GBP for _y fields, £M for _m) ──
        rev_latest = _to_gbp(_f(g("Latest revenue")), g("Latest revenue (currency)"))
        rev_2024 = _to_gbp(_f(g("Revenue 2024")), g("Revenue 2024 (currency)"))
        rev_2023 = _to_gbp(_f(g("Revenue 2023")), g("Revenue 2023 (currency)"))
        ebitda_latest = _to_gbp(_f(g("Latest EBITDA")), g("Latest EBITDA (currency)"))
        net_income_2024 = _to_gbp(_f(g("Net income 2024")), g("Net income 2024 (currency)"))
        assets_2024 = _to_gbp(_f(g("Total assets 2024")), g("Total assets 2024 (currency)"))
        assets_2023 = _to_gbp(_f(g("Total assets 2023")), g("Total assets 2023 (currency)"))
        funding = _to_gbp(_f(g("Total funding")), g("Total funding (currency)"))

        country = g("Country").upper()
        region = "Ireland" if country == "IE" else "UK"

        website = g("Website")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        investors = g("Investors") or g("Current owners")

        target = {
            "name": name,
            "description": g("Description"),
            "website": website,
            "sector": g("Industry"),
            "region": region,
            "hq_country": country,
            "hq_city": g("City"),
            "hq_location": ", ".join(x for x in (g("City"), g("Region")) if x),
            "year_founded": int(_f(g("Founded"))) if _f(g("Founded")) else None,
            "employees": int(_f(g("Employees (LinkedIn)"))) if _f(g("Employees (LinkedIn)")) else None,
            "ownership": _OWNERSHIP_LABELS.get(g("Ownership type"), g("Ownership type") or ""),
            "active_investors": investors,
            # Financials — CH-convention raw GBP + PitchBook-convention £M
            "revenue_y1": round(rev_latest, 0) if rev_latest else None,
            "revenue_y1_date": "latest (Inven)",
            "revenue_y2": round(rev_2024, 0) if rev_2024 else None,
            "revenue_y2_date": "FY2024",
            "revenue_y3": round(rev_2023, 0) if rev_2023 else None,
            "revenue_y3_date": "FY2023",
            "estimated_ebitda": round(ebitda_latest / 1e6, 2) if ebitda_latest else None,
            "net_income_m": round(net_income_2024 / 1e6, 2) if net_income_2024 else None,
            "total_assets_y1": round(assets_2024 or assets_2023, 0) if (assets_2024 or assets_2023) else None,
            "total_raised_m": round(funding / 1e6, 2) if funding else None,
            # New Inven-specific fields
            "revenue_cagr_3yr_pct": _f(g("Revenue 3yr CAGR %")),
            "employee_growth_1yr_pct": _f(g("Employee growth 1yr %")),
            "employee_growth_3yr_pct": _f(g("Employee 3yr CAGR %")),
            "ebitda_margin_pct": _f(g("Latest EBITDA margin %")),
            "directors": g("Directors")[:2000],
            "company_linkedin": g("LinkedIn URL"),
            "financing_note": converted_note if (rev_latest or ebitda_latest) else "",
        }
        targets.append({k: v for k, v in target.items() if v not in (None, "")})

    logger.info(f"[Inven] Parsed {len(targets)} companies")
    return targets
