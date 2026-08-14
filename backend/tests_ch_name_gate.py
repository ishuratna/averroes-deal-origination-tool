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
from services.companies_house_service import (  # noqa: E402
    _incorporated_after, _name_gate, _pick_best_match,
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

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
