"""
The investor gate: the three hard filters an investor must pass BEFORE any AI
research runs. Pure, zero AI, zero network. Mirrors SmartFill's hard filters
for companies (doctrine 4: no grounded call on a row we already know is wrong).

Per Ishu, 11 Sep 2026. An investor only enters the pipeline if:

  1. TYPE          not an institution. He has been explicit and repeated:
                   "no institutions whatsoever". Pensions, insurers, sovereigns,
                   endowments, banks, funds of funds and consultants do not
                   write GBP 200K to 10M into a single deal, and the process
                   cost is wrong for both sides.
  2. GEOGRAPHY     headquartered in the UK/Ireland, Europe, or the GCC.
                   Everything else is out of reach for a coffee in London or
                   Riyadh, which is what the email actually asks for.
  3. SIZE          big enough to write our cheque, and NOT SO BIG that our
                   cheque is beneath their notice.

WHY A CEILING EXISTS AT ALL. An investor typically puts 1 to 5 per cent of
assets into one private position, and will not spend diligence time on
anything under roughly half a per cent. Our top cheque is GBP 10M, so:

    AUM  USD 1bn   ->  GBP 10M is about 1 per cent          material
    AUM  USD 5bn   ->  GBP 10M is about 0.2 per cent        beneath notice
    AUM  USD 20bn  ->  GBP 10M is a rounding error          not a conversation

Ishu chose USD 1bn as the ceiling (11 Sep 2026), the tighter of the options:
we would rather always be a material line in a smaller book than an ignorable
one in a large book.

THE FLOOR IS DELIBERATELY LOW. A single wealthy individual with USD 10M can
comfortably write GBP 200K. Excluding them would delete exactly the audience
the email is written for.

UNKNOWN IS NOT A FAILURE. Most family offices publish nothing. A missing AUM
or ticket range PASSES the size check, flagged as unverified, because refusing
everything we cannot measure would empty the universe. Only a figure we
actually hold can disqualify.
"""
import re
from typing import Dict, List, Optional, Tuple

# ── The canonical geography sets ─────────────────────────────────────────────
# Defined HERE, and imported by ai/lp_priority.py, because the gate runs first
# in the pipeline: an investor is filtered before it is ranked. One definition
# only (doctrine 1) or the filter and the ranking could disagree about what the
# GCC is, and nobody would know which was wrong.
GCC = {"saudi arabia", "ksa", "united arab emirates", "uae", "qatar", "kuwait", "bahrain", "oman",
       "dubai", "abu dhabi", "riyadh", "doha", "manama", "muscat"}
UK_IE = {"united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "ireland",
         "london", "dublin", "edinburgh", "manchester"}
EUROPE = {"france", "germany", "netherlands", "belgium", "luxembourg", "switzerland", "spain", "italy",
          "portugal", "austria", "denmark", "sweden", "norway", "finland", "poland", "czech republic",
          "monaco", "liechtenstein", "europe", "western europe", "nordics", "benelux"}


def _low(v) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else ""

# ── Cheque band (Ishu, 11 Sep 2026: GBP 200K to 10M, confirmed) ──────────────
# Expressed in the USD millions PitchBook reports, at roughly 1.30.
# lp_priority imports these, so the filter and the ranking can never drift.
TICKET_MIN_USD_M = 0.26
TICKET_MAX_USD_M = 13.0

# ── Size window, in USD millions ─────────────────────────────────────────────
AUM_FLOOR_USD_M = 10.0      # below this they cannot comfortably write GBP 200K
AUM_CEILING_USD_M = 1000.0  # above this GBP 10M is under 1 per cent: immaterial

# Types that are institutional BY NATURE. No amount of stated appetite makes a
# pension fund a deal-by-deal co-investor at our size, so this is a filter and
# not a score. (lp_priority separately ranks them down; belt and braces.)
INSTITUTIONAL_MARKERS = (
    "pension", "insurance", "insurer", "assurance", "sovereign", "sovereign wealth",
    "endowment", "foundation trust", "superannuation", "fund of funds", "fund-of-funds",
    "investment consultant", "consultant", "outsourced cio", "ocio", "bank", "banking",
    "asset manager", "asset management", "institutional", "development finance",
    "government", "state-owned", "public pension", "reinsur",
)
# Words that look institutional but are not: a "family foundation" or a
# "private bank client" is exactly who we want. Checked first.
INSTITUTIONAL_EXCEPTIONS = (
    "family office", "family investment", "single family", "multi family", "multi-family",
    "family foundation", "family holding", "private office", "private investment office",
    "hnwi", "uhnwi", "high net worth", "angel", "individual",
)

