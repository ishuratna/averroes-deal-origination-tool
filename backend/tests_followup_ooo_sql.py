#!/usr/bin/env python3
"""
Proves the /followups reminder logic, including the out-of-office override.

The BigQuery SQL is translated to the DuckDB equivalents (TIMESTAMP_ADD,
SAFE.PARSE_DATE etc.) but the RULE is reimplemented here identically and then
cross-checked against services.ooo_detect.followup_due_date, so the query and
the Python helper can never disagree about when a reminder is due.
"""
import os
import sys
from datetime import date, datetime, timedelta

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.ooo_detect import followup_due_date  # noqa: E402

DAYS = 14          # @days
REPLY_DAYS = 3     # @reply_days
NOW = datetime(2026, 8, 12, 12, 0, 0)

SQL = f"""
WITH msgs AS (
    SELECT entity_name, direction, subject, snippet, counterparty_email, sent_at,
           COALESCE(classification, '') = 'out_of_office' AS is_ooo
    FROM email_log
    WHERE entity_type = 'company'
),
last_sent AS (
    SELECT * EXCLUDE(rn) FROM (
        SELECT entity_name, subject, snippet, counterparty_email, sent_at,
               ROW_NUMBER() OVER (PARTITION BY entity_name ORDER BY sent_at DESC) AS rn
        FROM msgs WHERE direction = 'sent'
    ) WHERE rn = 1
),
last_recv AS (
    SELECT * EXCLUDE(rn) FROM (
        SELECT entity_name, subject, snippet, counterparty_email, sent_at,
               ROW_NUMBER() OVER (PARTITION BY entity_name ORDER BY sent_at DESC) AS rn
        FROM msgs WHERE direction = 'received' AND NOT is_ooo
    ) WHERE rn = 1
),
calc AS (
    SELECT t.name, t.action_bucket, NULLIF(t.ooo_until, '') AS ooo_until,
           s.sent_at AS last_sent_at, r.sent_at AS last_recv_at,
           CASE WHEN TRY_CAST(NULLIF(t.ooo_until, '') AS DATE) IS NOT NULL
                     AND DATE_DIFF('day', CAST(s.sent_at AS DATE),
                                   TRY_CAST(NULLIF(t.ooo_until, '') AS DATE)) > {DAYS}
                THEN CAST(TRY_CAST(NULLIF(t.ooo_until, '') AS DATE) + INTERVAL 1 DAY AS TIMESTAMP)
                ELSE s.sent_at + INTERVAL {DAYS} DAY
           END AS due_at
    FROM targets t
    JOIN last_sent s ON s.entity_name = t.name
    LEFT JOIN last_recv r ON r.entity_name = t.name
    WHERE t.status IN ('Engaged','Contacted','Meeting','DD','Offer')
      AND COALESCE(t.source,'') != 'Internal Test'
)
SELECT name,
       CASE WHEN owed THEN 'we_owe_reply' ELSE 'waiting_on_them' END AS type,
       DATE_DIFF('day', last_sent_at, due_at) AS threshold_days,
       CAST(due_at AS DATE) AS due_on
FROM (SELECT *, last_recv_at IS NOT NULL AND last_recv_at > last_sent_at AS owed FROM calc)
WHERE (
    (owed AND DATE_DIFF('day', last_recv_at, TIMESTAMP '{NOW}') >= {REPLY_DAYS}
       AND COALESCE(action_bucket,'') NOT IN ('not_fit_no_respond','declined_close'))
    OR (NOT owed AND TIMESTAMP '{NOW}' >= due_at)
)
ORDER BY name
"""

db = duckdb.connect()
db.execute("""
CREATE TABLE targets (name VARCHAR, status VARCHAR, source VARCHAR,
                      ooo_until VARCHAR, action_bucket VARCHAR);
CREATE TABLE email_log (entity_name VARCHAR, entity_type VARCHAR, direction VARCHAR,
                        subject VARCHAR, snippet VARCHAR, counterparty_email VARCHAR,
                        sent_at TIMESTAMP, classification VARCHAR);
""")


def company(name, ooo=None, status="Engaged", source="Conference", bucket=None):
    db.execute("INSERT INTO targets VALUES (?,?,?,?,?)", [name, status, source, ooo, bucket])


def mail(name, direction, when, cls=None):
    db.execute("INSERT INTO email_log VALUES (?,?,?,?,?,?,?,?)",
               [name, "company", direction, "s", "b", "a@b.com", when, cls])


D = lambda *a: datetime(*a)

