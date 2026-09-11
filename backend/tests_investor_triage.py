#!/usr/bin/env python3
"""
Research triage: the free ordering that stops 7,413 unknown investors being
researched in arbitrary order.

Every rule here was learnt from a misfire on a real-looking name, so each test
names the misfire it prevents.
"""
import inspect
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.investor_triage import (  # noqa: E402
    looks_like_person, name_shape, order_for_research, research_priority,
)

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


def shape(n):
    return name_shape({"name": n})[0]


def tier(n, **kw):
    return research_priority({"name": n, **kw})["tier"]


print("-- fund-shaped names go to the back: the call would only confirm the name --")
for n in ("Octopus Ventures", "Notion Capital Partners", "Index Ventures", "Mercia Fund Managers",
          "Crowdcube", "Balderton Growth Equity", "Praxis Private Equity"):
    chk(f"{n} is a fund", shape(n), "fund")
chk("'Family Ventures LLP' is a fund, not a family (fund is checked FIRST)", shape("Family Ventures LLP"), "fund")

print()
print("-- corporates and public bodies go to the back too --")
chk("'Vodafone Group Plc' is a plc, not a group (legal form beats wanted word)",
    shape("Vodafone Group Plc"), "corporate")
chk("'Kuwait Investment Authority' is an authority, not an investor (sector beats wanted)",
    shape("Kuwait Investment Authority"), "corporate")
chk("'Scottish Enterprise' is a public body", shape("Scottish Enterprise"), "corporate")

print()
print("-- what we want goes to the front --")
for n in ("Al Mahmal Holding", "Sheikh Ahmed Family Office", "Al Rajhi Investments",
          "Bin Zayed Group", "Abdullah Al Othaim"):
    chk(f"{n} is wanted", shape(n), "wanted")
chk("a titled person is the strongest signal a string can give", shape("Sir John Timpson"), "titled")

print()
print("-- the 'al' misfire: whole words only --")
# " al " inside "capit-al " put Balderton Capital and Legal & General at the
# front of the queue as Arabic family names.
chk("'Balderton Capital' is NOT an Arabic name", shape("Balderton Capital") != "wanted")
chk("'Legal & General' is NOT an Arabic name", shape("Legal & General") != "wanted")
chk("'Passion Capital' is NOT an Arabic name", shape("Passion Capital") != "wanted")
chk("...but 'Abdullah Al Othaim' still is", shape("Abdullah Al Othaim"), "wanted")

print()
print("-- two plain words could be anyone: a bump, not a promotion --")
# Hambro Perks, Local Globe and Praxis Rock all pass the person-shape test and
# are all firms, so the shape alone cannot promise a person.
for n in ("Warren Cowan", "Hambro Perks", "Local Globe", "Praxis Rock", "Tom Blomfield"):
    chk(f"'{n}' is plain, not asserted as a person", shape(n), "plain")
chk("plain scores above unclear", research_priority({"name": "Warren Cowan"})["score"]
    > research_priority({"name": "Nauta"})["score"])
chk("...but below wanted", research_priority({"name": "Warren Cowan"})["score"]
    < research_priority({"name": "Al Mahmal Holding"})["score"])
chk("a plain name is 'research', not 'research_first', when cold", tier("Warren Cowan"), "research")
chk("entity words disqualify person shape", looks_like_person("Warren Capital"), "")
chk("digits disqualify", looks_like_person("Fund 3 Partners"), "")

print()
print("-- warmth: a mined name backs a company we track, and that promotes it --")
chk("a plain name backing ONE of our companies reaches research_first",
    tier("Warren Cowan", source_companies="FoundIt!"), "research_first")
chk("more companies, more warmth, capped",
    research_priority({"name": "X Y", "source_companies": "a, b, c, d, e"})["score"]
    <= research_priority({"name": "X Y", "source_companies": "a, b, c, d, e, f, g"})["score"])
chk("a network tag promotes", tier("Warren Cowan", network_tags="GCC"), "research_first")
chk("but a WARM FUND stays at the back: the name already answered the question",
    tier("Octopus Ventures", source_companies="FoundIt!, Journey", network_tags="GCC"), "research_last")
chk("a corporate that backs one of our companies is worth one call",
    tier("Vodafone Group Plc", source_companies="FoundIt!"), "research")

print()
print("-- ordering: the queue, and what is held back --")
rows = [{"name": "Octopus Ventures"}, {"name": "Nauta"}, {"name": "Warren Cowan", "source_companies": "FoundIt!"},
        {"name": "Al Mahmal Holding"}, {"name": "Vodafone Group Plc"}, {"name": "Sir John Timpson"}]
ordered, counts = order_for_research(rows)
names = [r["name"] for r in ordered]
chk("funds and corporates are held back by default", "Octopus Ventures" not in names and "Vodafone Group Plc" not in names)
chk("...but are COUNTED, so nobody thinks they vanished", counts["research_last"], 2)
chk("the titled person leads", names[0], "Sir John Timpson")
chk("the warm mined person and the family holding follow", set(names[1:3]), {"Warren Cowan", "Al Mahmal Holding"})
chk("the unclear name is last of the queued", names[-1], "Nauta")
ordered_all, _ = order_for_research(rows, include_funds=True)
chk("include_funds=True queues everything", len(ordered_all), 6)
chk("...with the fund at the very end", ordered_all[-1]["name"], "Octopus Ventures")
chk("every queued row carries its reasoning", all("why" in r["_triage"] for r in ordered))

print()
print("-- it is a queue order, never a refusal --")
src = inspect.getsource(__import__("ai.investor_triage", fromlist=["x"]))
chk("the triage module never parks, updates or refuses anything",
    not any(w in src for w in ("update_status", "park_bulk", "Passed", "unfit_reason")))

print()
print("-- the eligibility endpoint walks the queue --")
import main  # noqa: E402
esrc = inspect.getsource(main.investorfill_eligible)
chk("the gate runs on stored facts before anything is queued", "qualify_investor(inv)" in esrc)
chk("parked investors are skipped", '("Passed", "Talk Later")' in esrc)
chk("the queue is ordered by triage", "order_for_research(" in esrc)
chk("funds are held back unless asked", "include_funds" in esrc)
chk("the head of the queue is explained", "queue_head" in esrc)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
