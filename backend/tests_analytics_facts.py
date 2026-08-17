#!/usr/bin/env python3
"""
Proves what the analytics ledger will and will not record as a fact.

Runs the REAL SQL from analytics_service._facts_sql() over a simulated warehouse,
so the derivation is proven rather than assumed.

Anchored on two production faults:

  1. Every inbound message became a 'replied' fact, with no classification
     filter. Autoresponders and mailer-daemon bounces therefore counted as
     replies, inflating replied_ever and the headline RESPONSE RATE — the one
     number on the page anyone would act on.

  2. The company's CURRENT STATUS was ingested as a permanent fact. While the
     stage-rename migration wrongly held 18 companies in Responded, the ledger
     banked 'ever reached Responded' for each. Because the ledger is append-only,
     correcting the live rows could not correct the history. A snapshot of a
     mutable field is not evidence that an event ever happened.
"""
import inspect
import os
import re
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import analytics_service as A  # noqa: E402
from storage.bq_handler import BigQueryHandler  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


# ── Build the real SQL with a stub handler ───────────────────────────────────
class Stub:
    project_id = "p"
    dataset_id = "d"
    table_id = "targets"
    activity_table_id = "activity_log"
    STAGE_TIMESTAMP_COLS = BigQueryHandler.STAGE_TIMESTAMP_COLS
    NON_REPLY_CLASSES = BigQueryHandler.NON_REPLY_CLASSES


sql = A._facts_sql(Stub())
sql = sql.replace("`p.d.email_log`", "email_log").replace("`targets`", "targets") \
         .replace("`activity_log`", "activity_log")
sql = re.sub(r"`[\w.]*email_log`", "email_log", sql)
assert "`p.d" not in sql, f"unresolved table reference:\n{sql}"
# DuckDB spells this without the parentheses. Only a fallback for rows with no
# ingested_at, so it plays no part in what is being tested.
sql = sql.replace("CURRENT_TIMESTAMP()", "CURRENT_LOCALTIMESTAMP()")

stamp_cols = list(BigQueryHandler.STAGE_TIMESTAMP_COLS.values())
db = duckdb.connect()
db.execute(f"""
CREATE TABLE targets (
    name VARCHAR, status VARCHAR, source VARCHAR,
    ingested_at TIMESTAMP, stage_entered_at TIMESTAMP,
    outreach_sent_at TIMESTAMP, last_reply_at TIMESTAMP,
    {", ".join(f"{c} TIMESTAMP" for c in stamp_cols)}
);
CREATE TABLE activity_log (company_name VARCHAR, action_type VARCHAR, new_status VARCHAR,
                           created_by VARCHAR, created_at TIMESTAMP);
CREATE TABLE email_log (entity_name VARCHAR, entity_type VARCHAR, direction VARCHAR,
                        classification VARCHAR, sent_at TIMESTAMP);
""")

T = "2026-08-01 10:00:00"


def company(name, status, source="Conference", stamps=None, sent=None, reply=None):
    cols = {c: None for c in stamp_cols}
    cols.update(stamps or {})
    db.execute(
        f"INSERT INTO targets VALUES (?,?,?,?,?,?,?,{','.join('?' * len(stamp_cols))})",
        [name, status, source, T, T, sent, reply] + [cols[c] for c in stamp_cols])


def mail(name, direction, cls=None, at=T):
    db.execute("INSERT INTO email_log VALUES (?,?,?,?,?)",
               [name, "company", direction, cls, at])


def moved(name, to, by="email-sync", at=T):
    db.execute("INSERT INTO activity_log VALUES (?,?,?,?,?)",
               [name, "status_change", to, by, at])


# 1. THE BUG: wrongly labelled Responded, no stamp, no reply, no logged move.
#    Exactly the 18 rows the stage rename mislabelled.
company("FalseResponded", "Responded")
mail("FalseResponded", "sent")

# 2. A real replier: genuine inbound, and the stamp to match.
company("RealReplier", "Responded", stamps={"responded_at": T})
mail("RealReplier", "sent"); mail("RealReplier", "received", cls="interested")

# 3. Only an autoresponder. Must NOT produce a 'replied' fact.
company("OooOnly", "Contacted")
mail("OooOnly", "sent"); mail("OooOnly", "received", cls="out_of_office")

