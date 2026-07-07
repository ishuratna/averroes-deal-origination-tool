"""
PitchBook Limited Partner (LP) export parser.

Built against the actual "All Columns" PitchBook LP export format
(152 columns, preamble rows, header on the row containing 'Limited Partner ID').
Figures are in the PitchBook account currency — USD millions for this account.

RAW parse + local derivations only, no AI. InvestorFill enriches later.

Derived at upload:
  - strategy_preferences: PE-relevant strategies only (Buyout, Growth, FoF, ...)
  - geo_preferences: condensed to UK/Europe/Middle East mandate hits
  - ticket_min/max from 'Preferred Commitment Size' ("25.88 - 43.14")
  - canonical investor_type mapping
"""
import io
import re
import logging
from typing import List, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


# normalized header → internal field
COLUMN_MAP = {
    "limitedpartnerid": "pb_id",
    "limitedpartners": "name",
    "limitedpartneralsoknownas": "aka",
    "registrationnumber": "registration_number",
    "description": "description",
    "limitedpartnertype": "investor_type_raw",
    "aum": "aum_m",
    "yearfounded": "year_founded",
    "hqcity": "hq_city",
    "hqcountryterritoryregion": "hq_country",
    "hqglobalregion": "global_region",
    "hqemail": "hq_email",
    "primarycontact": "contact_name",
    "primarycontacttitle": "contact_title",
    "primarycontactemail": "contact_email",
    "primarycontactphone": "contact_phone",
    "website": "website",
    "opentofirsttimefunds": "open_to_first_time",
    "preferredcommitmentsize": "commitment_size_raw",
    "preferredgeography": "geo_raw",
    "fundstrategypreferences": "strategy_raw",
    "otherinvestmentpreferences": "other_preferences",
    "commitments": "num_commitments",
    "activecommitments": "num_active_commitments",
    "commitmentsinpefunds": "num_pe_commitments",
    "totalcommitments": "total_commitments_m",
    "lastupdateddate": "pb_last_updated",
    # generic-template fallbacks (other export shapes)
    "investorname": "name",
    "investors": "name",
    "primaryinvestortype": "investor_type_raw",
    "hqcountry": "hq_country",
    "hqlocation": "hq_location",
}

TYPE_MAP = {
    "high-net-worth investor": "HNWI",
    "high net worth": "HNWI",
    "family office (single)": "Family Office",
    "family office (multi)": "Family Office",
    "family office": "Family Office",
    "fund of funds": "Fund of Funds",
    "corporation": "Corporate",
    "banking institution": "Sovereign/Institutional",
    "pension": "Sovereign/Institutional",
    "endowment": "Sovereign/Institutional",
    "foundation": "Sovereign/Institutional",
    "insurance": "Sovereign/Institutional",
    "sovereign wealth fund": "Sovereign/Institutional",
    "government": "Sovereign/Institutional",
    "money management firm": "Sovereign/Institutional",
    "private investment fund": "PE",
    "venture capital": "VC",
    "pe/buyout": "PE",
    "angel": "Angel",
}

# Strategies relevant to the Averroes mandate — kept in condensed field
RELEVANT_STRATEGIES = [
    "Buyout", "Growth/Expansion", "Diversified Private Equity",
    "Co-Investment", "Fund of Funds", "Secondaries", "Mezzanine",
]

# Geography tokens we care about (UK/IE, Europe, Middle East/GCC)
RELEVANT_GEOS = [
    "United Kingdom", "Ireland", "Europe", "Western Europe", "Northern Europe",
    "Middle East", "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait",
    "Bahrain", "Oman",
]


def _clean_str(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "n/a", "-") else s


