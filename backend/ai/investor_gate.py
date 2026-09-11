"""
The investor gate: the hard filters an investor must pass BEFORE any AI research
runs. Pure, zero AI, zero network. Mirrors SmartFill's hard filters for
companies (doctrine 4: no grounded call on a row we already know is wrong).

TWO filters, per Ishu 11 Sep 2026 (revised the same day, see below):

  1. GEOGRAPHY  they are in the UK/Ireland, Europe or the GCC, OR their stated
                mandate covers the UK, Ireland or Europe. Either route
                qualifies.
  2. SIZE       big enough to write our cheque, and NOT SO BIG that our cheque
                is beneath their notice.

TYPE IS NO LONGER A FILTER. It was, briefly. Ishu removed it: "lets not
completely eliminate institutions, lets only put Size as the criteria." He is
right, and the reason is that SIZE ALREADY DOES THE WORK. Every institution we
actually want to avoid fails the AUM ceiling on its own arithmetic, so a
separate type filter only added a second way to be wrong, and it wrongly
excluded the small institution that CAN write GBP 2M. `lp_priority` still ranks
institutions down, which is the right instrument: a preference, not a wall.

GEOGRAPHY IS ABOUT REACH, AND MANDATE COUNTS. Earlier the same day I removed
`geo_preferences` from the geography test, calling it a bug that a Singapore
family office with a European mandate passed. Ishu overruled that: "their GEO
mandate if is UK/Ireland, awesome." He is right on the substance. Someone who
already invests in UK companies is a warmer prospect than someone who merely
lives nearby, and reach is solvable by a call while mandate is not.

But WHICH ROUTE qualified them decides HOW WE WRITE, and only the GCC email
exists so far, so the gate records the route in `email_strategy`:

    gcc            based in the Gulf        -> LP email v3 is written for them
    uk_eu          based in UK/IE/Europe    -> different content needed (TBU)
    mandate_only   elsewhere, UK/EU mandate -> different content again (TBU)

`lp_recipient_warning` surfaces that, so nobody invites a Zurich family office
for a coffee in Riyadh.

WHY A SIZE CEILING EXISTS. An investor typically puts 1 to 5 per cent of assets
into one private position, and will not spend diligence time on anything under
roughly half a per cent. Our top cheque is GBP 10M, so:

    AUM  USD 1bn   ->  GBP 10M is about 1 per cent          material
    AUM  USD 5bn   ->  GBP 10M is about 0.2 per cent        beneath notice
    AUM  USD 20bn  ->  GBP 10M is a rounding error          not a conversation

Ishu chose USD 1bn, the tighter option: better a material line in a smaller
book than an ignorable one in a large book.

THE FLOOR IS DELIBERATELY LOW. A wealthy individual with USD 10M can comfortably
write GBP 200K, and excluding them would delete exactly the audience the email
is written for.

UNKNOWN IS NOT A FAILURE. Most family offices publish nothing. A missing
country, AUM or ticket PASSES, flagged, and goes through to research to find
that very information (Ishu: "unknown will go through smartfill by itself to
find that particular information"). Only a figure we actually hold disqualifies.
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


# ── Display rollups for the Investor Universe filters (Ishu, 11 Sep 2026) ────
# "Why is the UAE not rolled up into Middle East?" Because the filter was
# reading raw hq_country values. These buckets are computed HERE, from the
# same sets the gate uses, and served on every investor row, so the Universe
# and the Pipeline filter on one definition rather than two copies.
#
# Region (where they ARE):   Middle East | UK & Ireland | Europe | Global | Unknown
# Mandate (where they INVEST): any of UK, Ireland, Europe, Middle East, Other
#
# "Unknown" is kept apart from "Global" on purpose: 7,565 rows have no location
# at all, and folding them into Global would make Global look like a finding.
MIDDLE_EAST = GCC | {"jordan", "lebanon", "egypt", "iraq", "middle east", "mena", "gulf",
                     "gcc", "amman", "beirut", "cairo"}
UK_ONLY = {"united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
           "london", "edinburgh", "manchester", "great britain", "britain"}
IRELAND = {"ireland", "republic of ireland", "dublin", "cork", "eire"}

REGION_BUCKETS = ("Middle East", "UK & Ireland", "Europe", "Global", "Unknown")
MANDATE_BUCKETS = ("UK", "Ireland", "Europe", "Middle East", "Other")


def _low(v) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else ""


_PLACE_RE_CACHE: Dict[frozenset, "re.Pattern"] = {}


def _mentions(text: str, places) -> bool:
    """Does `text` name any of `places`, as WHOLE WORDS?

    Substring containment put Romania in the Middle East ("r-oman-ia") and
    Ukraine in the UK ("uk-raine"), in the gate and in the filter alike
    (11 Sep 2026). Every place name is matched on word boundaries, so "oman"
    matches "Oman" and "Muscat, Oman" and never "Romania".
    """
    if not text:
        return False
    key = frozenset(places)
    pat = _PLACE_RE_CACHE.get(key)
    if pat is None:
        alts = sorted((re.escape(p) for p in places), key=len, reverse=True)
        pat = re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])")
        _PLACE_RE_CACHE[key] = pat
    return pat.search(text) is not None

# ── Cheque band (Ishu, 11 Sep 2026: GBP 200K to 10M, confirmed) ──────────────
# Expressed in the USD millions PitchBook reports, at roughly 1.30.
# lp_priority imports these, so the filter and the ranking can never drift.
TICKET_MIN_USD_M = 0.26
TICKET_MAX_USD_M = 13.0

# ── Size window, in USD millions ─────────────────────────────────────────────
AUM_FLOOR_USD_M = 10.0      # below this they cannot comfortably write GBP 200K
AUM_CEILING_USD_M = 1000.0  # above this GBP 10M is under 1 per cent: immaterial

# Institutional markers. NOT A FILTER any more (Ishu, 11 Sep 2026): size already
# excludes every institution we actually want to avoid, and a type filter also
# threw out the small institution that CAN write GBP 2M. Kept because
# `looks_institutional` is useful on the card and in `lp_priority`'s ranking:
# a preference, not a wall.
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


def _base_blob(inv: Dict) -> str:
    """Where they ARE."""
    return " ".join(_low(inv.get(k)) for k in
                    ("hq_country", "hq_city", "region", "global_region"))


def _mandate_blob(inv: Dict) -> str:
    """Where they INVEST. A separate field because it qualifies by a different
    route and, crucially, calls for a different email."""
    return " ".join(_low(inv.get(k)) for k in
                    ("geo_preferences", "strategy_preferences", "other_preferences",
                     "policy_description"))


def region_bucket(inv: Dict) -> str:
    """Where they ARE, rolled up for the filter. One of REGION_BUCKETS."""
    base = _base_blob(inv)
    if not base.strip():
        return "Unknown"
    if _mentions(base, MIDDLE_EAST):
        return "Middle East"
    if _mentions(base, UK_IE):
        return "UK & Ireland"
    if _mentions(base, EUROPE):
        return "Europe"
    return "Global"


def mandate_buckets(inv: Dict) -> List[str]:
    """Where they INVEST, rolled up. A mandate can cover several, so a list.
    Empty when no mandate is stated at all. Reads geo_preferences ONLY, not
    the strategy text: "UK" inside a strategy sentence is not a mandate."""
    text = _low(inv.get("geo_preferences"))
    if not text.strip():
        return []
    out = []
    if _mentions(text, UK_ONLY):
        out.append("UK")
    if _mentions(text, IRELAND):
        out.append("Ireland")
    if _mentions(text, EUROPE):
        out.append("Europe")
    if _mentions(text, MIDDLE_EAST):
        out.append("Middle East")
    # Anything named that is not one of ours (US, Asia, Africa, "global").
    if any(w in text for w in ("united states", "usa", "north america", "asia", "africa",
                               "latin america", "australia", "global", "worldwide", "india",
                               "china", "japan", "singapore", "canada")):
        out.append("Other")
    return out or ["Other"]


def looks_institutional(inv: Dict) -> str:
    """The institutional marker found, or "". INFORMATIONAL ONLY.

    This does not refuse anybody. Size decides (Ishu, 11 Sep 2026), and size
    catches every institution worth avoiding by arithmetic rather than by
    keyword. A small institution that can write GBP 2M is welcome.
    """
    blob = f"{_low(inv.get('investor_type'))} {_low(inv.get('name'))} " \
           f"{(_low(inv.get('description')))[:300]}"
    # An exception anywhere wins: "Al Rasheed Family Office (private bank
    # client)" contains "bank" and is plainly not a bank.
    if any(x in blob for x in INSTITUTIONAL_EXCEPTIONS):
        return ""
    for marker in INSTITUTIONAL_MARKERS:
        if marker in blob:
            return marker
    return ""


def check_geography(inv: Dict) -> Tuple[bool, str, str, str]:
    """Returns (passes, region, email_strategy, reason).

    TWO routes in, per Ishu (11 Sep 2026):
      WHERE THEY ARE      the Gulf, or the UK/Ireland, or Europe.
      WHAT THEY BACK      a stated mandate covering the UK, Ireland or Europe,
                          wherever they happen to sit. Someone already writing
                          cheques into UK companies is a warmer prospect than
                          someone who merely lives nearby.

    The route matters beyond a yes: it decides which email they should get, and
    only the GCC one is written. `email_strategy` carries that forward so the
    draft can say so rather than sending the wrong invitation.
    """
    base, mandate = _base_blob(inv), _mandate_blob(inv)

    if _mentions(base, GCC):
        return True, "GCC", "gcc", ""
    if _mentions(base, UK_IE):
        return True, "UK/IE", "uk_eu", ""
    if _mentions(base, EUROPE):
        return True, "Europe", "uk_eu", ""

    # Not based in reach. Does their MANDATE bring them in?
    if _mentions(mandate, UK_IE):
        return True, "mandate: UK/IE", "mandate_only", ""
    if _mentions(mandate, EUROPE):
        return True, "mandate: Europe", "mandate_only", ""
    # A Gulf MANDATE is not a qualification: we raise there, we do not invest
    # there, so an investor who only looks at Gulf assets is not our audience.

    if not base.strip() and not mandate.strip():
        # Nothing to judge. Research exists to find this.
        return True, "unknown", "unknown", ""

    where = (inv.get("hq_country") or inv.get("global_region") or inv.get("region") or "").strip()
    return False, "out of scope", "none", (
        f"Based outside the UK, Europe and the Gulf ({where or 'location unclear'}) with no stated "
        f"UK, Irish or European mandate. Nothing to talk to them about.")


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


# Which email each qualifying route needs. Only the first exists.
EMAIL_STRATEGIES = {
    "gcc": "LP email v3, written for the Gulf: a coffee in London or Riyadh.",
    "uk_eu": "TBU. A UK or European investor needs different content; the Gulf email does not fit.",
    "mandate_only": "TBU. Based elsewhere but backs UK and European companies, so different again.",
    "unknown": "Region not established yet. Research first.",
}


def qualify_investor(inv: Dict) -> Dict:
    """The gate. PURE. Run this BEFORE spending a grounded call on an investor.

    Returns:
      qualified        bool, both filters passed
      unfit_reason     the ONE sentence to show a human, empty when qualified
      checks           per-check verdict and reason, for the card
      region           GCC | UK/IE | Europe | mandate: ... | unknown | out of scope
      email_strategy   which email they need: gcc | uk_eu | mandate_only | unknown
      size_basis       what the size check judged on
      institutional    the institutional marker found, or "". NOT a refusal.
      wants            True when the type is one we actively want
    """
    geo_ok, region, strategy, geo_why = check_geography(inv)
    size_ok, size_basis, size_why = check_size(inv)
    inst = looks_institutional(inv)

    checks = {
        "geography": {"pass": geo_ok, "why": geo_why or f"{region}."},
        "size": {"pass": size_ok, "why": size_why or (
            "Size unknown, research will find it." if size_basis == "unknown"
            else f"{size_basis} sits inside our range.")},
    }
    # Size first: it is the criterion Ishu kept, and a fund that is too big is
    # a harder no than a location we might work around.
    reason = size_why if not size_ok else (geo_why if not geo_ok else "")

    blob = f"{_low(inv.get('investor_type'))} {(_low(inv.get('description')))[:300]}"
    return {
        "qualified": geo_ok and size_ok,
        "unfit_reason": reason,
        "checks": checks,
        "region": region,
        "email_strategy": strategy,
        "size_basis": size_basis,
        "institutional": inst,
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
