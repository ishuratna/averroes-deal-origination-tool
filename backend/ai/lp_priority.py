"""
LP priority for a deal-by-deal CO-INVESTMENT raise (per Ishu, 9 Sep 2026).

The old lp_fit_score was built for a fund raise (geography, PE appetite,
ticket, tech affinity). Averroes is raising deal by deal at GBP 200K-10M per
LP, so "important" means: writes DIRECT cheques into growth software, at our
size, in our home geographies (UK/IE, GCC, then Europe), recently active, and
reachable warm. PURE function, zero AI: every input is already on the row
(PitchBook fields, InvestorFill results, network tags, portfolio overlap).

  score 0-100 = weighted fit (co-invest appetite 30, ticket 20, software
                affinity 15, home geography 15, recency 10, readiness 10)
              + warm-path boost (network tags, co-investor in our universe)
  tier  A  score >= 70 and contactable (a named principal with an address)
        B  score >= 45, or >= 70 but not yet contactable (InvestorFill it)
        C  the rest
        Parked  Passed / Talk Later, whatever the score

The details dict names every input and the rule applied, so the card can show
WHY (same principle as the company fit score's clickable evidence).
"""
import json
import re
from datetime import date
from typing import Dict, List, Optional

# THE CHEQUE BAND: GBP 200K to 10M per investor, per deal (Ishu, confirmed
# 11 Sep 2026), expressed in the USD millions PitchBook reports in.
#
# It lives in ai/investor_gate.py and is imported here, never copied. The gate
# uses it to EXCLUDE an investor whose stated ticket cannot overlap ours; this
# module uses it to RANK. When those two disagreed (the gate at 200K-10M while
# the ranking still ran on 250K-2M) we would have emailed the wrong people a
# correct number, so there is exactly one definition.
# The geography sets and the cheque band are defined in ai/investor_gate.py,
# which runs FIRST in the pipeline, and are imported here so the filter and the
# ranking can never disagree about what the GCC is or what cheque we write.
from ai.investor_gate import (  # noqa: F401
    EUROPE, GCC, TICKET_MAX_USD_M, TICKET_MIN_USD_M, UK_IE, _low,
)

DIRECT_CHEQUE_TYPES = {"family office", "hnwi", "uhnwi", "angel", "single family office", "multi-family office"}

WARM_TAGS = {"gcc": 15, "bea": 15, "partner": 15, "network": 12, "co-investor": 15, "warm": 12, "intro": 10}
WEIGHTS = {"coinvest": 0.30, "ticket": 0.20, "affinity": 0.15, "geography": 0.15, "recency": 0.10, "readiness": 0.10}


def parse_tags(s) -> List[str]:
    """'GCC, Bea' -> ['GCC', 'Bea']. Trimmed, deduplicated, order kept."""
    out, seen = [], set()
    for t in re.split(r"[,;|]", s or ""):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _region_score(inv: Dict) -> (float, str):
    blob = " ".join(_low(inv.get(k)) for k in ("hq_country", "hq_city", "region", "global_region", "geo_preferences"))
    if any(g in blob for g in GCC):
        return 1.0, "GCC home geography"
    if any(g in blob for g in UK_IE):
        return 1.0, "UK/IE home geography"
    if any(g in blob for g in EUROPE):
        return 0.7, "European"
    if not blob.strip():
        return 0.5, "geography unknown"
    return 0.3, f"outside home geographies ({(inv.get('hq_country') or inv.get('global_region') or '').strip()})"


def _coinvest_score(inv: Dict) -> (float, str):
    prefs = _low(inv.get("strategy_preferences")) + " " + _low(inv.get("other_preferences")) + " " + _low(inv.get("policy_description"))
    itype = _low(inv.get("investor_type"))
    if "co-invest" in prefs or "coinvest" in prefs or "direct" in prefs:
        return 1.0, "states co-investment / direct appetite"
    if inv.get("source_companies"):
        return 1.0, "already a direct investor in a company in our universe"
    if itype in DIRECT_CHEQUE_TYPES:
        return 0.8, f"{inv.get('investor_type')}: writes direct cheques by nature"
    # Type ceilings come BEFORE track record: a pension with 40 fund
    # commitments is still a pension for a GBP 200K-10M cheque.
    if "sovereign" in itype or "institutional" in itype or "bank" in itype or "pension" in itype or "insurance" in itype:
        return 0.2, "institutional: co-invests only at scale"
    if "fund of funds" in itype:
        return 0.4, "fund of funds: rarely co-invests at our size"
    if (inv.get("num_pe_commitments") or 0) > 0:
        return 0.6, f"{int(inv.get('num_pe_commitments'))} PE fund commitments (fund LP, not proven direct)"
    return 0.3, "no co-investment evidence yet"


