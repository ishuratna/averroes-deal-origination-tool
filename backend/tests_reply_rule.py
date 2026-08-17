#!/usr/bin/env python3
"""
Tests for THE REPLY RULE.

    Qualified  = not yet emailed
    Contacted  = emailed, no genuine reply yet
    Responded  = emailed and they genuinely replied

Two halves, both proven against the REAL code rather than a retyped copy:

  1. classify_reply_stage() — the decision, on every combination of inputs.
  2. The actual SELECT extracted from reconcile_reply_stages(), run over a
     simulated targets / email_log / activity_log in DuckDB, so the rows the
     decision is fed are proven too.

Anchored on the production bug this replaced. reconcile_unreplied_contacted()
demoted a company only if activity_log held a status_change to Responded made by
'email-sync'. Of 21 wrongly-Responded companies, 20 had NO activity row at all
(they were moved by the raw-SQL stage-rename migration, which logged nothing), so
the JOIN silently dropped exactly the rows that needed fixing and every preview
came back empty while the board stayed wrong. The new query LEFT JOINs the
activity log and uses it only to choose automatic-vs-ask, never to decide whether
a row is wrong.
"""
import inspect
import os
import re
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from storage.bq_handler import BigQueryHandler, classify_reply_stage  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


# ── Part 1: the decision ─────────────────────────────────────────────────────
print("── Promotion: a reply arrived and status has not caught up ──")
chk("Contacted + genuine reply -> Responded",
    classify_reply_stage("Contacted", True, True), ("promote", "Responded"))
chk("Contacted + no reply -> left alone",
    classify_reply_stage("Contacted", False, True), (None, None))

print()
print("── Demotion: no reply on record ──")
chk("Responded + no reply, moved by sync -> demote to Contacted",
    classify_reply_stage("Responded", False, True, moved_by="email-sync"),
    ("demote", "Contacted"))
chk("Responded + no reply, never emailed -> back to Qualified",
    classify_reply_stage("Responded", False, False, moved_by="email-sync"),
    ("demote", "Qualified"))
chk("Responded + genuine reply -> left alone",
    classify_reply_stage("Responded", True, True, moved_by="email-sync"), (None, None))

print()
print("── The 20 rows the old code could not reach ──")
# THE BUG. No activity row at all, so moved_by is blank. The old version JOINed
# on the activity log and dropped these entirely; they must now surface, as a
# question rather than a silent change.
chk("no record of how it got there -> ask, never silent",
    classify_reply_stage("Responded", False, True, moved_by=""), ("ask", "Contacted"))
chk("a person moved it -> ask, never silent",
    classify_reply_stage("Responded", False, True, moved_by="Ishu Ratna"),
    ("ask", "Contacted"))
# And once the user has answered "yes, move it", the same inputs demote.
chk("...and once confirmed, it demotes",
    classify_reply_stage("Responded", False, True, moved_by="Ishu Ratna", confirmed=True),
    ("demote", "Contacted"))
chk("confirmation on a never-emailed row still goes to Qualified",
    classify_reply_stage("Responded", False, False, moved_by="", confirmed=True),
    ("demote", "Qualified"))

print()
print("── Real work downstream is never touched ──")
for stage in ("Meeting", "DD", "Offer", "Won", "Lost", "Qualified", "Not a Fit"):
    chk(f"{stage} is left alone with no reply",
        classify_reply_stage(stage, False, True, moved_by="email-sync"), (None, None))
    chk(f"{stage} is left alone with a reply",
        classify_reply_stage(stage, True, True, moved_by="email-sync"), (None, None))

