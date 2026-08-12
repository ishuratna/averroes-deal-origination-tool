#!/usr/bin/env python3
"""
Runs the ACTUAL pull-back SELECT from bq_handler.reconcile_unreplied_contacted()
against a simulated targets / email_log / activity_log, so the row-selection
logic is proven rather than assumed.

The query is extracted from the real source, not retyped, so this test fails if
someone edits the method and changes which companies get pulled back.
"""
import inspect
import os
import re
import sys

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
from storage.bq_handler import BigQueryHandler  # noqa: E402

# ── Pull the real SQL out of the method ──────────────────────────────────────
src = inspect.getsource(BigQueryHandler.reconcile_unreplied_contacted)
m = re.search(r'list\(self\.client\.query\(f"""(.*?)"""\)\.result\(\)\)', src, re.S)
assert m, "could not extract the SELECT from the method"
sql = m.group(1)
# Resolve the f-string table placeholders onto local table names.
sql = (sql.replace("`{log}`", "email_log")
          .replace("`{self.activity_table_id}`", "activity_log")
          .replace("`{self.table_id}`", "targets"))
assert "{" not in sql, f"unresolved placeholder in SQL:\n{sql}"

db = duckdb.connect()
db.execute("""
CREATE TABLE targets (name VARCHAR, status VARCHAR, source VARCHAR, outreach_sent_at TIMESTAMP);
CREATE TABLE email_log (entity_name VARCHAR, entity_type VARCHAR, direction VARCHAR,
                        classification VARCHAR);
CREATE TABLE activity_log (company_name VARCHAR, action_type VARCHAR, new_status VARCHAR,
                           created_by VARCHAR, created_at TIMESTAMP);
""")

T = "2026-08-01 10:00:00"

def company(name, status, sent=T, source="Conference"):
    db.execute("INSERT INTO targets VALUES (?,?,?,?)", [name, status, source, sent])

def mail(name, direction, cls=None):
    db.execute("INSERT INTO email_log VALUES (?,?,?,?)", [name, "company", direction, cls])

def moved(name, to, by, at=T):
    db.execute("INSERT INTO activity_log VALUES (?,?,?,?,?)",
               [name, "status_change", to, by, at])

# ── The cases that matter ────────────────────────────────────────────────────
# 1. THE BUG: sync advanced it, the reply is gone. Must be pulled back.
company("GhostReply", "Contacted");            mail("GhostReply", "sent")
moved("GhostReply", "Contacted", "email-sync")

# 2. A genuine reply exists. Must be LEFT ALONE.
company("RealReply", "Contacted")
mail("RealReply", "sent"); mail("RealReply", "received")
moved("RealReply", "Contacted", "email-sync")

# 3. A HUMAN moved it (replied by phone). Must be left alone even with no email.
company("PhonedIn", "Contacted");              mail("PhonedIn", "sent")
moved("PhonedIn", "Contacted", "Ishu Ratna")

# 4. Sync advanced it, then a human re-advanced it later. Latest move wins -> leave.
company("HumanLast", "Contacted");             mail("HumanLast", "sent")
moved("HumanLast", "Contacted", "email-sync", "2026-07-01 09:00:00")
moved("HumanLast", "Contacted", "Ishu Ratna",  "2026-07-20 09:00:00")

# 5. Human moved it first, sync re-advanced later. Latest is sync -> pull back.
company("SyncLast", "Contacted");               mail("SyncLast", "sent")
moved("SyncLast", "Contacted", "Ishu Ratna",   "2026-07-01 09:00:00")
moved("SyncLast", "Contacted", "email-sync",   "2026-07-20 09:00:00")

# 6. Never emailed at all -> should go back to Qualified, not Engaged.
company("NeverEmailed", "Contacted", sent=None)
moved("NeverEmailed", "Contacted", "email-sync")

# 7. Stages past Contacted must NEVER be reversed, reply or not.
for stage in ("Meeting", "DD", "Offer", "Won"):
    company(f"Deep{stage}", stage);            mail(f"Deep{stage}", "sent")
    moved(f"Deep{stage}", "Contacted", "email-sync")

# 8. The Internal Test row is exempt.
company("TestCo", "Contacted", source="Internal Test"); mail("TestCo", "sent")
moved("TestCo", "Contacted", "email-sync")

# 9. Still in Engaged (no reply yet) -> nothing to do, must not appear.
company("AwaitingReply", "Engaged");           mail("AwaitingReply", "sent")

# 10. An outbound-only log plus a received row for a DIFFERENT company must not
#     protect this one (guards against a sloppy join).
company("Neighbour", "Contacted");             mail("Neighbour", "sent")
moved("Neighbour", "Contacted", "email-sync")
mail("SomeoneElse", "received")

# 11. An out-of-office is NOT a reply, so it must not shield a company from
#     being pulled back. This is the whole point of the classification filter.
company("OooOnly", "Contacted");                mail("OooOnly", "sent")
mail("OooOnly", "received", cls="out_of_office")
moved("OooOnly", "Contacted", "email-sync")

# 12. An OOO plus a genuine reply -> the genuine one protects it.
company("OooAndReal", "Contacted");             mail("OooAndReal", "sent")
mail("OooAndReal", "received", cls="out_of_office")
mail("OooAndReal", "received", cls="interested")
moved("OooAndReal", "Contacted", "email-sync")

got = {r[0]: r[1] for r in db.execute(sql).fetchall()}
want = {
    "GhostReply":   "Engaged",
    "SyncLast":     "Engaged",
    "NeverEmailed": "Qualified",
    "Neighbour":    "Engaged",
    "OooOnly":      "Engaged",   # an autoresponder never counts as a reply
}

fails = 0
for name, target in want.items():
    ok = got.get(name) == target
    print(("PASS" if ok else "FAIL"), f"{name} pulled back to {target}",
          "" if ok else f"-> got {got.get(name)!r}")
    fails += 0 if ok else 1

for name in ("RealReply", "PhonedIn", "HumanLast", "TestCo", "AwaitingReply",
             "OooAndReal", "DeepMeeting", "DeepDD", "DeepOffer", "DeepWon"):
    ok = name not in got
    print(("PASS" if ok else "FAIL"), f"{name} left alone",
          "" if ok else f"-> wrongly pulled back to {got.get(name)!r}")
    fails += 0 if ok else 1

extra = set(got) - set(want)
if extra:
    print("FAIL unexpected companies selected:", extra)
    fails += 1

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