def _ticket_score(inv: Dict) -> (float, str):
    lo, hi = inv.get("ticket_min_m"), inv.get("ticket_max_m")
    try:
        lo = float(lo) if lo is not None else None
        hi = float(hi) if hi is not None else None
    except (TypeError, ValueError):
        lo = hi = None
    if lo is not None or hi is not None:
        lo = lo if lo is not None else 0.0
        hi = hi if hi is not None else lo
        if hi >= TICKET_MIN_USD_M and lo <= TICKET_MAX_USD_M:
            return 1.0, f"stated ticket ${lo:g}-{hi:g}M overlaps our $0.3-2.6M"
        if lo <= TICKET_MAX_USD_M * 3 and hi >= TICKET_MIN_USD_M / 3:
            return 0.5, f"stated ticket ${lo:g}-{hi:g}M is adjacent to ours"
        return 0.15, f"stated ticket ${lo:g}-{hi:g}M is far from ours"
    for col, label in (("aum_m", "AUM"), ("net_assets_m", "net assets")):
        v = inv.get(col)
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v:
            if 20 <= v <= 3000:
                return 0.7, f"{label} ${v:,.0f}M implies our cheque size is natural"
            if v > 3000:
                return 0.4, f"{label} ${v:,.0f}M: our cheque is small for them"
            return 0.4, f"{label} ${v:,.0f}M: our cheque may be large for them"
    return 0.5, "ticket unknown"


def _affinity_score(inv: Dict) -> (float, str):
    if inv.get("score_tech_affinity") is not None:
        try:
            v = float(inv["score_tech_affinity"])
            return v, f"InvestorFill tech affinity {v:.2f}"
        except (TypeError, ValueError):
            pass
    if inv.get("source_companies"):
        return 1.0, "holds companies in our software universe"
    prefs = _low(inv.get("strategy_preferences"))
    if "growth" in prefs or "expansion" in prefs or "venture" in prefs or (inv.get("num_vc_commitments") or 0) > 0:
        return 0.6, "growth / venture appetite stated"
    return 0.3, "no software or growth evidence yet"


def _recency_score(inv: Dict, today: Optional[date]) -> (float, str):
    today = today or date.today()
    for col, label in (("last_commitment_date", "last commitment"), ("pb_last_updated", "PitchBook profile updated"),
                       ("updated_at", "record updated")):
        s = _low(inv.get(col))
        m = re.match(r"(\d{4})", s)
        if m:
            yrs = today.year - int(m.group(1))
            if yrs <= 1:
                return 1.0, f"{label} within a year"
            if yrs <= 3:
                return 0.6, f"{label} {yrs} years ago"
            return 0.3, f"{label} {yrs} years ago"
    return 0.5, "no activity date"


def _readiness(inv: Dict) -> (float, str, bool):
    email = _low(inv.get("contact_email"))
    name = (inv.get("contact_name") or "").strip()
    if email and "@" in email:
        return 1.0, f"contactable: {name or 'address only'}", True
    if name:
        return 0.5, f"principal named ({name}), no address yet", False
    return 0.0, "no principal identified", False


def lp_priority(inv: Dict, today: Optional[date] = None) -> Dict:
    """Score, tier, and the reasons. Pure."""
    parts = {}
    parts["coinvest"] = _coinvest_score(inv)
    parts["ticket"] = _ticket_score(inv)
    parts["affinity"] = _affinity_score(inv)
    parts["geography"] = _region_score(inv)
    parts["recency"] = _recency_score(inv, today)
    r_score, r_note, contactable = _readiness(inv)
    parts["readiness"] = (r_score, r_note)

    fit = sum(WEIGHTS[k] * float(v[0]) for k, v in parts.items())
    boost, warm = 0, []
    for t in parse_tags(inv.get("network_tags")):
        w = WARM_TAGS.get(t.lower(), 8)
        boost += w
        warm.append(f"{t} (+{w})")
    if inv.get("source_companies"):
        boost += 10
        warm.append("co-investor in our universe (+10)")
    boost = min(boost, 30)
    score = round(min(100.0, fit * 100 + boost), 1)

    status = inv.get("status") or ""
    if status in ("Passed", "Talk Later"):
        tier = "Parked"
    elif score >= 70 and contactable:
        tier = "A"
    elif score >= 45 or score >= 70:
        tier = "B"
    else:
        tier = "C"
    details = {k: {"score": round(float(v[0]), 2), "weight": WEIGHTS[k], "why": v[1]} for k, v in parts.items()}
    details["warm_boost"] = {"points": boost, "why": warm or ["no warm path recorded"]}
    details["contactable"] = contactable
    return {"score": score, "tier": tier, "contactable": contactable, "details": details}


def priority_json(result: Dict) -> str:
    return json.dumps(result["details"], default=str)
