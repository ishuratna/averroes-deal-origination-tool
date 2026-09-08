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
chk("ticket far from ours", r3["details"]["ticket"]["score"], 0.15)
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
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