# 1. No OOO, emailed 20 days ago -> due at 14, so it shows.
company("PlainOverdue");      mail("PlainOverdue", "sent", D(2026, 7, 23))
# 2. No OOO, emailed 5 days ago -> not due yet.
company("PlainTooSoon");      mail("PlainTooSoon", "sent", D(2026, 8, 7))
# 3. OOO back well beyond 14 days: away until 15 Sep, emailed 1 Aug.
#    length = 45 -> reminder at 46 days -> 16 Sep. Not due on 12 Aug.
company("LongLeave", ooo="2026-09-15"); mail("LongLeave", "sent", D(2026, 8, 1))
mail("LongLeave", "received", D(2026, 8, 1, 1), cls="out_of_office")
# 4. OOO shorter than 14 days: back 5 Aug, emailed 1 Aug -> floor of 14 applies,
#    due 15 Aug. Not due on 12 Aug.
company("ShortLeave", ooo="2026-08-05"); mail("ShortLeave", "sent", D(2026, 8, 1))
mail("ShortLeave", "received", D(2026, 8, 1, 1), cls="out_of_office")
# 5. OOO that has now passed, emailed 25 days ago -> 14-day floor already met,
#    shows as normal. The stale OOO must not defer it further.
company("LeaveOver", ooo="2026-07-25"); mail("LeaveOver", "sent", D(2026, 7, 18))
mail("LeaveOver", "received", D(2026, 7, 18, 1), cls="out_of_office")
# 6. An OOO must NOT make it look like we owe a reply.
company("OnlyOoo", ooo="2026-09-15"); mail("OnlyOoo", "sent", D(2026, 7, 1))
mail("OnlyOoo", "received", D(2026, 7, 1, 1), cls="out_of_office")
# 7. A genuine reply 5 days ago -> we owe them.
company("RealReply");         mail("RealReply", "sent", D(2026, 8, 1))
mail("RealReply", "received", D(2026, 8, 7))
# 8. Genuine reply but parked -> never nag.
company("Parked", bucket="declined_close"); mail("Parked", "sent", D(2026, 8, 1))
mail("Parked", "received", D(2026, 8, 7))
# 9. Test row is exempt.
company("TestCo", source="Internal Test"); mail("TestCo", "sent", D(2026, 7, 1))
# 10. Genuine reply, then an OOO after it. Still "we owe" from the real reply,
#     and the OOO must not reset the clock to itself.
company("ReplyThenOoo", ooo="2026-09-15"); mail("ReplyThenOoo", "sent", D(2026, 8, 1))
mail("ReplyThenOoo", "received", D(2026, 8, 5))
mail("ReplyThenOoo", "received", D(2026, 8, 9), cls="out_of_office")

rows = {r[0]: {"type": r[1], "threshold": r[2], "due_on": r[3]} for r in db.execute(SQL).fetchall()}

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Who appears in the queue ──")
chk("PlainOverdue shows", "PlainOverdue" in rows, True)
chk("PlainTooSoon hidden (only 5 days)", "PlainTooSoon" in rows, False)
chk("LongLeave hidden until they are back", "LongLeave" in rows, False)
chk("ShortLeave hidden (14-day floor not reached)", "ShortLeave" in rows, False)
chk("LeaveOver shows (leave finished, past 14 days)", "LeaveOver" in rows, True)
chk("OnlyOoo hidden (away until Sept)", "OnlyOoo" in rows, False)
chk("RealReply shows", "RealReply" in rows, True)
chk("Parked never nags", "Parked" in rows, False)
chk("TestCo exempt", "TestCo" in rows, False)
chk("ReplyThenOoo shows", "ReplyThenOoo" in rows, True)

print()
print("── An autoresponder is not us owing a reply ──")
chk("OnlyOoo is not we_owe_reply", rows.get("OnlyOoo", {}).get("type"), None)
chk("RealReply is we_owe_reply", rows["RealReply"]["type"], "we_owe_reply")
chk("ReplyThenOoo owes from the REAL reply", rows["ReplyThenOoo"]["type"], "we_owe_reply")
chk("PlainOverdue is waiting_on_them", rows["PlainOverdue"]["type"], "waiting_on_them")

print()
print("── Threshold matches the agreed rule ──")
chk("PlainOverdue threshold is the plain 14", rows["PlainOverdue"]["threshold"], 14)
chk("LeaveOver threshold is 14 (stale OOO ignored)", rows["LeaveOver"]["threshold"], 14)

print()
print("── SQL agrees with followup_due_date() in Python ──")
cases = [
    ("no OOO",        date(2026, 8, 1), None),
    ("short leave",   date(2026, 8, 1), date(2026, 8, 5)),
    ("exactly 14",    date(2026, 8, 1), date(2026, 8, 15)),
    ("long leave",    date(2026, 8, 1), date(2026, 9, 15)),
    ("very long",     date(2026, 8, 1), date(2027, 1, 5)),
    ("stale OOO",     date(2026, 8, 1), date(2026, 7, 1)),
]
for label, sent, ooo in cases:
    py = followup_due_date(sent, ooo)
    lit = "NULL" if not ooo else repr(str(ooo))
    sql_due = db.execute(
        f"""SELECT CAST(
              CASE WHEN TRY_CAST({lit} AS DATE) IS NOT NULL
                        AND DATE_DIFF('day', DATE '{sent}', TRY_CAST({lit} AS DATE)) > {DAYS}
                   THEN CAST(TRY_CAST({lit} AS DATE) + INTERVAL 1 DAY AS TIMESTAMP)
                   ELSE TIMESTAMP '{sent} 00:00:00' + INTERVAL {DAYS} DAY
              END AS DATE)""").fetchone()[0]
    chk(f"{label}: SQL == Python", sql_due, py)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