# ── Part 2: the real SQL, over a simulated warehouse ─────────────────────────
print()
print("── The actual SELECT from reconcile_reply_stages() ──")
src = inspect.getsource(BigQueryHandler.reconcile_reply_stages)
m = re.search(r'list\(self\.client\.query\(f"""(.*?)"""\)\.result\(\)\)', src, re.S)
assert m, "could not extract the SELECT from reconcile_reply_stages"
sql = m.group(1)
# The predicate is shared, so splice in the real one rather than a copy — if the
# definition of "replied" ever changes, this test follows it automatically.
pred = inspect.getsource(BigQueryHandler._genuine_reply_sql)
pm = re.search(r'return f"""(.*?)"""', pred, re.S)
assert pm, "could not extract the genuine-reply predicate"
sql = sql.replace("{self._genuine_reply_sql()}", pm.group(1))
sql = (sql.replace("`{log}`", "email_log")
          .replace("`{self.activity_table_id}`", "activity_log")
          .replace("`{self.table_id}`", "targets"))
assert "{" not in sql, f"unresolved placeholder in SQL:\n{sql}"
# BigQuery's ARRAY_AGG(... LIMIT 1)[SAFE_OFFSET(0)] has no DuckDB equivalent.
# last_direction plays no part in the reply rule (it drives the Responded page's
# "whose turn is it" hint), so swap in a dialect-neutral equivalent rather than
# weaken the production query for the sake of the test.
sql = re.sub(r"ARRAY_AGG\(direction ORDER BY sent_at DESC LIMIT 1\)\[SAFE_OFFSET\(0\)\]",
             "ANY_VALUE(direction)", sql)
assert "ARRAY_AGG" not in sql, "the array aggregation was not translated"

db = duckdb.connect()
db.execute("""
CREATE TABLE targets (name VARCHAR, status VARCHAR, source VARCHAR,
                      outreach_sent_at TIMESTAMP, hidden_at TIMESTAMP,
                      reply_exempt_at TIMESTAMP, last_reply_at TIMESTAMP);
CREATE TABLE email_log (entity_name VARCHAR, entity_type VARCHAR, direction VARCHAR,
                        classification VARCHAR, sent_at TIMESTAMP);
CREATE TABLE activity_log (company_name VARCHAR, action_type VARCHAR, new_status VARCHAR,
                           created_by VARCHAR, created_at TIMESTAMP);
""")

T = "2026-08-01 10:00:00"


def company(name, status, sent=T, source="Conference", hidden=None, exempt=None):
    db.execute("INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
               [name, status, source, sent, hidden, exempt, None])


def mail(name, direction, cls=None, at=T):
    db.execute("INSERT INTO email_log VALUES (?,?,?,?,?)",
               [name, "company", direction, cls, at])


def moved(name, to, by, at=T):
    db.execute("INSERT INTO activity_log VALUES (?,?,?,?,?)",
               [name, "status_change", to, by, at])


# 1. Sync advanced it, the reply is gone. Automatic demotion.
company("GhostReply", "Responded"); mail("GhostReply", "sent")
moved("GhostReply", "Responded", "email-sync")

# 2. A genuine reply exists. Left alone.
company("RealReply", "Responded")
mail("RealReply", "sent"); mail("RealReply", "received", cls="interested")
moved("RealReply", "Responded", "email-sync")

# 3. THE 20 ROWS. No activity record at all — the old JOIN dropped these.
company("MigratedNoTrace", "Responded"); mail("MigratedNoTrace", "sent")

# 4. A human moved it. Must be ASKED about, not dropped and not silently moved.
company("PhonedIn", "Responded"); mail("PhonedIn", "sent")
moved("PhonedIn", "Responded", "Ishu Ratna")

# 5. Latest move wins: sync moved it most recently, so automatic.
company("SyncLast", "Responded"); mail("SyncLast", "sent")
moved("SyncLast", "Responded", "Ishu Ratna", "2026-07-01 09:00:00")
moved("SyncLast", "Responded", "email-sync", "2026-07-20 09:00:00")

# 6. Never emailed -> Qualified, not Contacted.
company("NeverEmailed", "Responded", sent=None)
moved("NeverEmailed", "Responded", "email-sync")

