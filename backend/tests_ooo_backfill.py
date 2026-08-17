#!/usr/bin/env python3
"""
Tests the retro out-of-office backfill decision logic (_ooo_backfill in main.py)
against a stubbed handler, in dry-run so nothing is written and no AI is spent.

Covers the rules that make a retro pass safe to run over live data:
  * a genuine reply is never mistaken for an autoresponder
  * the newest OOO wins when a company sent several
  * an OOO SUPERSEDED by a later genuine reply does not set ooo_until
  * a company with a genuine reply keeps its stage and its reply state
  * an OOO with no readable date still corrects the stage, but keeps 14 days
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


class StubHandler:
    """Newest-first, exactly as get_received_log() orders."""

    def get_received_log(self, limit=5000):
        return [
            # LongLeave: two autoresponders, the newest is the Sept one.
            {"message_id": "m6", "entity_name": "LongLeave", "subject": "Automatic reply",
             "snippet": "I am out of office until 30 September.", "classification": "other",
             "sent_at": "2026-08-01T09:00:00"},
            {"message_id": "m5", "entity_name": "LongLeave", "subject": "Automatic reply",
             "snippet": "Away until 5 August.", "classification": "other",
             "sent_at": "2026-07-01T09:00:00"},
            # RepliedLater: an OOO, then a REAL reply after it. OOO is superseded.
            {"message_id": "m4", "entity_name": "RepliedLater", "subject": "Re: Intro",
             "snippet": "Happy to chat next week.", "classification": "interested",
             "sent_at": "2026-07-20T09:00:00"},
            {"message_id": "m3", "entity_name": "RepliedLater", "subject": "Automatic reply",
             "snippet": "Out of office until 20 August.", "classification": "other",
             "sent_at": "2026-07-10T09:00:00"},
            # NoDate: an OOO with nothing parseable in it.
            {"message_id": "m2", "entity_name": "NoDate", "subject": "Automatic reply",
             "snippet": "Away with limited access to email.", "classification": "",
             "sent_at": "2026-08-05T09:00:00"},
            # GenuineOnly: a real reply, never an autoresponder.
            {"message_id": "m1", "entity_name": "GenuineOnly", "subject": "Re: Intro",
             "snippet": "Not interested thanks.", "classification": "declined",
             "sent_at": "2026-08-02T09:00:00"},
            # TestCo must be exempt from the whole pass.
            {"message_id": "m0", "entity_name": "TestCo", "subject": "Automatic reply",
             "snippet": "Out of office until 30 September.", "classification": "",
             "sent_at": "2026-08-01T09:00:00"},
        ]

    def genuine_reply_names(self):
        return {"RepliedLater", "GenuineOnly"}

    def get_universe_slim(self):
        return [
            {"name": "LongLeave", "status": "Responded", "outreach_sent_at": "2026-08-01", "source": "Conf"},
            {"name": "RepliedLater", "status": "Responded", "outreach_sent_at": "2026-07-01", "source": "Conf"},
            {"name": "NoDate", "status": "Responded", "outreach_sent_at": "2026-08-05", "source": "Conf"},
            {"name": "GenuineOnly", "status": "Responded", "outreach_sent_at": "2026-08-01", "source": "Conf"},
            {"name": "TestCo", "status": "Responded", "outreach_sent_at": "2026-08-01", "source": "Internal Test"},
        ]

    def reconcile_reply_stages(self, dry_run=False, confirm_names=None):
        return {"promote": [], "demote": [], "needs_confirmation": []}


main.bq_handler = StubHandler()
res = main._ooo_backfill(dry_run=True, ai_budget=0, limit=5000)
by = {c["name"]: c for c in res["companies"]}

print("── Detection ──")
chk("scanned every stored inbound", res["scanned_messages"], 7)
# 5 autoresponders: LongLeave x2, RepliedLater x1, NoDate x1, TestCo x1.
chk("found every autoresponder", res["autoresponders_found"], 5)
chk("a genuine reply is never treated as OOO", "GenuineOnly" in by, False)
# The test row's LOG entry is still marked — that is a fact about the message,
# and marking it keeps it from ever counting as a reply. The exemption applies
# to changing the COMPANY, which is asserted further down.
chk("marks every unmarked autoresponder row", res["messages_to_mark"], 5)

print()
print("── Which OOO counts ──")
chk("newest OOO wins: back 1 Oct, not 6 Aug", by["LongLeave"]["ooo_until"], "2026-10-01")
chk("an OOO superseded by a real reply sets no date",
    "RepliedLater" in by, False)
chk("OOO with no readable date carries none", by["NoDate"]["ooo_until"], "")
chk("...and keeps the plain 14-day rule", by["NoDate"]["reminder_days"], None)
chk("test row is exempt", "TestCo" in by, False)

print()
print("── Reminder arithmetic ──")
# Our email 2026-08-01, they are back 2026-10-01: length 61 -> remind at 62.
chk("long leave defers to length + 1", by["LongLeave"]["reminder_days"], 62)
chk("counts the deferred reminders", res["reminder_deferred"], 1)

print()
print("── Stage correction ──")
chk("no genuine reply -> pulled back to Contacted", by["LongLeave"]["pull_back_to"], "Contacted")
chk("undated OOO still corrects the stage", by["NoDate"]["pull_back_to"], "Contacted")
chk("counts the pull-backs", res["would_pull_back"], 2)

print()
print("── A preview must be inert ──")
chk("reports as dry run", res["dry_run"], True)
chk("spends no AI", res["ai_calls_used"], 0)
chk("says nothing was changed", "Nothing was changed" in res["message"], True)
chk("status is Preview", res["status"], "Preview")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
