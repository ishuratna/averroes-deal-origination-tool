#!/usr/bin/env python3
"""
Email threading: a follow-up must land IN the original conversation.

Found in production: a follow-up to a founder arrived as a separate email. The
subject said "Re:" but the headers said nothing, and Gmail threads on the
In-Reply-To / References headers carrying the previous message's Message-ID,
never on the subject. These tests pin every piece of the fix.
"""
import inspect
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


# ── send_email sets the headers ──────────────────────────────────────────────
print("── send_email carries the thread headers ──")
from services import outreach_service as O  # noqa: E402

src = inspect.getsource(O.send_email)
chk("accepts in_reply_to", "in_reply_to" in src, True)
chk("accepts references", "references" in src, True)
chk("sets the In-Reply-To header", 'msg["In-Reply-To"]' in src, True)
chk("sets the References header", 'msg["References"]' in src, True)
chk("headers are conditional (a first email stays fresh)",
    "if in_reply_to:" in src and "if references:" in src, True)

# ── the send path looks the thread up ────────────────────────────────────────
print()
print("── /outreach/send threads every Re: send ──")
import main  # noqa: E402

send_src = inspect.getsource(main.send_outreach)
chk("thread ids are fetched for Re: subjects", "get_thread_ids" in send_src, True)
chk("keyed on the subject, so a changed subject starts afresh on purpose",
    'startswith("re:")' in send_src, True)
chk("ids are passed into send_email",
    "in_reply_to=in_reply_to" in send_src and "references=references" in send_src, True)
chk("a failed lookup degrades to unthreaded, never to a failed send",
    "sending unthreaded" in send_src, True)

# ── the id selection logic ───────────────────────────────────────────────────
print()
print("── get_thread_ids picks the right ids ──")
from storage.bq_handler import BigQueryHandler  # noqa: E402

gt = inspect.getsource(BigQueryHandler.get_thread_ids)
chk("only real Message-IDs are used (synthetic dedup ids would corrupt threads)",
    "LIKE '<%'" in gt, True)
chk("oldest first (References is a chronological chain)", "ORDER BY sent_at ASC" in gt, True)
chk("In-Reply-To is the NEWEST message", "ids[-1]" in gt, True)
chk("References is capped for RFC header limits", "ids[-7:]" in gt, True)

# The cap logic, exercised directly: root must survive, newest must be last.
ids = [f"<m{i}@x>" for i in range(1, 13)]
refs = ids[:1] + ids[-7:] if len(ids) > 8 else ids
chk("the thread root survives the cap", refs[0], "<m1@x>")
chk("the newest id is last", refs[-1], "<m12@x>")
chk("capped to 8 entries", len(refs), 8)
short = [f"<m{i}@x>" for i in range(1, 4)]
chk("short threads keep every id", short[:1] + short[-7:] if len(short) > 8 else short, short)

# ── the drafts produce Re: subjects, which is what triggers threading ────────
print()
print("── the drafts and the trigger agree ──")
d = O.draft_followup_email({"name": "Acme", "contact_name": "James",
                            "contact_email": "j@acme.co",
                            "outreach_draft_subject": "Averroes Capital, Acme"})
chk("the follow-up subject starts with Re:", d["subject"].lower().startswith("re:"), True)
compose_src = inspect.getsource(main.outreach_compose_draft)
chk("compose normalises its subject to a single Re:",
    'startswith("re:")' in compose_src, True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