# 4. Only a bounce. Must NOT produce a 'replied' fact.
company("BouncedOnly", "Qualified")
mail("BouncedOnly", "sent"); mail("BouncedOnly", "received", cls="bounce")

# 5. Reached Responded genuinely in the past, since moved on. The EVER fact must
#    survive, which is what the ledger is for.
company("MovedOn", "Meeting", stamps={"responded_at": T, "meeting_at": T})
mail("MovedOn", "sent"); mail("MovedOn", "received", cls="interested")

# 6. Not a Fit has no stamp column, so current status is its only record.
company("Unfit", "Not a Fit")

# 7. Internal Test is excluded everywhere.
company("TestCo", "Responded", source="Internal Test", stamps={"responded_at": T})
mail("TestCo", "sent"); mail("TestCo", "received", cls="interested")

# 8. A logged status_change is evidence even with no stamp: it records that the
#    move actually happened, unlike a snapshot of where the row sits now.
company("LoggedMove", "Contacted")
moved("LoggedMove", "Responded")

rows = db.execute(sql).fetchall()
cols = [d[0] for d in db.description]
facts = {}
for r in rows:
    d = dict(zip(cols, r))
    facts.setdefault(d["name"], set()).add(d["event"])

print("── The 18-row bug: a status snapshot is not a fact ──")
chk("wrongly-Responded row records NO Responded fact",
    "Responded" in facts.get("FalseResponded", set()), False)
chk("...but is still recorded as emailed",
    "emailed" in facts.get("FalseResponded", set()), True)
chk("...and as stored",
    "stored" in facts.get("FalseResponded", set()), True)
chk("...and never as replied",
    "replied" in facts.get("FalseResponded", set()), False)

print()
print("── Autoresponders and bounces are not replies ──")
chk("an out-of-office produces no replied fact",
    "replied" in facts.get("OooOnly", set()), False)
chk("a bounce produces no replied fact",
    "replied" in facts.get("BouncedOnly", set()), False)
chk("an out-of-office company is still counted as emailed",
    "emailed" in facts.get("OooOnly", set()), True)
chk("a bounced company is still counted as emailed",
    "emailed" in facts.get("BouncedOnly", set()), True)
# This is the response-rate fix: replied_ever counts only genuine repliers.
chk("only genuine repliers produce a replied fact",
    {n for n, e in facts.items() if "replied" in e}, {"RealReplier", "MovedOn"})

print()
print("── Genuine facts are kept ──")
chk("a real replier records Responded", "Responded" in facts["RealReplier"], True)
chk("a company that moved on KEEPS its Responded fact",
    "Responded" in facts["MovedOn"], True)
chk("...and its Meeting fact", "Meeting" in facts["MovedOn"], True)
chk("a logged status_change is evidence on its own",
    "Responded" in facts["LoggedMove"], True)

print()
print("── Stages with no stamp column still work ──")
chk("Not a Fit is recorded from current status",
    "Not a Fit" in facts.get("Unfit", set()), True)

print()
print("── Internal Test is excluded everywhere ──")
chk("the test row produces no facts at all", "TestCo" in facts, False)

print()
print("── Structural guarantees ──")
src = inspect.getsource(A._facts_sql)
chk("the replied filter is built from the shared constant, not a local copy",
    "NON_REPLY_CLASSES" in src, True)
# Funnel stages must never be sourced from a status snapshot. Only the unstamped
# ones (Not a Fit, Under Review) may be, and they are not funnel stages.
chk("current status is restricted to unstamped stages", "unstamped_list" in src, True)
for stage in ("Contacted", "Responded", "Meeting", "DD", "Offer"):
    chk(f"{stage} is a stamped stage, so no snapshot can fake it",
        stage in BigQueryHandler.STAGE_TIMESTAMP_COLS, True)

rebuild = inspect.getsource(A.ledger_rebuild)
chk("rebuild preserves facts for companies gone from targets",
    "NOT IN (SELECT LOWER(name)" in rebuild, True)
chk("rebuild defaults to a dry run", "dry_run: bool = True" in rebuild, True)
chk("rebuild derives from the same shared SQL as the sync",
    "_facts_sql(bq_handler)" in rebuild, True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
