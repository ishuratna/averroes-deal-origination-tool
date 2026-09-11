"""
LP priority for a deal-by-deal CO-INVESTMENT raise (per Ishu, 9 Sep 2026).

The old lp_fit_score was built for a fund raise (geography, PE appetite,
ticket, tech affinity). Averroes is raising deal by deal at GBP 200K-10M per
LP, so "important" means: writes DIRECT cheques into growth software, at our
size, SMALL ENOUGH that our cheque matters to them, in our home geographies
(UK/IE, GCC, then Europe), recently active, and reachable warm. PURE function,
zero AI: every input is already on the row (PitchBook fields, InvestorFill
results, network tags, portfolio overlap).

  score 0-100 = weighted fit (co-invest appetite 25, ticket 20, size 15,
                home geography 15, software affinity 10, recency 7.5,
                readiness 7.5)
              + warm-path boost (network tags, co-investor in our universe)
              CAPPED AT 25 when the investor fails the gate's size check
  tier  A  score >= 70 and contactable (a named principal with an address)
        B  score >= 45, or >= 70 but not yet contactable (InvestorFill it)
        C  the rest, and EVERYTHING over the size ceiling
        Parked  Passed / Talk Later, whatever the score

SIZE IS TWO FACTS (Ishu, 11 Sep 2026: "the scoring is not following the
filters"). Mubadala sat at 98: GCC geography, a PitchBook profile updated last
month, a named contact and a GCC tag carried it there, while its
quarter-trillion book only ever entered the ranking as a soft 0.4 FALLBACK when
no ticket was stated. Two mistakes, both fixed here:

  1. The cheques they write are the PRIMARY size signal (`ticket`), scored by
     how much of THEIR stated range sits inside ours. "5M to 500M" overlaps
     our band and used to score 1.0; it is now 0.41, because a house that
     writes 500M cheques does not spend its Monday on a 2M one.
  2. How big they are is its OWN dimension (`size`), never a fallback, and
     SMALLER IS BETTER: "if the cheque range is the same between two
     investors, the smaller investor is more interesting". Full marks at USD
     50M, 0.6 at the USD 1bn ceiling, near zero at sovereign scale.
  3. And a hard layer on top, because weights alone cannot hold a sovereign
     down: anything the GATE's size check would refuse (over the ceiling,
     ticket outside our band) is capped at 25 and tier C. One definition of
     "too big", `investor_gate.check_size`, used by the filter and the
     ranking alike, so a row that slipped past the gate (a protected stage,
     an old score) can still never appear at the top of the list.

The details dict names every input and the rule applied, so the card can show
WHY (same principle as the company fit score's clickable evidence).
"""
import json
import math
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

# THE CHEQUE BAND, the size window and the geography sets live in
# ai/investor_gate.py, which runs FIRST in the pipeline, and are imported here,
# never copied. When the gate and the ranking disagreed about the band (the
# gate at 200K-10M while the ranking still ran on 250K-2M) we would have
# emailed the wrong people a correct number, so there is exactly one
# definition of each.
from ai.investor_gate import (  # noqa: F401
    AUM_CEILING_USD_M, AUM_FLOOR_USD_M, EUROPE, GCC, TICKET_MAX_USD_M, TICKET_MIN_USD_M,
    UK_IE, _low, _mentions, check_size, size_of,
)

DIRECT_CHEQUE_TYPES = {"family office", "hnwi", "uhnwi", "angel", "single family office", "multi-family office"}

WARM_TAGS = {"gcc": 15, "bea": 15, "partner": 15, "network": 12, "co-investor": 15, "warm": 12, "intro": 10}
WEIGHTS = {"coinvest": 0.25, "ticket": 0.20, "size": 0.15, "geography": 0.15,
           "affinity": 0.10, "recency": 0.075, "readiness": 0.075}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

# The most an investor the gate would refuse on size can ever score. Low on
# purpose: a sovereign is not "a weak fit", it is not a fit, and the list must
# say so even when every other dimension is perfect.
SIZE_FAIL_CAP = 25.0

# Size ladder (USD millions -> 0..1), log-linear between the anchors.
# "Smaller is better" once they can write the cheque at all.
_SIZE_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (50.0, 1.0),                 # a USD 50M office: our GBP 2M is a real position for them
    (AUM_CEILING_USD_M, 0.6),    # USD 1bn: GBP 10M is about 1 per cent, still material
    (5000.0, 0.2),               # USD 5bn: 0.2 per cent, beneath diligence time
    (50000.0, 0.0),              # USD 50bn and up: a rounding error
)


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
    if _mentions(blob, GCC):
        return 1.0, "GCC home geography"
    if _mentions(blob, UK_IE):
        return 1.0, "UK/IE home geography"
    if _mentions(blob, EUROPE):
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


def _band_label() -> str:
    return f"${TICKET_MIN_USD_M:g}-{TICKET_MAX_USD_M:g}M"