# 7. Stages past Responded must never even be selected.
for stage in ("Meeting", "DD", "Offer", "Won", "Lost"):
    company(f"Deep{stage}", stage); mail(f"Deep{stage}", "sent")
    moved(f"Deep{stage}", "Responded", "email-sync")

# 8. Internal Test is exempt.
company("TestCo", "Responded", source="Internal Test"); mail("TestCo", "sent")

# 9. A reply arrived but status never advanced -> must be selected for promotion.
company("ReplyNotSeen", "Contacted")
mail("ReplyNotSeen", "sent"); mail("ReplyNotSeen", "received", cls="interested")

# 10. Still waiting, no reply. Selected but classified as no-op.
company("AwaitingReply", "Contacted"); mail("AwaitingReply", "sent")

# 11. An out-of-office is NOT a reply. This is the OOO -> Contacted rule.
company("OooOnly", "Responded"); mail("OooOnly", "sent")
mail("OooOnly", "received", cls="out_of_office")
moved("OooOnly", "Responded", "email-sync")

# 12. An OOO plus a genuine reply -> the genuine one wins, stays Responded.
company("OooAndReal", "Responded"); mail("OooAndReal", "sent")
mail("OooAndReal", "received", cls="out_of_office")
mail("OooAndReal", "received", cls="interested")

# 13. An OOO arriving while still in Contacted must NOT promote it.
company("OooWhileContacted", "Contacted"); mail("OooWhileContacted", "sent")
mail("OooWhileContacted", "received", cls="out_of_office")

# 14. Already answered "keep it" -> the rule must never ask again.
company("ConfirmedByHand", "Responded", exempt=T); mail("ConfirmedByHand", "sent")

# 15. Hidden rows are out of scope.
company("HiddenCo", "Responded", hidden=T); mail("HiddenCo", "sent")

# 16. A received row for a DIFFERENT company must not protect this one.
company("Neighbour", "Responded"); mail("Neighbour", "sent")
moved("Neighbour", "Responded", "email-sync")
mail("SomeoneElse", "received", cls="interested")

rows = db.execute(sql).fetchall()
cols = [d[0] for d in db.description]
sel = {r[cols.index("name")]: dict(zip(cols, r)) for r in rows}

# Feed the selected rows through the REAL decision function.
out = {"promote": {}, "demote": {}, "ask": {}}
for name, r in sel.items():
    action, target = classify_reply_stage(
        r["status"], int(r["recv_count"] or 0) > 0, bool(r["emailed"]),
        moved_by=r["moved_by"] or "")
    if action:
        out[action][name] = target

chk("promoted", out["promote"], {"ReplyNotSeen": "Responded"})
chk("demoted automatically", out["demote"],
    {"GhostReply": "Contacted", "SyncLast": "Contacted",
     "NeverEmailed": "Qualified", "OooOnly": "Contacted", "Neighbour": "Contacted"})
chk("asked about, never silently moved", out["ask"],
    {"MigratedNoTrace": "Contacted", "PhonedIn": "Contacted"})

print()
for name in ("DeepMeeting", "DeepDD", "DeepOffer", "DeepWon", "DeepLost",
             "TestCo", "HiddenCo", "ConfirmedByHand"):
    chk(f"{name} is never even selected", name in sel, False)
for name in ("RealReply", "OooAndReal", "AwaitingReply", "OooWhileContacted"):
    chk(f"{name} is selected but unchanged",
        name in sel and name not in out["promote"] and name not in out["demote"]
        and name not in out["ask"], True)

print()
print("── The bug that made this necessary cannot come back ──")
# The activity log must be LEFT JOINed. An inner join is what hid 20 rows.
chk("activity log is LEFT JOINed, so a missing record cannot hide a row",
    "LEFT JOIN last_move" in sql, True)
chk("MigratedNoTrace (no activity row at all) survives the join",
    "MigratedNoTrace" in sel, True)
chk("...and is surfaced as a question, not dropped",
    out["ask"].get("MigratedNoTrace"), "Contacted")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
