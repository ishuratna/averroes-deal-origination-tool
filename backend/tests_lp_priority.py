#!/usr/bin/env python3
"""
Co-investment priority (ai/lp_priority.py): the one ranking for a deal-by-deal
raise at GBP 250K-2M per LP. Pure, zero AI, so every rule is checkable here.
"""
import os
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from ai.lp_priority import lp_priority, parse_tags  # noqa: E402

fails = 0
TODAY = date(2026, 9, 9)


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── Tags ──")
chk("parse trims, dedupes, keeps order", parse_tags(" GCC, bea ;GCC|Partner "), ["GCC", "bea", "Partner"])
chk("empty is empty", parse_tags(None), [])

print()
print("── The ideal GCC family office ──")
fo = {"name": "Al X Family Office", "investor_type": "Family Office", "hq_country": "Saudi Arabia",
      "strategy_preferences": "Growth/Expansion, Co-Investment", "ticket_min_m": 0.5, "ticket_max_m": 2.0,
      "contact_name": "Principal", "contact_email": "p@alx.sa", "pb_last_updated": "2026-03-01",
      "network_tags": "GCC"}
r = lp_priority(fo, TODAY)
chk("tier A", r["tier"], "A")
chk("high score", r["score"] >= 85, True)
chk("co-invest appetite read from preferences", r["details"]["coinvest"]["score"], 1.0)
chk("GCC counted as home geography", "GCC" in r["details"]["geography"]["why"], True)
chk("ticket overlap recognised", r["details"]["ticket"]["score"], 1.0)
chk("GCC tag adds the warm boost", r["details"]["warm_boost"]["points"], 15)

print()
print("── Contactability decides A vs B, never the score alone ──")
no_mail = dict(fo, contact_email="", contact_name="Principal")
r2 = lp_priority(no_mail, TODAY)
chk("same fit without an address is tier B (InvestorFill it)", r2["tier"], "B")
chk("readiness half credit for a named principal", r2["details"]["readiness"]["score"], 0.5)
chk("contactable flag false", r2["contactable"], False)

print()
print("── Institutional money at scale scores low for our cheque ──")
pension = {"name": "Big Pension", "investor_type": "Sovereign/Institutional", "hq_country": "United States",
           "ticket_min_m": 50, "ticket_max_m": 200, "num_pe_commitments": 40, "contact_email": "x@y.z"}
r3 = lp_priority(pension, TODAY)
chk("tier C", r3["tier"], "C")
chk("ticket far from ours", r3["details"]["ticket"]["score"], 0.05)
chk("institutional co-invest discount", r3["details"]["coinvest"]["score"], 0.2)

print()
print("── Evidence beats type ──")
ff = {"name": "Some FoF", "investor_type": "Fund of Funds", "hq_country": "United Kingdom",
      "source_companies": "Plastometrex", "contact_email": "a@b.c"}
r4 = lp_priority(ff, TODAY)
chk("a direct holding in our universe = co-invest appetite 1.0 regardless of type", r4["details"]["coinvest"]["score"], 1.0)
chk("...and a +10 warm boost as a co-investor", r4["details"]["warm_boost"]["points"], 10)
chk("software affinity from the holding", r4["details"]["affinity"]["score"], 1.0)

print()
print("── Unknowns are neutral, never punished as zero ──")
blank = {"name": "Mystery LP"}
r5 = lp_priority(blank, TODAY)
chk("unknown ticket 0.5", r5["details"]["ticket"]["score"], 0.5)
chk("unknown geography 0.5", r5["details"]["geography"]["score"], 0.5)
chk("no contact -> readiness 0 and tier C", (r5["details"]["readiness"]["score"], r5["tier"]), (0.0, "C"))

print()
print("── Parked stays parked; boost is capped ──")
chk("Talk Later -> Parked whatever the score", lp_priority(dict(fo, status="Talk Later"), TODAY)["tier"], "Parked")
many = lp_priority(dict(fo, network_tags="GCC, Bea, Partner, Co-investor", source_companies="X"), TODAY)
chk("warm boost capped at 30", many["details"]["warm_boost"]["points"], 30)
chk("score capped at 100", many["score"] <= 100, True)

print()
print("── THE SIZE LAYER (Ishu, 11 Sep 2026: Mubadala at 98 'does not make sense') ──")
sovereign = {"name": "Mubadala Investment Company", "investor_type": "Sovereign Wealth Fund", "hq_country": "United Arab Emirates",
             "aum_m": 250000, "strategy_preferences": "Co-Investment, Direct", "contact_name": "Head of Ventures",
             "contact_email": "x@mubadala.ae", "pb_last_updated": "2026-08-01", "network_tags": "GCC",
             "ticket_min_m": 5, "ticket_max_m": 500}
rs = lp_priority(sovereign, TODAY)
chk("a quarter-trillion book is tier C whatever else is true", rs["tier"], "C")
chk("...and capped at 25", rs["score"] <= 25, True)
chk("...the card says why (the gate's own reason)", "under 1 per cent" in rs["details"]["size_gate"]["why"], True)
chk("...and records what it would have scored uncapped", rs["details"]["size_gate"]["capped_from"] > 25, True)
chk("size dimension is zero at that scale", rs["details"]["size"]["score"], 0.0)
chk("5-500M ticket is NOT a full overlap: attention is at the top of the range", rs["details"]["ticket"]["score"] < 0.5, True)

one_bn = dict(fo, name="Billion Office", aum_m=1000, ticket_min_m=1, ticket_max_m=5, network_tags="")
small = dict(one_bn, name="Small Office", aum_m=150)
r_bn, r_sm = lp_priority(one_bn, TODAY), lp_priority(small, TODAY)
chk("USD 1bn with our mandate and ticket is still tier A", r_bn["tier"], "A")
chk("same ticket, smaller book scores HIGHER", r_sm["score"] > r_bn["score"], True)
chk("size at the ceiling is 0.6", r_bn["details"]["size"]["score"], 0.6)
chk("size at USD 50M or below is 1.0", lp_priority(dict(one_bn, aum_m=40), TODAY)["details"]["size"]["score"], 1.0)
chk("USD 3bn with a perfect ticket is still capped (the ceiling is the gate's)", lp_priority(dict(one_bn, aum_m=3000), TODAY)["tier"], "C")
chk("size unknown is neutral 0.5", r5["details"]["size"]["score"], 0.5)
chk("below the floor scores low but is a size fact, not a cap", lp_priority(dict(one_bn, aum_m=4), TODAY)["details"]["size"]["score"], 0.2)

print()
print("── Ticket: share of THEIR range inside ours ──")
def tk(lo, hi): return lp_priority({"name": "T", "ticket_min_m": lo, "ticket_max_m": hi}, TODAY)["details"]["ticket"]["score"]
chk("0.5-2M inside the band = 1.0", tk(0.5, 2), 1.0)
chk("1-20M mostly inside = about 0.78", abs(tk(1, 20) - 0.78) < 0.02, True)
chk("10-50M barely touching = about 0.45", abs(tk(10, 50) - 0.45) < 0.02, True)
chk("a single stated 2M point = 1.0", tk(2, 2), 1.0)
chk("20-40M adjacent = 0.25", tk(20, 40), 0.25)
chk("stated ticket beats AUM only for the ticket row: AUM is its own dimension now", "ticket unknown" in lp_priority({"name": "X", "aum_m": 500}, TODAY)["details"]["ticket"]["why"], True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
