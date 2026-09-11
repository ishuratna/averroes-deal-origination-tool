#!/usr/bin/env python3
"""
InvestorFill: does the research collect what the gate and the ranking need?

Three defects found and fixed 11 Sep 2026 by reading the module against its own
consumers. None of them would have thrown an error; all three silently produced
wrong answers, which is why they survived.

  1. UNITS. The prompt asked for GBP millions while ai/investor_gate.py compares
     against USD thresholds. A GBP 900M family office read as 900 against a
     1000 ceiling and PASSED (it is USD 1.17bn and should fail); a stated
     GBP 250K ticket read as 0.25 against a 0.26 floor and was REFUSED.
  2. MISSING FIELDS. ai/lp_priority.py puts its heaviest weight, 0.30, on
     co-investment appetite, read from strategy_preferences,
     other_preferences and policy_description. The research returned NONE of
     them, so every researched investor scored the default "no co-investment
     evidence yet".
  3. NO MANDATE. The gate qualifies an investor whose mandate covers the UK or
     Europe wherever they sit. geo_preferences was never returned, so research
     could never discover one.
"""
import inspect
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai import investor_fill as f  # noqa: E402
from ai import investor_gate as g  # noqa: E402
from ai import lp_priority as p  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


src = inspect.getsource(f.investor_fill)
# Only the RETURN dict, not the JSON schema inside the prompt: those are the
# keys we ask the model for, which are deliberately named differently (aum_usd_m
# in, aum_m out) precisely so the unit is explicit on the way in.
_ret = src[src.index('        return {\n            "investor_type"'):]
returned = set(re.findall(r'^\s+"([a-z_]+)":', _ret, re.M))

print("-- the research returns every field the RANKING weights --")
weighted = set(re.findall(r'inv\.get\("([a-z_]+)"\)', inspect.getsource(p)))
# What lp_priority reads that InvestorFill could plausibly supply. network_tags
# is set by a human, source_companies by mining, status by the loop.
supplied_by_human = {"network_tags", "source_companies", "status"}
need = weighted - supplied_by_human
missing = sorted(need - returned)
chk(f"nothing the ranking reads is left unfilled (missing: {missing})", missing, [])
chk("co-invest appetite, the heaviest dimension at 0.30, has all three sources",
    {"strategy_preferences", "other_preferences", "policy_description"} <= returned)
chk("recency has a date to work from", "last_commitment_date" in returned)
chk("track record counts are returned",
    {"num_pe_commitments", "num_vc_commitments"} <= returned)

print()
print("-- and every field the GATE needs, including the mandate route --")
chk("geo_preferences is returned, or the mandate route can never fire from research",
    "geo_preferences" in returned)
chk("size: assets and both ticket ends", {"aum_m", "ticket_min_m", "ticket_max_m"} <= returned)
chk("reach: where they are", {"hq_country", "region"} <= returned)

print()
print("-- UNITS. The gate compares USD, so the research must return USD --")
prompt = inspect.getsource(f.investor_fill)
chk("the prompt demands USD millions in capitals", "**USD MILLIONS**" in prompt)
chk("...with a worked conversion so the model cannot misread it",
    "GBP 900 million becomes 1170" in prompt)
chk("...including the small end, where the old bug refused real prospects",
    "GBP 250,000 becomes 0.33" in prompt)
chk("the JSON keys say USD out loud",
    all(k in prompt for k in ("aum_usd_m", "ticket_min_usd_m", "ticket_max_usd_m")))
chk("a conversion is recorded so a wrong one is auditable", "aum_converted_from" in prompt)
chk("no GBP-denominated money field survives in the prompt's schema",
    re.search(r'"(aum|ticket_min|ticket_max)_m"\s*:\s*number', prompt) is None)
# The real check: the number the research returns is compared against USD.
chk("the gate's ceiling is USD", g.AUM_CEILING_USD_M, 1000.0)
chk("GBP 250K would now clear the floor as USD 0.33M", 0.33 >= g.TICKET_MIN_USD_M)
chk("...and under the old GBP reading it would not have", 0.25 >= g.TICKET_MIN_USD_M, False)

print()
print("-- the brief describes how we ACTUALLY invest --")
chk("deal by deal, not a blind fund", "We do NOT raise a blind fund" in prompt and "DEAL BY DEAL" in prompt)
chk("the cheque band is the confirmed one",
    "GBP 200,000 to GBP 10 million" in prompt)
chk("the old fund-raise framing is gone",
    "LIMITED PARTNERS" not in prompt and "commit capital" not in prompt)
chk("the decision-maker ladder is injected, not hard-coded", "{target_brief" in prompt)

print()
print("-- identity: doctrine 4a, which the investor side was missing --")
chk("same entity, differently written", f._identity_ok("Al Rasheed Family Office",
                                                      "Al Rasheed Family Office Ltd")[0], "confirmed")
chk("a DIFFERENT investor is refused", f._identity_ok("Al Rasheed Family Office",
                                                      "Al Mahmal Investment Company")[0], "mismatch")
chk("no echo at all is unverified, not confirmed",
    f._identity_ok("Al Rasheed Family Office", "")[0], "unverified")
chk("furniture words alone never confirm a match",
    f._identity_ok("Capital Partners", "Family Office Limited")[0], "unverified")
chk("a mismatch returns an ERROR and writes nothing",
    'return {"error": note, "identity_status": "mismatch"' in src)

print()
print("-- emails are found or absent, never constructed --")
chk("a published address is kept",
    f._found_email_only({"contact_email": "faisal@almahmal.com", "contact_confidence": "found"}),
    "faisal@almahmal.com")
chk("an inferred address is dropped",
    f._found_email_only({"contact_email": "f.alrasheed@x.com", "contact_confidence": "inferred"}), "")
chk("no stated confidence is not a licence to trust it",
    f._found_email_only({"contact_email": "faisal@x.com"}), "")
chk("a general inbox is not a decision maker",
    f._found_email_only({"contact_email": "info@x.com", "contact_confidence": "found"}), "")
chk("the prompt forbids constructing one", "Never construct one from a name and a domain" in prompt)

print()
print("-- the row actually stores all of it --")
from storage.investor_handler import InvestorBQHandler as H  # noqa: E402
cols = {n for n, _ in H.SCHEMA}
chk("every returned field has a column",
    sorted(returned - cols - {"error", "criteria_assessed"}), [])
write = inspect.getsource(H.update_enrichment)
for field in ("strategy_preferences", "other_preferences", "policy_description",
              "geo_preferences", "num_pe_commitments", "last_commitment_date", "identity_status"):
    chk(f"{field} is written", f"@{field}" in write)
chk("PitchBook's own wording is never overwritten by an AI paraphrase",
    "IFNULL(NULLIF(strategy_preferences, ''), @strategy_preferences)" in write)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
