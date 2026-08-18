#!/usr/bin/env python3
"""
Thread matching (the ReadyGo fix): a reply from an unknown domain is matched by
its In-Reply-To/References headers naming a Message-ID we logged.

Found in production: ReadyGo's founder replied from a different domain than the
one we wrote to. Address matching cannot see such a reply by design (matching
arbitrary domains would mis-attribute mail), so the company sat in Contacted
looking unanswered for a month. The reply's own headers named our message the
whole time.
"""
import email
import inspect
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services.email_sync_service import _thread_refs  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


def msg(headers: str):
    return email.message_from_string(headers + "\n\nbody")


print("── Reading the reply headers ──")
chk("In-Reply-To alone",
    _thread_refs(msg("In-Reply-To: <abc@mail.gmail.com>")), ["<abc@mail.gmail.com>"])
chk("References alone, several ids",
    _thread_refs(msg("References: <root@x> <mid@x> <last@x>")),
    ["<root@x>", "<mid@x>", "<last@x>"])
chk("both headers, deduplicated",
    _thread_refs(msg("In-Reply-To: <last@x>\nReferences: <root@x> <last@x>")),
    ["<last@x>", "<root@x>"])
chk("no headers -> empty", _thread_refs(msg("Subject: hi")), [])
chk("malformed tokens are skipped",
    _thread_refs(msg("References: garbage <ok@x> not-an-id")), ["<ok@x>"])

print()
print("── The matching ladder is wired correctly ──")
from services.email_sync_service import _fetch_folder, sync_mailbox  # noqa: E402

src = inspect.getsource(_fetch_folder)
chk("thread match is the THIRD rung (only when address and domain fail)",
    'if not entity and thread_map and direction == "received"' in src, True)
chk("only received messages thread-match (our own sends already carry the entity)",
    'direction == "received"' in src, True)
chk("entries say how they matched", '"matched_by": matched_by' in src, True)
chk("sync_mailbox passes the map through",
    "thread_map" in inspect.getsource(sync_mailbox), True)

print()
print("── The adoption guard (never a third party) ──")
import main  # noqa: E402

sync_src = inspect.getsource(main.sync_emails)
chk("adoption considers ONLY thread-matched replies",
    'r.get("matched_by") != "thread"' in sync_src, True)
chk("autoresponders and bounces prove nothing about a person",
    "NON_REPLY_CLASSES" in sync_src.split("Cross-domain contact adoption")[1][:1500], True)
chk("relation must be 'company', anything else does not adopt",
    'if relation != "company":' in sync_src, True)
chk("a declined adoption is still logged for the record",
    "was NOT changed" in sync_src, True)
chk("an adoption writes an activity note naming the new address",
    "Contact adopted from their reply" in sync_src, True)

from storage.bq_handler import BigQueryHandler  # noqa: E402
gm = inspect.getsource(BigQueryHandler.get_message_id_entity_map)
chk("thread map holds only real Message-IDs", "LIKE '<%'" in gm, True)

from services.email_sync_service import assess_sender_relation  # noqa: E402
chk("no API key / no text -> 'unknown', never a guess",
    assess_sender_relation("Acme", "", "x@y.com", "", ""), "unknown")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
