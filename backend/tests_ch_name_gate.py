#!/usr/bin/env python3
"""
Regression tests for the Companies House match gate.

Anchored on a REAL false match found in production:

  Stored row : "Porta"  (porta.network — a Web3/token project, site dead
               since 2022, footer names "Porta Limited")
  CH picked  : PORTA DIGITAL LTD (17246969), incorporated 28 May 2026,
               Stansted, no accounts ever filed, PSC Huseyin Durak
  Confidence : "high"

There are 2,637 companies matching "porta" on the register. The real entity
behind the website was PORTA LIMITED, now renamed TRADESHIFT NETWORK LTD
(07010566, incorporated 2009, London). Two independent defects let this through
and both are covered below.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import companies_house_service as chs  # noqa: E402
from services.companies_house_service import (  # noqa: E402
    _incorporated_after, _name_gate, _officer_name_match, _person_tokens,
    _pick_best_match, person_names_for_match,
)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


def gate(a, b):
    g = _name_gate(a, b)
    return g[0] if g else None


print("── Defect 1: a bare name absorbing a descriptor ──")
# THE BUG. "digital" is stripped as a descriptor, so both cores became "porta".
chk("Porta vs PORTA DIGITAL LTD is no longer exact",
    gate("Porta", "PORTA DIGITAL LTD"), "core-ambiguous")
for t in ("PORTA SOFTWARE LTD", "PORTA TECHNOLOGIES LIMITED", "PORTA GROUP LTD",
          "PORTA HOLDINGS LTD", "PORTA LONDON LTD", "PORTA CLOUD LTD"):
    chk(f"Porta vs {t}", gate("Porta", t), "core-ambiguous")
# Real companies from the live CH search for "porta" that are NOT this company.
# These reach the gate via CONTAINMENT ("porta" is inside "porta coffee"), which
# used to score 75 = medium = good enough to write financials. A one-word core
# sits inside hundreds of register names, so it can never be evidence of
# identity on its own.
for t in ("PORTA COFFEE LIMITED", "PORTA FURNITURE LTD", "PORTA LOGISTICS LTD",
          "PORTA GUARD LTD", "PORTA HEALTH LTD"):
    chk(f"Porta vs {t} is not trustworthy", gate("Porta", t), "core-ambiguous")

print()
print("── Still works: genuine matches must not regress ──")
chk("identical but for the legal suffix",
    gate("Acme Widgets Ltd", "Acme Widgets Limited"), "exact-core")
chk("same descriptor on both sides",
    gate("Vrinsoft Technology Inc", "Vrinsoft Technology Ltd"), "exact-core")
chk("descriptors differ (the Kaizen case)",
    gate("Kaizen Software Ltd", "Kaizen Consulting Ltd"), "core-ambiguous")
# Deliberate trade-off: a ONE-WORD name can no longer be matched by
# containment, even a distinctive one. Monzo now needs its registration number
# (stored at ingest, or read off its own website) instead of a string match.
# Losing an auto-match costs a re-run; a wrong match costs wrong financials.
chk("a one-word name is no longer matched by containment",
    gate("Monzo Ltd", "Monzo Bank Ltd"), "core-ambiguous")
chk("a two-word core still matches by containment",
    gate("First Direct", "First Direct Bank Ltd"), "contains")
chk("genuinely unrelated is still refused",
    gate("Vrinsoft Technology Inc", "All Eat App Network Technology Incorporated Ltd"), None)
chk("exact string match",
    gate("Starling Bank Limited", "Starling Bank Limited"), "exact")

print()
print("── Defect 2: matched to a company that did not exist yet ──")
# We knew about Porta long before PORTA DIGITAL LTD was incorporated.
chk("incorporated after we first saw it",
    _incorporated_after({"date_of_creation": "2026-05-28"}, "2026-03-01"), True)
chk("incorporated before we saw it is fine",
    _incorporated_after({"date_of_creation": "2009-09-07"}, "2026-03-01"), False)
chk("same day is not 'after'",
    _incorporated_after({"date_of_creation": "2026-03-01"}, "2026-03-01"), False)
chk("no date known -> never blocks a match",
    _incorporated_after({}, "2026-03-01"), False)
chk("no ingest date known -> never blocks a match",
    _incorporated_after({"date_of_creation": "2026-05-28"}, ""), False)

print()
print("── The two defects together, on the real candidate list ──")
candidates = [
    {"title": "PORTA DIGITAL LTD", "company_number": "17246969", "company_status": "active",
     "date_of_creation": "2026-05-28", "sic_codes": ["62012"], "snippet": "software development",
     "address": {"locality": "Stansted", "country": "England"}},
    {"title": "PORTA COFFEE LIMITED", "company_number": "14914762", "company_status": "active",
     "date_of_creation": "2023-06-05", "sic_codes": [], "snippet": "",
     "address": {"locality": "Sevenoaks", "country": "England"}},
    {"title": "PORTA LOGISTICS LTD", "company_number": "14019945", "company_status": "active",
     "date_of_creation": "2022-04-01", "sic_codes": [], "snippet": "",
     "address": {"locality": "Rugby", "country": "England"}},
]
best = _pick_best_match(candidates, "Porta", sector="Technology",
                        description="blockchain platform", known_since="2026-03-01")
# The company that did not exist yet is gone entirely. Note it would otherwise
# have WON: it is the only software company in the list, so the sector
# tie-break actively preferred it. That is precisely how it was picked.
chk("the impossible candidate is excluded",
    best is not None and best["company_number"] != "17246969", True)
# Whatever is left is one-word containment, which extract_ch_financials refuses
# (it accepts only exact / exact-core / contains), so no financials are written.
chk("...and what remains is too weak to trust", best["_match_gate"], "core-ambiguous")
chk("...which the financials gate refuses",
    best["_match_gate"] in ("exact", "exact-core", "contains"), False)

# With a distinctive name the same machinery still matches happily.
good = [{"title": "VRINSOFT TECHNOLOGY LTD", "company_number": "111", "company_status": "active",
         "date_of_creation": "2015-01-01", "sic_codes": ["62012"], "snippet": "software",
         "address": {"locality": "London", "country": "England"}}]
picked = _pick_best_match(good, "Vrinsoft Technology", sector="Software",
                          description="software", known_since="2026-03-01")
chk("a distinctive name still matches", picked and picked["company_number"], "111")
chk("...at full strength", picked and picked["_match_gate"], "exact-core")

# ── The officer gate ─────────────────────────────────────────────────────────
#
# Anchored on a REAL miss, the mirror image of Porta:
#
#   Stored row : "FoundIt!"  (foundit.com, contact Warren Cowan)
#   On CH      : FOUNDIT! GROUP LIMITED (09690801), active, London, SIC 62012,
#                Warren James Cowan an active director since 2015
#   Result     : refused. "foundit" is one word, so every candidate came back
#                core-ambiguous and no financials were ever read.
#
# The name gate is right to refuse on the name alone. What settles it is that a
# person we already knew about is on that company's register.

print()
print("-- name parsing, both register formats --")
chk("officers format: surname first, comma", _person_tokens("COWAN, Warren James"), ("cowan", ["warren", "james"]))
chk("PSC format: forename first, titled", _person_tokens("Mr Warren James Cowan"), ("cowan", ["warren", "james"]))
chk("plain two-word name", _person_tokens("Warren Cowan"), ("cowan", ["warren"]))
chk("suffixes are not names", _person_tokens("WILLMOTT, Roger Guy, Nr"), ("willmott", ["roger", "guy"]))
chk("a single token yields no forename", _person_tokens("Cowan"), ("cowan", []))
chk("empty is None", _person_tokens("  "), None)

print()
print("-- how well two people agree --")
chk("same person, both formats", _officer_name_match("Warren Cowan", "COWAN, Warren James"), "full")
chk("initial is enough to confirm", _officer_name_match("W Cowan", "COWAN, Warren James"), "full")
chk("relative: surname only, never full", _officer_name_match("Alicia Cowan", "COWAN, Warren James"), "surname")
chk("different surname is nothing", _officer_name_match("Warren Newbert", "COWAN, Warren James"), "")
chk("a shared FORENAME alone proves nothing", _officer_name_match("Warren Newbert", "SMITH, Warren"), "")
chk("one-word name cannot reach full", _officer_name_match("Cowan", "COWAN, Warren James"), "surname")

print()
print("-- which names we are willing to use --")
chk("researched contact first, row contact after, deduplicated",
    person_names_for_match({"contact_name": "Warren Cowan"},
                           {"contact_name": "warren cowan", "original_contact_name": "Andreas Pouros"}),
    ["Warren Cowan", "Andreas Pouros"])
chk("a single-word contact is not usable", person_names_for_match({"contact_name": "Warren"}), [])
chk("the test row's bracketed name is not a person",
    person_names_for_match({"contact_name": "Averroes Admin (Test)"}), [])

print()
print("-- promotion, with the register stubbed --")
_real_off, _real_psc = chs.get_officers_summary, chs.get_psc_summary
_REGISTER = {
    "09690801": ["CARPENTER, James Daniel", "COWAN, Alicia Nadine", "COWAN, Warren James"],
    "13176168": ["PATEL, Nikhil"],
}
calls = []


def _fake_officers(number, max_officers=6):
    calls.append(number)
    return {"officers_summary": "", "directors": [{"name": n} for n in _REGISTER.get(number, [])]}


def _fake_psc(number):
    return {"psc_summary": "", "ownership_verified": "", "psc_individuals": []}


chs.get_officers_summary, chs.get_psc_summary = _fake_officers, _fake_psc
try:
    foundit = [
        {"title": "FOUNDIT PROPERTY LTD", "company_number": "13176168", "company_status": "active",
         "date_of_creation": "2021-02-03", "sic_codes": [], "snippet": "",
         "address": {"locality": "London", "country": "England"}},
        {"title": "FOUNDIT! GROUP LIMITED", "company_number": "09690801", "company_status": "active",
         "date_of_creation": "2015-07-17", "sic_codes": ["62012"], "snippet": "software development",
         "address": {"locality": "London", "country": "United Kingdom"}},
    ]
    blind = _pick_best_match([dict(c) for c in foundit], "FoundIt!", sector="Software",
                             description="software", known_since="2026-01-01")
    chk("without a person the name alone still cannot settle it", blind["_match_gate"], "core-ambiguous")

    calls.clear()
    seeing = _pick_best_match([dict(c) for c in foundit], "FoundIt!", sector="Software",
                              description="software", known_since="2026-01-01",
                              person_names=["Warren Cowan"])
    chk("the director we know decides it", seeing["company_number"], "09690801")
    chk("...and it is promoted past the financials bar", seeing["_match_gate"], "officer-verified")
    chk("...with the evidence recorded",
        "Warren" in (seeing.get("_officer_evidence") or "") and "director" in (seeing.get("_officer_evidence") or ""), True)
    chk("...having checked no more than the candidates in play", len(calls) <= 5, True)

    # A name nobody on the register shares must change NOTHING. This is the
    # whole safety property: the gate can only ever add matches.
    calls.clear()
    stranger = _pick_best_match([dict(c) for c in foundit], "FoundIt!", sector="Software",
                                description="software", known_since="2026-01-01",
                                person_names=["Jane Nobody"])
    chk("an unknown person leaves the verdict exactly as it was",
        stranger["_match_gate"], "core-ambiguous")

    # Surname alone breaks a tie but must not promote: a common surname on a
    # same-named company is a coincidence we cannot rule out.
    relative = _pick_best_match([dict(c) for c in foundit], "FoundIt!", sector="Software",
                                description="software", known_since="2026-01-01",
                                person_names=["Priya Cowan"])
    chk("surname alone is a tie-break, not a promotion", relative["_match_gate"], "core-ambiguous")

    # And it must never fire when the name gate already settled things — that
    # is where the API calls would be wasted.
    calls.clear()
    strong = _pick_best_match(
        [{"title": "VRINSOFT TECHNOLOGY LTD", "company_number": "111", "company_status": "active",
          "date_of_creation": "2015-01-01", "sic_codes": ["62012"], "snippet": "software",
          "address": {"locality": "London", "country": "England"}}],
        "Vrinsoft Technology", sector="Software", description="software",
        known_since="2026-03-01", person_names=["Warren Cowan"])
    chk("a confident name match costs no register calls", calls, [])
    chk("...and keeps its own gate level", strong["_match_gate"], "exact-core")
finally:
    chs.get_officers_summary, chs.get_psc_summary = _real_off, _real_psc

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
