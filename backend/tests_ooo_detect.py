#!/usr/bin/env python3
"""
Tests for services/ooo_detect.py — autoresponder detection, return-date
reading, and the agreed follow-up reminder arithmetic.

AI fallback is disabled throughout (allow_ai=False) so these are pure and free.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.ooo_detect import (  # noqa: E402
    BASE_FOLLOWUP_DAYS, detect, followup_days, followup_due_date,
    is_auto_reply, parse_return_date,
)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


REF = date(2026, 8, 12)          # a Wednesday

print("── Is it an autoresponder? ──")
chk("Outlook 'Automatic reply' subject",
    is_auto_reply("Automatic reply: Introduction", "I am away."), True)
chk("Re: prefix still detected",
    is_auto_reply("Re: Automatic reply: Introduction", ""), True)
chk("out of office in subject",
    is_auto_reply("Out of Office", ""), True)
chk("annual leave in body",
    is_auto_reply("Re: Introduction", "Thanks - I'm on annual leave right now."), True)
chk("RFC-3834 header",
    is_auto_reply("Re: Introduction", "hello", headers="Auto-Submitted: auto-replied"), True)
chk("German abwesenheit",
    is_auto_reply("Abwesenheitsnotiz", ""), True)
# The important negatives: a real founder reply must never be swallowed.
chk("genuine interested reply is NOT auto",
    is_auto_reply("Re: Introduction", "Thanks for reaching out, happy to chat next week."), False)
chk("genuine decline is NOT auto",
    is_auto_reply("Re: Introduction", "We're not looking to raise or sell right now."), False)
chk("mentions a meeting, not an autoresponder",
    is_auto_reply("Re: Introduction", "I'm in the office Monday if you want to call."), False)

print()
print("── Reading the return date ──")
chk("until 15 September -> back the 16th",
    parse_return_date("Automatic reply", "I am out of office until 15 September.", REF),
    date(2026, 9, 16))
chk("back on 3rd October -> that day",
    parse_return_date("Automatic reply", "I'll be back on 3rd October.", REF),
    date(2026, 10, 3))
chk("returning 2026-09-01",
    parse_return_date("Automatic reply", "Away, returning 2026-09-01.", REF),
    date(2026, 9, 1))
chk("UK numeric 01/09/2026 day-first",
    parse_return_date("Automatic reply", "Back on 01/09/2026.", REF),
    date(2026, 9, 1))
chk("until Friday (from Wed 12 Aug) -> Sat 15th",
    parse_return_date("Automatic reply", "Out of office until Friday.", REF),
    date(2026, 8, 15))
chk("back Monday (from Wed 12 Aug) -> Mon 17th",
    parse_return_date("Automatic reply", "I'm back Monday.", REF),
    date(2026, 8, 17))
chk("month-day order: until September 20",
    parse_return_date("Automatic reply", "On leave until September 20.", REF),
    date(2026, 9, 21))
chk("no year stated rolls forward, never into the past",
    parse_return_date("Automatic reply", "Back on 5 January.", REF),
    date(2027, 1, 5))
chk("no date at all -> None (never invent one)",
    parse_return_date("Automatic reply", "I am currently away with limited access to email.", REF),
    None)
chk("ignores an unrelated signature phone number",
    parse_return_date("Automatic reply", "Out of office until 20 August. Tel 020 7946 0958.", REF),
    date(2026, 8, 21))

print()
print("── detect() end to end ──")
d = detect("Automatic reply: Intro", "I'm on annual leave until 30 September.",
           received_on=REF, allow_ai=False)
chk("detect flags OOO", d["is_ooo"], True)
chk("detect reads the date", d["until"], date(2026, 10, 1))
chk("detect records the source", d["date_source"], "pattern")

d2 = detect("Re: Intro", "Happy to talk, when suits?", received_on=REF, allow_ai=False)
chk("genuine reply is not OOO", d2["is_ooo"], False)
chk("genuine reply has no date", d2["until"], None)

d3 = detect("Automatic reply", "Away with limited access to email.",
            received_on=REF, allow_ai=False)
chk("OOO with no readable date still flagged", d3["is_ooo"], True)
chk("...but carries no date", d3["until"], None)
chk("...and says so via an empty source", d3["date_source"], "")

print()
print("── The reminder rule ──")
SENT = date(2026, 8, 1)
chk("no OOO -> plain 14 days", followup_days(SENT, None), 14)
chk("no OOO -> due 15 Aug", followup_due_date(SENT, None), date(2026, 8, 15))
# Back inside the 14-day window: the 14-day floor wins.
chk("back in 3 days -> still 14", followup_days(SENT, date(2026, 8, 4)), 14)
chk("back in 14 days exactly -> still 14", followup_days(SENT, date(2026, 8, 15)), 14)
# Exactly at the boundary: length must be STRICTLY greater than 14 to override.
chk("length 14 does not override", followup_days(SENT, SENT.replace(day=15)), 14)
# Longer than 14 days away: reminder moves to length + 1.
chk("back in 20 days -> 21", followup_days(SENT, date(2026, 8, 21)), 21)
chk("back in 20 days -> due the day after they return",
    followup_due_date(SENT, date(2026, 8, 21)), date(2026, 8, 22))
chk("back in 45 days -> 46", followup_days(SENT, date(2026, 9, 15)), 46)
chk("long leave due date", followup_due_date(SENT, date(2026, 9, 15)), date(2026, 9, 16))
# A return date already in the past must never pull the reminder earlier.
chk("stale OOO date never shortens the wait", followup_days(SENT, date(2026, 7, 1)), 14)
chk("never earlier than the 14-day floor",
    followup_due_date(SENT, date(2026, 7, 1)) >= SENT.replace(day=15), True)
chk("minimum is always 14", min(followup_days(SENT, date(2026, 8, d)) for d in range(2, 16)),
    BASE_FOLLOWUP_DAYS)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