def _num(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _int(v) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def _parse_ticket(raw: str):
    """'25.88 - 43.14' → (25.88, 43.14); single values → (v, v)."""
    raw = _clean_str(raw)
    if not raw:
        return None, None
    nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", raw)
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    if len(nums) == 1:
        return float(nums[0]), float(nums[0])
    return None, None


def _condense_strategies(raw: str) -> str:
    """Keep only Averroes-relevant strategies from PitchBook's long list."""
    raw = _clean_str(raw)
    if not raw:
        return ""
    found = [s for s in RELEVANT_STRATEGIES if s.lower() in raw.lower()]
    if found:
        return ", ".join(found)
    return "None relevant"  # they have preferences but not ours — a real negative signal


def _condense_geo(raw: str) -> str:
    """Reduce PitchBook's (sometimes 200-territory) list to our mandate hits."""
    raw = _clean_str(raw)
    if not raw:
        return ""
    hits = [g for g in RELEVANT_GEOS if g.lower() in raw.lower()]
    # A mandate listing 100+ territories is effectively global
    is_global = raw.count(",") > 80
    if hits:
        label = ", ".join(hits[:6])
        return f"Global incl. {label}" if is_global else label
    return "Global" if is_global else "Outside mandate"


def _map_type(raw: str) -> str:
    r = _clean_str(raw).lower()
    if not r:
        return "Unknown"
    for key, canonical in TYPE_MAP.items():
        if key in r:
            return canonical
    return _clean_str(raw).title() if len(r) < 40 else "Unknown"


def parse_investor_file(file_content: bytes, filename: str = "") -> List[Dict]:
    """Parse a PitchBook LP export (xlsx/csv) into investor dicts."""
    is_csv = filename.lower().endswith(".csv")
    raw = pd.read_csv(io.BytesIO(file_content), header=None) if is_csv \
        else pd.read_excel(io.BytesIO(file_content), header=None)

    # Find the header row: contains 'Limited Partner ID' / 'Limited Partners' / a name column
    header_idx = None
    name_headers = {"limitedpartnerid", "limitedpartners", "investorname", "investors"}
    for i in range(min(15, len(raw))):
        row_norm = {_norm(x) for x in raw.iloc[i].tolist()}
        if row_norm & name_headers:
            header_idx = i
            break
    if header_idx is None:
        logger.error(f"No header row found in {filename} (searched first 15 rows)")
        return []

    df = raw.iloc[header_idx + 1:].reset_index(drop=True)
    df.columns = raw.iloc[header_idx].tolist()

    # Build column lookup
    colmap = {}
    for col in df.columns:
        field = COLUMN_MAP.get(_norm(col))
        if field and field not in colmap:
            colmap[field] = col

    if "name" not in colmap:
        logger.error(f"No investor-name column in {filename}")
        return []

    def get(row, field):
        col = colmap.get(field)
        return row.get(col) if col else None

    # Source = the filename (minus extension) so separate uploads stay distinguishable
    source_label = re.sub(r"\.(xlsx|xls|csv)$", "", filename, flags=re.IGNORECASE) or "PitchBook LP Upload"

    investors: List[Dict] = []
    seen = set()
    for _, row in df.iterrows():
        name = _clean_str(get(row, "name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        hq_city = _clean_str(get(row, "hq_city"))
        hq_country = _clean_str(get(row, "hq_country"))
        if not (hq_city or hq_country):
            loc = _clean_str(get(row, "hq_location"))
            if "," in loc:
                parts = [p.strip() for p in loc.split(",")]
                hq_city, hq_country = parts[0], parts[-1]
            else:
                hq_country = loc

        tmin, tmax = _parse_ticket(_clean_str(get(row, "commitment_size_raw")))
        pb_updated = _clean_str(get(row, "pb_last_updated"))
        if pb_updated.endswith(" 00:00:00"):
            pb_updated = pb_updated[:-9]

        investors.append({
            "name": name,
            "pb_id": _clean_str(get(row, "pb_id")),
            "aka": _clean_str(get(row, "aka")),
            "registration_number": _clean_str(get(row, "registration_number")),
            "investor_type": _map_type(_clean_str(get(row, "investor_type_raw"))),
            "aum_m": _num(get(row, "aum_m")),
            "ticket_min_m": tmin,
            "ticket_max_m": tmax,
            "year_founded": _int(get(row, "year_founded")),
            "hq_city": hq_city,
            "hq_country": hq_country,
            "region": hq_country,
            "global_region": _clean_str(get(row, "global_region")),
            "hq_email": _clean_str(get(row, "hq_email")),
            "website": _clean_str(get(row, "website")),
            "description": _clean_str(get(row, "description")),
            "contact_name": _clean_str(get(row, "contact_name")),
            "contact_title": _clean_str(get(row, "contact_title")),
            "contact_email": _clean_str(get(row, "contact_email")),
            "contact_phone": _clean_str(get(row, "contact_phone")),
            "open_to_first_time": _clean_str(get(row, "open_to_first_time")),
            "strategy_preferences": _condense_strategies(_clean_str(get(row, "strategy_raw"))),
            "geo_preferences": _condense_geo(_clean_str(get(row, "geo_raw"))),
            "other_preferences": _clean_str(get(row, "other_preferences"))[:500],
            "num_commitments": _int(get(row, "num_commitments")),
            "num_active_commitments": _int(get(row, "num_active_commitments")),
            "num_pe_commitments": _int(get(row, "num_pe_commitments")),
            "total_commitments_m": _num(get(row, "total_commitments_m")),
            "pb_last_updated": pb_updated,
            "source": source_label,
            "status": "Identified",
        })

    logger.info(f"Parsed {len(investors)} investors from {filename}")
    return investors