# Types we are actively looking for. Not a filter, but it lets the gate say
# "this is what we want" rather than only "this is not what we do not want".
DIRECT_CHEQUE_MARKERS = (
    "family office", "single family", "multi family", "multi-family", "hnwi", "uhnwi",
    "high net worth", "angel", "private office", "private investment office",
    "investment company", "holding company", "syndicate", "individual", "private investor",
)


def _num(v) -> Optional[float]:
    """A float, or None. Tolerates strings, blanks and junk from uploads."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _geo_blob(inv: Dict) -> str:
    """WHERE THEY ARE. Deliberately excludes `geo_preferences`, which is where
    they INVEST: a Singapore family office with a European mandate is still in
    Singapore, and the email asks for a coffee in London or Riyadh. Including
    it let every global investor through the geography filter."""
    return " ".join(_low(inv.get(k)) for k in
                    ("hq_country", "hq_city", "region", "global_region"))


def check_type(inv: Dict) -> Tuple[bool, str]:
    """Institution by nature? Returns (passes, reason)."""
    blob = f"{_low(inv.get('investor_type'))} {_low(inv.get('name'))} " \
           f"{(_low(inv.get('description')))[:300]}"
    # An exception anywhere wins: "Al Rasheed Family Office (private bank
    # client)" contains "bank" and is plainly not a bank.
    if any(x in blob for x in INSTITUTIONAL_EXCEPTIONS):
        return True, ""
    for marker in INSTITUTIONAL_MARKERS:
        if marker in blob:
            return False, (f"Institutional investor ({marker}). They do not write GBP 200K to 10M "
                           f"into a single deal, and Averroes does not raise from institutions.")
    return True, ""


def check_geography(inv: Dict) -> Tuple[bool, str, str]:
    """In the UK/Ireland, Europe or the GCC? Returns (passes, region, reason).

    Judged on where the investor IS, not where they invest: the ask is a coffee
    in London or Riyadh. A stated European or UK mandate is recorded but does
    not rescue an investor headquartered in Singapore.
    """
    blob = _geo_blob(inv)
    if any(g in blob for g in GCC):
        return True, "GCC", ""
    if any(g in blob for g in UK_IE):
        return True, "UK/IE", ""
    if any(g in blob for g in EUROPE):
        return True, "Europe", ""
    if not blob.strip():
        # Unknown location: let it through to research, which is what research
        # is FOR. The gate re-runs after InvestorFill with a real country.
        return True, "unknown", ""
    where = (inv.get("hq_country") or inv.get("global_region") or inv.get("region") or "").strip()
    return False, "out of scope", (f"Headquartered outside our reach ({where or 'not UK, Europe or GCC'}). "
                                   f"We raise in the UK, Europe and the Gulf.")


def check_size(inv: Dict) -> Tuple[bool, str, str]:
    """Big enough to write our cheque, small enough for it to matter.

    Returns (passes, basis, reason). `basis` names what the judgement was made
    on, so the card can show why rather than just a verdict.
    """
    # A STATED ticket range is the strongest evidence: it is their own number.
    lo, hi = _num(inv.get("ticket_min_m")), _num(inv.get("ticket_max_m"))
    if lo is not None or hi is not None:
        lo = lo if lo is not None else 0.0
        hi = hi if hi is not None else lo
        if hi < TICKET_MIN_USD_M:
            return False, "stated ticket", (f"Writes at most ${hi:g}M, below our GBP 200K minimum. "
                                            f"Too small to be worth a vehicle.")
        if lo > TICKET_MAX_USD_M:
            return False, "stated ticket", (f"Writes ${lo:g}M at minimum, above our GBP 10M maximum. "
                                            f"We cannot offer them a position that size.")
        return True, "stated ticket", ""

    # Otherwise assets, then net assets from the register, as a proxy.
    for col, label in (("aum_m", "AUM"), ("net_assets_m", "net assets")):
        v = _num(inv.get(col))
        if v is None:
            continue
        if v > AUM_CEILING_USD_M:
            return False, label, (f"{label} ${v:,.0f}M. A GBP 10M cheque is under 1 per cent of their "
                                  f"book, so we would never be worth their diligence time.")
        if v < AUM_FLOOR_USD_M:
            return False, label, (f"{label} ${v:,.0f}M is too small to commit GBP 200K to one "
                                  f"illiquid position.")
        return True, label, ""

    # Nothing to measure. Pass, flagged: refusing every undisclosed family
    # office would empty the universe, and finding the number is research's job.
    return True, "unknown", ""


def qualify_investor(inv: Dict) -> Dict:
    """The gate. PURE. Run this BEFORE spending a grounded call on an investor.

    Returns:
      qualified     bool, all three checks passed
      unfit_reason  the ONE sentence to show a human, empty when qualified
      checks        per-check verdict and reason, for the card's evidence panel
      region        UK/IE | GCC | Europe | unknown | out of scope
      size_basis    what the size check judged on
      wants         True when the type is one we actively want (informational)
    """
    type_ok, type_why = check_type(inv)
    geo_ok, region, geo_why = check_geography(inv)
    size_ok, size_basis, size_why = check_size(inv)

    checks = {
        "type": {"pass": type_ok, "why": type_why or "Not an institution."},
        "geography": {"pass": geo_ok, "why": geo_why or f"{region} investor."},
        "size": {"pass": size_ok, "why": size_why or (
            "Size unknown, worth researching." if size_basis == "unknown"
            else f"{size_basis} sits inside our range.")},
    }
    # ONE reason, in the order a human would care about: what they are, then
    # where, then how big. A list of three failures helps nobody.
    reason = ""
    for ok, why in ((type_ok, type_why), (geo_ok, geo_why), (size_ok, size_why)):
        if not ok:
            reason = why
            break

    blob = f"{_low(inv.get('investor_type'))} {(_low(inv.get('description')))[:300]}"
    return {
        "qualified": type_ok and geo_ok and size_ok,
        "unfit_reason": reason,
        "checks": checks,
        "region": region,
        "size_basis": size_basis,
        "wants": any(m in blob for m in DIRECT_CHEQUE_MARKERS),
    }


# ── Who to write to, by what kind of investor this is ────────────────────────
# Ishu, 11 Sep 2026: "let's also try to understand if it's an HNI who's the
# decision maker, if it's a family office who's the decision maker".
#
# The answer differs by structure, and getting it wrong wastes the one email we
# get. A single family office is usually run by a CIO who reports to the family;
# a multi-family office has a head of private markets who owns the co-invest
# decision; a wealthy individual IS the decision maker unless they have staffed
# a private office, in which case the person running it controls access.
#
# Ordered best first. The research pass walks the ladder and stops at the first
# real person it can evidence.
TARGET_LADDERS = {
    "single family office": [
        "Chief Investment Officer", "Head of Investments", "Head of Private Markets",
        "the family principal or a family member on the investment committee",
        "Managing Director",
    ],
    "multi family office": [
        "Head of Private Markets", "Head of Direct Investments", "Chief Investment Officer",
        "Head of Alternatives", "Managing Partner",
    ],
    "individual": [
        "the individual themselves",
        "the person who runs their private office or family office",
        "their chief of staff or principal adviser",
    ],
    "syndicate": [
        "the syndicate lead", "Managing Partner", "General Partner",
    ],
    "investment company": [
        "Chief Investment Officer", "Head of Investments", "Managing Director", "Chairman",
    ],
    "default": [
        "Chief Investment Officer", "Head of Investments", "Managing Partner", "Principal",
        "Founder",
    ],
}


def target_kind(inv: Dict) -> str:
    """Which ladder applies to this investor."""
    blob = f"{_low(inv.get('investor_type'))} {(_low(inv.get('description')))[:300]}"
    # ORDER MATTERS. "Angel Syndicate" contains "angel", but a syndicate is led
    # by a person who decides for the group, which is not the same as writing
    # to an individual about their own money. Syndicate is checked first.
    if "syndicate" in blob:
        return "syndicate"
    if any(k in blob for k in ("hnwi", "uhnwi", "high net worth", "individual",
                               "private investor", "angel")):
        return "individual"
    if "multi family" in blob or "multi-family" in blob:
        return "multi family office"
    if "family office" in blob or "single family" in blob or "family investment" in blob:
        return "single family office"
    if "investment company" in blob or "holding" in blob:
        return "investment company"
    return "default"


def target_ladder(inv: Dict) -> List[str]:
    """The ordered list of roles worth chasing for this investor."""
    return TARGET_LADDERS[target_kind(inv)]


def target_brief(inv: Dict) -> str:
    """One line for the research prompt naming who we actually want."""
    kind = target_kind(inv)
    roles = TARGET_LADDERS[kind]
    if kind == "individual":
        return ("This is a private individual, so the decision maker is usually the person "
                "themselves. Find them, or whoever runs their private office and controls access. "
                "Order of preference: " + "; then ".join(roles) + ".")
    return (f"This is a {kind}, so the person who can actually decide on a co-investment is, in "
            f"order of preference: " + "; then ".join(roles) +
            ". Do NOT return an assistant, an analyst, a marketing or compliance contact, or a "
            "general enquiries address as the contact when a decision maker can be found.")