def _ticket_score(inv: Dict) -> (float, str):
    """How much of THEIR stated cheque range is OUR cheque range.

    A range fully inside GBP 200K-10M scores 1.0. A range that merely touches
    ours scores by the share of it that overlaps, floored at 0.4 (any overlap
    means they have written our size at least sometimes). "5M to 500M" is
    therefore 0.41, not 1.0: the top of their range tells you where their
    attention is. Adjacent 0.25, far 0.05, unknown 0.5 (neutral, the size
    dimension and research carry it from there). AUM is NOT a fallback here
    any more; it has its own dimension.
    """
    lo, hi = inv.get("ticket_min_m"), inv.get("ticket_max_m")
    try:
        lo = float(lo) if lo is not None else None
        hi = float(hi) if hi is not None else None
    except (TypeError, ValueError):
        lo = hi = None
    if lo is None and hi is None:
        return 0.5, "ticket unknown"
    lo = lo if lo is not None else 0.0
    hi = hi if hi is not None else lo
    if hi < lo:
        lo, hi = hi, lo
    stated = f"${lo:g}-{hi:g}M" if hi != lo else f"${lo:g}M"
    if hi >= TICKET_MIN_USD_M and lo <= TICKET_MAX_USD_M:
        overlap = min(hi, TICKET_MAX_USD_M) - max(lo, TICKET_MIN_USD_M)
        span = hi - lo
        if span <= 0 or overlap >= span - 1e-9:
            return 1.0, f"stated ticket {stated} sits inside our {_band_label()}"
        frac = max(0.0, min(1.0, overlap / span))
        score = round(0.4 + 0.6 * frac, 2)
        return score, f"stated ticket {stated}: {frac:.0%} of their range is inside our {_band_label()}"
    if lo <= TICKET_MAX_USD_M * 3 and hi >= TICKET_MIN_USD_M / 3:
        return 0.25, f"stated ticket {stated} is adjacent to our {_band_label()}"
    return 0.05, f"stated ticket {stated} is far from our {_band_label()}"


def _size_curve(v: float) -> float:
    """Log-linear interpolation along _SIZE_ANCHORS. Monotonic non-increasing."""
    if v <= _SIZE_ANCHORS[0][0]:
        return _SIZE_ANCHORS[0][1]
    for (x0, y0), (x1, y1) in zip(_SIZE_ANCHORS, _SIZE_ANCHORS[1:]):
        if v <= x1:
            t = (math.log(v) - math.log(x0)) / (math.log(x1) - math.log(x0))
            return y0 + (y1 - y0) * t
    return _SIZE_ANCHORS[-1][1]


def _size_score(inv: Dict) -> (float, str):
    """How big they are. SMALLER IS BETTER once they can write the cheque.

    Ishu, 11 Sep 2026: "if the cheque range is the same between two investors,
    the smaller investor is more interesting." So this is a real dimension
    with its own weight, not a fallback for a missing ticket. Below the floor
    scores low (they cannot comfortably write GBP 200K); unknown is neutral.
    """
    size = size_of(inv)
    if size is None:
        return 0.5, "size unknown"
    v, label = size
    if v < AUM_FLOOR_USD_M:
        return 0.2, f"{label} ${v:,.0f}M: too small to hold GBP 200K in one illiquid position"
    s = round(_size_curve(v), 2)
    if v <= AUM_CEILING_USD_M:
        pct = 13.0 / v * 100  # our top cheque, GBP 10M ~ USD 13M, as a share of their book
        return s, f"{label} ${v:,.0f}M: our top cheque is {pct:.1f}% of their book, material; smaller scores higher"
    return s, f"{label} ${v:,.0f}M: over the USD {AUM_CEILING_USD_M:,.0f}M ceiling, our cheque is beneath their notice"


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
    parts["size"] = _size_score(inv)
    parts["geography"] = _region_score(inv)
    parts["affinity"] = _affinity_score(inv)
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

    # THE SIZE LAYER. The gate's own size check, so "too big" has one meaning.
    # A warm tag and a home address must never lift a sovereign into tier A.
    size_ok, size_basis, size_reason = check_size(inv)
    size_gate = {"pass": size_ok, "basis": size_basis, "why": size_reason or "within the size window"}
    if not size_ok:
        size_gate["capped_from"] = score
        score = min(score, SIZE_FAIL_CAP)

    status = inv.get("status") or ""
    if status in ("Passed", "Talk Later"):
        tier = "Parked"
    elif not size_ok:
        tier = "C"
    elif score >= 70 and contactable:
        tier = "A"
    elif score >= 45 or score >= 70:
        tier = "B"
    else:
        tier = "C"
    details = {k: {"score": round(float(v[0]), 2), "weight": WEIGHTS[k], "why": v[1]} for k, v in parts.items()}
    details["warm_boost"] = {"points": boost, "why": warm or ["no warm path recorded"]}
    details["size_gate"] = size_gate
    details["contactable"] = contactable
    return {"score": score, "tier": tier, "contactable": contactable, "details": details}


def priority_json(result: Dict) -> str:
    return json.dumps(result["details"], default=str)
