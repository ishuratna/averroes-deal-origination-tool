"""
Research triage: which unknown investors deserve an InvestorFill call first.

THE PROBLEM (gate audit, 11 Sep 2026). 7,413 of 11,905 investors have no
location, no size and no type: a bare name mined from the cap table of a company
in our universe. The gate rightly lets them through as "unknown", because
finding out is what research is for. But research costs a grounded call each,
the shared daily budget is a few hundred, and blasting 7,413 names in arbitrary
order would spend a month of budget learning that most of them are VC funds
that will never write GBP 200K into a single deal.

THE ANSWER IS FREE. The NAME already says a great deal:

    "Octopus Ventures"            a venture fund. Will not co-invest at our size.
    "Notion Capital Partners"     same.
    "Warren Cowan"                a person. Exactly who the email is written for.
    "Al Mahmal Holding"           a family holding company. Same.
    "Sheikh Ahmed Family Office"  obviously.
    "Vodafone Group Plc"          a corporate. Strategic, not a co-investor.

So this module scores every unknown by name shape and warmth, PURE and zero AI,
and `/investorfill/eligible` walks the queue in that order. Fund-shaped names
are held back entirely unless asked for, because a research call that confirms
"yes, this is a venture fund" buys nothing we did not already know.

THREE TIERS:
    research_first   person, family office, holding company, private investor,
                     or anything with a warm path (backs a company we track,
                     carries a network tag). These convert.
    research         cannot tell from the name. Worth the call.
    research_last    fund-shaped or corporate-shaped. Held back by default.

This is a QUEUE ORDER, not a verdict. Nothing here refuses anyone: a fund-shaped
name is deferred, not parked, and can be researched on request. The gate is the
only thing that refuses.
"""
import re
from typing import Dict, List, Tuple

from ai.investor_gate import _low

# ── Name shapes ──────────────────────────────────────────────────────────────
# Fund vehicles and managers. A research call on these returns "it is a fund",
# which the name already told us.
FUND_MARKERS = (
    "ventures", "venture partners", "venture capital", "capital partners", "growth equity",
    "growth partners", "equity partners", "private equity", "buyout", "accelerator",
    "incubator", "seed fund", "seed capital", " vc", "vc ", "fund i", "fund ii", "fund iii",
    "fund iv", "fund v", " fund", "funds", "sicav", "fcp", "lp fund", "opportunities",
    "infrastructure", "credit", "debt", "lending", "real estate", "reit", "property fund",
    "impact fund", "innovation fund", "enterprise capital", "scale-up", "scaleup",
    "crowdcube", "seedrs", "crowdfunding", "syndicate fund", "angel network",
    "ventures ltd", "management company", "asset management", "investment management",
    "capital management", "partners lp", "partners llp",
)
# Operating companies and public bodies. Rarely a GBP 200K co-investor.
# Legal forms are checked BEFORE the wanted words, because "Vodafone Group Plc"
# is a plc first and a "group" second.
CORPORATE_LEGAL_FORMS = (
    " plc", "plc ", " inc", "inc.", " corp", "corporation", " ag ", " se ", " nv", " sa ",
    " spa", "gmbh", " oy", " ab ", " kk", "kabushiki",
)
CORPORATE_MARKERS = (
    "technologies", "software", "systems", "solutions", "telecom", "energy", "pharma",
    "airlines", "motors", "bank", "banking", "insurance", "university", "council",
    "government", "ministry", "authority", "agency", "enterprise", "enterprises",
    "industries", "manufacturing", "retail", "media", "broadcasting",
)
# What we are actually looking for. Front of the queue.
WANTED_MARKERS = (
    "family office", "family", "families", "holding", "holdings", "private office",
    "investment office", "trust", "foundation", "estate", "& sons", "and sons", "brothers",
    "group", "invest", "investments", "investment company", "angel", "private investor",
    "hnwi", "uhnwi", "individual", "sheikh", "shaikh", "sheikha", "h.h.", "his highness",
    "sons of",
)
# Arabic name particles. Matched as WHOLE WORDS only: "al " inside "capital "
# put Balderton Capital and Legal & General at the front of the queue.
_ARABIC_PARTICLES = re.compile(r"\b(al|bin|bint|abu|ibn)\b")
# Words that mark a name as a legal entity rather than a person.
_ENTITY_WORDS = {
    "ltd", "limited", "llc", "llp", "lp", "plc", "inc", "corp", "co", "company", "gmbh", "sa",
    "ag", "nv", "bv", "oy", "ab", "spa", "sarl", "pte", "pty", "holdings", "holding", "group",
    "capital", "partners", "ventures", "fund", "funds", "trust", "office", "investments",
    "investment", "management", "advisors", "advisers", "associates", "international",
    "global", "the", "of", "and", "authority", "enterprise", "enterprises", "industries",
    "technologies", "systems", "solutions", "equity", "assets", "wealth", "securities",
    "finance", "financial", "bank", "insurance", "energy", "media", "digital", "labs",
    "studio", "studios", "network", "networks", "foundation", "family", "sons", "brothers",
    "properties", "property", "estates", "consulting", "services", "council", "agency",
    "development", "corporation", "incorporated", "society", "association", "institute",
}
_TITLES = {"mr", "mrs", "ms", "dr", "sir", "dame", "lord", "lady", "sheikh", "shaikh", "prince",
           "princess", "hh", "hrh", "h.h.", "h.r.h."}


