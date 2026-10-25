"""A bounce (or reply) that arrives minutes after our send must thread-match
IN THE SAME SCAN as the send (Oxford Semantic Technologies, 25 Oct 2026).
Run: python3 tests_sync_same_scan_thread.py"""
from email.message import EmailMessage

from services.email_sync_service import _fetch_folder

OUR = "beatrice@averroescapital.com"


def _raw(msg: EmailMessage) -> bytes:
    return msg.as_bytes()


class FakeMail:
    """Two messages: seq 1 = our send (older), seq 2 = the bounce (newer)."""

    def __init__(self):
        send = EmailMessage()
        send["From"] = f"Beatrice <{OUR}>"
        send["To"] = "p.crocker@oxfordsemantic.tech"
        send["Subject"] = "Averroes Capital, Oxford Semantic Technologies"
        send["Message-ID"] = "<send-123@averroescapital.com>"
        send["Date"] = "Sat, 25 Oct 2026 20:18:09 +0000"
        send.set_content("Hello")

        bounce = EmailMessage()
        bounce["From"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"
        bounce["To"] = OUR
        bounce["Subject"] = "Delivery Status Notification (Failure)"
        bounce["Message-ID"] = "<bounce-456@mail.gmail.com>"
        bounce["In-Reply-To"] = "<send-123@averroescapital.com>"
        bounce["References"] = "<send-123@averroescapital.com>"
        bounce["Date"] = "Sat, 25 Oct 2026 20:19:02 +0000"
        bounce.set_content("Address not found: p.crocker@oxfordsemantic.tech")
        self.msgs = {b"1": _raw(send), b"2": _raw(bounce)}

    def select(self, folder, readonly=True):
        return "OK", None

    def search(self, charset, query):
        return "OK", [b"1 2"]

    def fetch(self, ids, what):
        if what.startswith("(BODY.PEEK"):
            out = []
            for i in ids.split(b","):
                raw = self.msgs[i]
                mid = [l for l in raw.split(b"\n") if l.lower().startswith(b"message-id")][0]
                out.append((i + b" (BODY[HEADER.FIELDS (MESSAGE-ID)] {0}", mid + b"\r\n"))
            return "OK", out
        return "OK", [(ids + b" (RFC822 {0}", self.msgs[ids])]


def test_bounce_links_to_send_in_same_scan():
    known = {"p.crocker@oxfordsemantic.tech": {"type": "company", "name": "Oxford Semantic Technologies"}}
    # thread_map from BigQuery is EMPTY: this is the first sync after the send
    entries = _fetch_folder(FakeMail(), "[Gmail]/All Mail", "01-Oct-2026", OUR, known,
                            known_domains={}, thread_map={}, skip_ids=set())
    by_dir = {e["direction"]: e for e in entries}
    assert "sent" in by_dir, "our send must be filed"
    assert "received" in by_dir, "the bounce must be filed in the same scan"
    b = by_dir["received"]
    assert b["entity_name"] == "Oxford Semantic Technologies"
    assert b["matched_by"] == "thread", b["matched_by"]
    assert b["counterparty_email"] == "mailer-daemon@googlemail.com"


def test_oldest_first_order():
    known = {"p.crocker@oxfordsemantic.tech": {"type": "company", "name": "Oxford Semantic Technologies"}}
    entries = _fetch_folder(FakeMail(), "[Gmail]/All Mail", "01-Oct-2026", OUR, known,
                            known_domains={}, thread_map=None, skip_ids=None)
    assert [e["direction"] for e in entries] == ["sent", "received"]


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"ok   {name}")
            except Exception as e:
                fails += 1; print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)
