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
print("-- filter 1: no institutions, whatever they say about themselves --")
for t in ("Pension Fund", "Insurance Company", "Sovereign Wealth Fund", "Endowment",
          "Fund of Funds", "Bank", "Asset Manager", "Investment Consultant"):
    r = q(name="Big Co", investor_type=t, hq_country="United Kingdom")
    chk(f"{t} refused", r["qualified"], False)
chk("...and the reason names what they are",
    "Institutional" in q(name="X", investor_type="Pension Fund", hq_country="UK")["unfit_reason"])
# The exception matters: these contain institutional WORDS and are our target.
chk("a family foundation is NOT an institution",
    q(name="Al Rasheed Family Foundation", investor_type="Family Office",
      hq_country="Saudi Arabia")["qualified"])
chk("a private bank's CLIENT is not a bank",
    q(name="Private Office", investor_type="Single Family Office",
      description="A family office serving a private banking client", hq_country="UAE")["qualified"])

print()
print("-- filter 2: UK/Ireland, Europe, the GCC. Where they ARE, not where they invest --")
for country, region in (("Saudi Arabia", "GCC"), ("United Arab Emirates", "GCC"),
                        ("United Kingdom", "UK/IE"), ("Ireland", "UK/IE"),
                        ("Switzerland", "Europe"), ("Germany", "Europe")):
    r = q(name="FO", investor_type="Family Office", hq_country=country)
    chk(f"{country} passes as {region}", (r["qualified"], r["region"]), (True, region))
for country in ("United States", "Singapore", "Japan", "Australia", "Brazil"):
    chk(f"{country} refused", q(name="FO", investor_type="Family Office", hq_country=country)["qualified"], False)
chk("...and the reason says where they are",
    "Singapore" in q(name="FO", investor_type="Family Office", hq_country="Singapore")["unfit_reason"])
chk("a European MANDATE does not rescue a Singapore office (the ask is a coffee)",
    q(name="FO", investor_type="Family Office", hq_country="Singapore",
      geo_preferences="Europe, United Kingdom")["qualified"], False)
chk("an unknown location goes THROUGH to research, which is what research is for",
    q(name="Some Office", investor_type="Family Office")["region"], "unknown")

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
chk("...and the size check says it is worth researching",
    "worth researching" in r["checks"]["size"]["why"])

print()
print("-- one reason, in the order a human cares: what, where, how big --")
r = q(name="Giant Pension", investor_type="Pension Fund", hq_country="United States", aum_m=90000)
chk("all three fail but only the type is reported", "Institutional" in r["unfit_reason"])
chk("...and every check is still recorded for the card",
    [r["checks"][k]["pass"] for k in ("type", "geography", "size")], [False, False, False])

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
chk("the decision-maker ladder is passed into the research call",
    "target_brief=target_brief(context)" in src)
chk("the gate RE-RUNS on researched facts, since research usually finds the country",
    "post = qualify_investor(" in src)
chk("a post-research refusal keeps the fields we paid for", "update_enrichment(investor_name, result)" in src)
chk("the Internal Test investor is never gated out",
    src.count('context.get("source") != "Internal Test"') >= 2)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