def looks_like_person(name: str) -> str:
    """"titled" | "plain" | "".

    A title (Sheikh, Sir, Dr, HH) followed by a name is as good as a person
    gets from a string. Otherwise two or three capitalised words with no entity
    or business word is only "not obviously a firm": Hambro Perks, Local Globe
    and Praxis Rock all pass that test and are all firms. So "plain" earns a
    modest bump, not a promotion, and the research call settles it.
    """
    raw = (name or "").strip()
    if not raw or any(ch.isdigit() for ch in raw) or "&" in raw or "/" in raw:
        return ""
    words = [w.strip(".,") for w in raw.split()]
    low = [w.lower() for w in words]
    if not 2 <= len(words) <= 5:
        return ""
    rest = low[1:] if low[0] in _TITLES else low
    if any(w in _ENTITY_WORDS for w in rest):
        return ""
    if low[0] in _TITLES:
        return "titled"
    if len(words) > 3:
        return ""
    if not all(w[:1].isupper() for w in words) or any(w.isupper() and len(w) > 1 for w in words):
        return ""
    return "plain"


def name_shape(inv: Dict) -> Tuple[str, str]:
    """('titled' | 'wanted' | 'plain' | 'fund' | 'corporate' | 'unclear', what decided it).

    Order matters and every step of it was learnt from a misfire:
      fund first          "Family Ventures LLP" is a fund, not a family
      legal form second   "Vodafone Group Plc" is a plc, not a group
      then corporate      sector words and public bodies ("Kuwait Investment Authority")
      then wanted words   family, holding, office, trust, Arabic particles as whole words
      then person shape   titled is strong; plain is only "not obviously a firm"
    """
    name = _low(inv.get("name"))
    padded = f" {name} "
    for m in FUND_MARKERS:
        if m in padded:
            return "fund", m.strip()
    for m in CORPORATE_LEGAL_FORMS:
        if m in padded:
            return "corporate", m.strip()
    # Public bodies and sector words BEFORE the wanted words: "Kuwait Investment
    # Authority" is an authority first and "invest" second.
    for m in CORPORATE_MARKERS:
        if m in padded:
            return "corporate", m.strip()
    for m in WANTED_MARKERS:
        if m in padded:
            return "wanted", m.strip()
    if _ARABIC_PARTICLES.search(name):
        return "wanted", "arabic name"
    person = looks_like_person(inv.get("name") or "")
    if person == "titled":
        return "titled", "a titled person"
    if person == "plain":
        return "plain", "two or three plain words, could be a person or a small firm"
    return "unclear", ""


def research_priority(inv: Dict) -> Dict:
    """Score 0-100 and tier. PURE. Higher = research sooner.

    Name shape sets the base; warmth adds. A fund-shaped name stays in
    research_last no matter how warm, because the call would only confirm
    what the name says; a warm PERSON is the best call we can make.
    """
    shape, marker = name_shape(inv)
    # "plain" sits just above unclear: a two-word name is a person about as
    # often as it is a boutique, and only the research call can tell.
    base = {"titled": 70, "wanted": 60, "plain": 48, "unclear": 40, "corporate": 15, "fund": 5}[shape]
    reasons = [f"{shape}" + (f" ({marker})" if marker else "")]

    warmth = 0
    srcs = [s for s in re.split(r"[,;]", inv.get("source_companies") or "") if s.strip()]
    if srcs:
        # Backs a company we track: that is a direct investor by definition,
        # and the email has a true "why them" line ready made.
        warmth += min(20, 8 + 4 * len(srcs))
        reasons.append(f"backs {len(srcs)} company(ies) in our universe")
    tags = [t for t in re.split(r"[,;|]", inv.get("network_tags") or "") if t.strip()]
    if tags:
        warmth += 15
        reasons.append(f"tagged {', '.join(t.strip() for t in tags)}")
    if inv.get("contact_name") or inv.get("contact_email"):
        warmth += 5
        reasons.append("a contact already on file")

    score = min(100, base + warmth)
    if shape == "fund":
        tier = "research_last"
    elif shape == "corporate":
        tier = "research_last" if not srcs else "research"
    elif score >= 60:
        tier = "research_first"
    else:
        tier = "research"
    return {"score": score, "tier": tier, "shape": shape, "why": "; ".join(reasons)}


def order_for_research(investors: List[Dict], include_funds: bool = False) -> Tuple[List[Dict], Dict]:
    """Sort a list of investors into research order and summarise the split.

    Fund-shaped and corporate-shaped names are HELD BACK unless include_funds,
    so the daily budget goes on names that can actually convert. Returns
    (ordered investors each carrying `_triage`, counts by tier).
    """
    scored = []
    counts = {"research_first": 0, "research": 0, "research_last": 0}
    for inv in investors:
        t = research_priority(inv)
        counts[t["tier"]] += 1
        if t["tier"] == "research_last" and not include_funds:
            continue
        scored.append({**inv, "_triage": t})
    scored.sort(key=lambda i: (-i["_triage"]["score"], _low(i.get("name"))))
    return scored, counts
