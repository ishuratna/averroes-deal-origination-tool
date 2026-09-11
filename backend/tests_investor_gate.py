#!/usr/bin/env python3
"""
The investor gate: three hard filters, run BEFORE any AI (Ishu, 11 Sep 2026).

An investor enters the pipeline only if it is not an institution, sits in the
UK/Ireland, Europe or the GCC, and is big enough to write GBP 200K while small
enough for GBP 10M to matter to it. Everything here is PURE: no AI, no network,
so it is free to run over all 11,763 rows.

The ceiling is the interesting number. An investor puts 1 to 5 per cent of
assets into one private position and ignores anything under about half a per
cent, so at USD 1bn our top cheque is roughly 1 per cent (material) and at USD
5bn it is 0.2 per cent (beneath notice). Ishu chose USD 1bn.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai import investor_gate as g  # noqa: E402
from ai.lp_priority import TICKET_MAX_USD_M, TICKET_MIN_USD_M  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


def q(**kw):
    return g.qualify_investor(kw)


print("-- one definition of the cheque band, shared with the ranking --")
chk("the gate and lp_priority are the SAME objects, so they cannot drift",
    (TICKET_MIN_USD_M, TICKET_MAX_USD_M) == (g.TICKET_MIN_USD_M, g.TICKET_MAX_USD_M))
chk("the band is GBP 200K to 10M in USD millions", (g.TICKET_MIN_USD_M, g.TICKET_MAX_USD_M), (0.26, 13.0))
chk("geography is defined once too", g.GCC is __import__("ai.lp_priority", fromlist=["GCC"]).GCC)

print()
print("-- type is NOT a filter: size decides (Ishu, revised 11 Sep 2026) --")
# It was a filter for about an hour. Size already excludes every institution
# worth avoiding, by arithmetic rather than keyword, and the type filter also
# threw out the small institution that CAN write GBP 2M.
small_pension = q(name="County Pension", investor_type="Pension Fund", hq_country="UK",
                  ticket_min_m=1, ticket_max_m=3)
chk("a small pension writing 1-3M is WELCOME", small_pension["qualified"])
chk("...and is still flagged as institutional for the ranking, not refused",
    small_pension["institutional"], "pension")
giant = q(name="State Pension", investor_type="Pension Fund", hq_country="UK", aum_m=90000)
chk("a USD 90bn pension is refused, but on SIZE", giant["qualified"], False)
chk("...and the reason is the arithmetic, not the label", "under 1 per cent" in giant["unfit_reason"])
chk("only two checks exist now", sorted(giant["checks"].keys()), ["geography", "size"])
chk("a family foundation is not read as institutional",
    q(name="Al Rasheed Family Foundation", investor_type="Family Office",
      hq_country="Saudi Arabia")["institutional"], "")

print()
print("-- filter 1: reach. Where they ARE, or what their MANDATE covers --")
for country, region in (("Saudi Arabia", "GCC"), ("United Arab Emirates", "GCC"),
                        ("United Kingdom", "UK/IE"), ("Ireland", "UK/IE"),
                        ("Switzerland", "Europe"), ("Germany", "Europe")):
    r = q(name="FO", investor_type="Family Office", hq_country=country)
    chk(f"based in {country} passes as {region}", (r["qualified"], r["region"]), (True, region))
# Ishu overruled my earlier removal of mandate, and he is right: someone
# already writing cheques into UK companies is warmer than a neighbour.
r = q(name="FO", investor_type="Family Office", hq_country="Singapore",
      geo_preferences="United Kingdom, Europe")
chk("a Singapore office with a UK mandate QUALIFIES", r["qualified"])
chk("...recorded as mandate, not as being based here", r["region"], "mandate: UK/IE")
chk("a Singapore office with an Asia-only mandate is refused",
    q(name="FO", investor_type="Family Office", hq_country="Singapore",
      geo_preferences="Asia, Japan")["qualified"], False)
chk("a GULF mandate alone does not qualify: we raise there, we do not invest there",
    q(name="FO", investor_type="Family Office", hq_country="Singapore",
      geo_preferences="Middle East, GCC")["qualified"], False)
chk("nothing known at all goes through to research",
    q(name="Some Office", investor_type="Family Office")["region"], "unknown")

print()
print("-- the route in decides WHICH email, and only the Gulf one exists --")
chk("Gulf based -> the email we have",
    q(name="A", investor_type="Family Office", hq_country="Qatar")["email_strategy"], "gcc")
chk("UK based -> needs its own content (TBU)",
    q(name="A", investor_type="Family Office", hq_country="London")["email_strategy"], "uk_eu")
chk("European based -> same TBU bucket",
    q(name="A", investor_type="Family Office", hq_country="France")["email_strategy"], "uk_eu")
chk("qualifying on mandate alone -> a third content strategy (TBU)",
    q(name="A", investor_type="Family Office", hq_country="Singapore",
      geo_preferences="United Kingdom")["email_strategy"], "mandate_only")
chk("every qualifying route has an entry explaining what is needed",
    all(k in g.EMAIL_STRATEGIES for k in ("gcc", "uk_eu", "mandate_only", "unknown")))

print()
print("-- filter 3: big enough to write our cheque, small enough for it to matter --")
chk("USD 500M family office passes", q(name="A", investor_type="Family Office",
                                       hq_country="UK", aum_m=500)["qualified"])
chk("USD 1bn is the ceiling and still passes", q(name="A", investor_type="Family Office",
                                                 hq_country="UK", aum_m=1000)["qualified"])
r = q(name="A", investor_type="Family Office", hq_country="UK", aum_m=5000)
chk("USD 5bn refused: GBP 10M is 0.2 per cent of their book", r["qualified"], False)
chk("...and the reason explains the arithmetic, not just the verdict",
    "under 1 per cent" in r["unfit_reason"])
chk("USD 2M is too small to put GBP 200K in one illiquid position",
    q(name="A", investor_type="HNWI", hq_country="UK", aum_m=2)["qualified"], False)
chk("USD 10M individual passes: they can comfortably write GBP 200K",
    q(name="A", investor_type="UHNWI", hq_country="UAE", aum_m=10)["qualified"])

print()
print("-- a STATED ticket beats any assets proxy: it is their own number --")
chk("stated 1-5M overlaps ours", q(name="A", investor_type="Family Office", hq_country="UK",
                                   ticket_min_m=1, ticket_max_m=5)["qualified"])
chk("a 25M MINIMUM is refused: we cannot offer a position that size",
    q(name="A", investor_type="Family Office", hq_country="UK", ticket_min_m=25)["qualified"], False)
chk("a 0.05M MAXIMUM is refused: too small to be worth a vehicle",
    q(name="A", investor_type="Angel", hq_country="UK", ticket_max_m=0.05)["qualified"], False)
chk("a stated ticket is used even when AUM would have failed",
    q(name="A", investor_type="Family Office", hq_country="UK", aum_m=9000,
      ticket_min_m=1, ticket_max_m=5)["size_basis"], "stated ticket")

print()
print("-- unknown is never a failure: most family offices publish nothing --")
r = q(name="Discreet Family Office", investor_type="Single Family Office", hq_country="Qatar")
chk("no AUM and no ticket still qualifies", r["qualified"])
chk("...flagged as unmeasured rather than silently assumed", r["size_basis"], "unknown")
chk("...and the size check says research will find it",
    "research will find it" in r["checks"]["size"]["why"])

print()
print("-- one reason, size first: it is the criterion Ishu kept --")
r = q(name="Giant Pension", investor_type="Pension Fund", hq_country="United States", aum_m=90000)
chk("both fail but only one reason is shown", "under 1 per cent" in r["unfit_reason"])
chk("...and both checks are still recorded for the card",
    [r["checks"][k]["pass"] for k in ("geography", "size")], [False, False])

print()
print("-- who to write to, by what kind of investor this is --")
chk("a single family office is decided by its CIO",
    g.target_ladder({"investor_type": "Single Family Office"})[0], "Chief Investment Officer")
chk("a multi family office by its head of private markets",
    g.target_ladder({"investor_type": "Multi-Family Office"})[0], "Head of Private Markets")
chk("a wealthy individual decides for themselves",
    g.target_ladder({"investor_type": "UHNWI"})[0], "the individual themselves")
chk("a syndicate by its lead", g.target_ladder({"investor_type": "Angel Syndicate"})[0], "the syndicate lead")
chk("anything else falls back to the CIO",
    g.target_ladder({"investor_type": "Something Odd"})[0], "Chief Investment Officer")
chk("the individual brief says to find whoever controls access",
    "controls access" in g.target_brief({"investor_type": "HNWI"}))
chk("the brief forbids returning an assistant or a general inbox",
    "Do NOT return an assistant" in g.target_brief({"investor_type": "Family Office"}))

print()
print("-- the gate runs BEFORE the AI, and the AI knows who to look for --")
import inspect  # noqa: E402
import main  # noqa: E402
src = inspect.getsource(main.investorfill)
gate_at = src.index("qualify_investor(context)")
spend_at = src.index('_enforce_grounding_budget(1, "InvestorFill")')
chk("the gate is checked before the budget is spent", gate_at < spend_at)
chk("a refused investor returns without any AI call", '"ai_calls": 0' in src)
chk("a refused investor is parked with the reason, not deleted",
    'reason="not_a_fit"' in src and 'gate["unfit_reason"]' in src)
chk("the email strategy travels with the enrichment result",
    'result["email_strategy"] = post["email_strategy"]' in src)
chk("the decision-maker ladder is passed into the research call",
    "target_brief=target_brief(context)" in src)
chk("the gate RE-RUNS on researched facts, since research usually finds the country",
    "post = qualify_investor(" in src)
chk("a post-research refusal keeps the fields we paid for", "update_enrichment(investor_name, result)" in src)
chk("the Internal Test investor is never gated out",
    src.count('context.get("source") != "Internal Test"') >= 2)

print()
print("-- applying the gate at scale: one statement, not three queries a row --")
# 1,292 rows through update_status = ~3,900 sequential BigQuery queries, about
# an hour. Cloud Run cuts a request at ten minutes. Ishu watched curl die at
# 10:00 with a partial apply (11 Sep 2026). park_bulk does it in three
# statements and is the BULK TWIN of update_status: same writes, same audit.
from storage.investor_handler import InvestorBQHandler as H  # noqa: E402
one, bulk = inspect.getsource(H.update_status), inspect.getsource(H.park_bulk)
for field in ("status", "stage_entered_at", "park_reason", "park_reason_detail", "updated_at"):
    chk(f"park_bulk writes {field}, as update_status does", field in one and field in bulk)
chk("park_bulk appends the same audit line to notes, computed from the OLD status in SQL",
    "notes = CONCAT" in bulk and "IFNULL(status, 'Unknown'), ' -> Passed" in bulk)
chk("protected stages are excluded INSIDE the SQL, so a re-run is safe",
    "NOT IN UNNEST(@protected)" in bulk)
chk("...and the protected list includes every work-done stage plus the parked ones",
    {"Contacted", "Responded", "Meeting", "Committed", "Passed", "Talk Later"} <= set(H.PARK_BULK_PROTECTED))
chk("chunks of 500 keep parameter arrays sane", "chunk: int = 500" in bulk)
chk("no client refuses rather than pretending", H(None, "p").park_bulk([("a", "b")], "r", "t"), 0)
audit = inspect.getsource(main.investors_gate_audit)
chk("gate-audit apply goes through park_bulk", "park_bulk(" in audit)
chk("...and no longer loops update_status per row", "update_status(" not in audit)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
