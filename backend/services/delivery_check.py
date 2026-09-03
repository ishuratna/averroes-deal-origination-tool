"""
Did the outreach email actually reach a human?

Two failure modes, both of which must pull the company back out of Contacted:

  1. BOUNCE / auto-reject — the message came back. The address does not exist,
     the mailbox is full, the domain does not resolve, or a gateway refused it.
  2. NEVER SENT — no outbound message for this company exists in the mailbox at
     all, so the SMTP call reported success but nothing was filed in Sent.

Pure functions with no IMAP and no BigQuery, so every pattern below is testable
directly. bq_handler.verify_delivery() is the only caller.

WHY PATTERNS AND NOT AI: a bounce is a machine-generated message in a small
number of standard shapes (RFC 3464 delivery status notifications, plus each
provider's prose). Patterns are free, instant and deterministic. An AI call here
would cost money on every sync to answer a question the headers already answer.

CRITICAL DISTINCTION: a bounce must never be confused with an out-of-office.
Both are automated, both arrive straight after we write, and both are ABOUT our
message rather than a reply to it. But they mean opposite things:

    out-of-office -> the address is GOOD, the human is away. Stay in Contacted
                     and defer the reminder (see ooo_detect.py).
    bounce        -> the address is DEAD. Go back to Qualified and find a new one.

Get that backwards and you either bin a live prospect or keep emailing a dead
mailbox forever. is_bounce() therefore checks for out-of-office markers FIRST and
refuses to call anything a bounce if they are present.
"""
import re
from typing import Optional

# Addresses that only ever send machine reports. A message from one of these is
# about delivery, never a reply from a founder.
_DAEMON_ADDRESSES = (
    "mailer-daemon", "postmaster", "mail-daemon", "no-reply-delivery",
    "bounce", "bounces", "mdaemon", "returns",
)

# Subject lines providers use for a failed delivery. Kept as substrings because
# each provider decorates them differently ("Undelivered Mail Returned to
# Sender", "Mail delivery failed: returning message to sender", ...).
_BOUNCE_SUBJECTS = (
    "undelivered mail returned to sender",
    "undeliverable",
    "delivery status notification (failure)",
    "delivery status notification failure",
    "mail delivery failed",
    "mail delivery subsystem",
    "returned mail",
    "failure notice",
    "delivery failure",
    "message not delivered",
    "address not found",
    "delivery incomplete",
    "could not be delivered",
    "unable to deliver",
    "rejected",
)

# Body phrases. Deliberately specific: "not found" or "failed" alone would fire
# on ordinary founder replies discussing their own business.
_BOUNCE_PHRASES = (
    "address not found",
    "recipient address rejected",
    "user unknown",
    "no such user",
    "no such recipient",
    "mailbox unavailable",
    "mailbox is full",
    "mailbox full",
    "quota exceeded",
    "does not exist",
    "couldn't be delivered",
    "could not be delivered",
    "wasn't delivered",
    "was not delivered",
    "delivery to the following recipient failed",
    "delivery to the following recipients failed",
    "permanent error",
    "permanently failed",
    "domain not found",
    "unrouteable address",
    "relay access denied",
    "message blocked",
    "blocked it as spam",
    "552 ",
    "553 ",
    "550 ",
    "554 ",
)

# RFC 3464 / RFC 3834 headers. The most reliable signal there is: a real DSN
# carries a report content type, and 'failed' is the machine's own verdict.
_DSN_HEADER_HINTS = (
    "content-type: multipart/report",
    "report-type=delivery-status",
    "message/delivery-status",
    "auto-submitted: auto-replied",
)

# 5.x.x = PERMANENT failure (the address is wrong). 4.x.x = temporary (server
# busy, greylisted) and must NOT pull a company back — the mail may yet arrive.
_PERMANENT_STATUS = re.compile(r"\b5\.\d{1,3}\.\d{1,3}\b")
_TEMPORARY_STATUS = re.compile(r"\b4\.\d{1,3}\.\d{1,3}\b")

# Out-of-office markers. If any of these appear, the mailbox is alive and this is
# NOT a bounce, whatever else the text says.
_OOO_MARKERS = (
    "out of office", "out-of-office", "automatic reply", "autoreply",
    "auto-reply", "annual leave", "on leave", "on holiday", "on vacation",
    "away from the office", "abwesenheitsnotiz", "maternity leave",
    "paternity leave", "parental leave", "limited access to email",
    "i am away", "i'm away", "currently away",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def is_bounce(subject: str = "", snippet: str = "", headers: str = "",
              from_addr: str = "") -> bool:
    """True only when this message says our email could not be delivered.

    Conservative by design. A false positive throws a real prospect back to
    Qualified and wipes a working address, so every branch below needs a
    delivery-specific signal, not merely an automated-looking message.
    """
    subj, body, head = _norm(subject), _norm(snippet), _norm(headers)
    sender = _norm(from_addr)
    blob = f"{subj} {body}"

    # An out-of-office is never a bounce. Checked FIRST so no later pattern can
    # override it: "I am on annual leave, your message could not be dealt with"
    # must stay an out-of-office.
    if any(m in blob for m in _OOO_MARKERS):
        return False

    # A 4.x.x temporary failure is not a verdict yet. Never pull back on one.
    if _TEMPORARY_STATUS.search(blob) and not _PERMANENT_STATUS.search(blob):
        return False

    from_daemon = any(d in sender for d in _DAEMON_ADDRESSES)
    dsn_header = any(h in head for h in _DSN_HEADER_HINTS)
    bad_subject = any(s in subj for s in _BOUNCE_SUBJECTS)
    bad_body = any(p in body for p in _BOUNCE_PHRASES)
    permanent = bool(_PERMANENT_STATUS.search(blob))

    # A daemon address alone is not enough: mailer-daemon also sends
    # vacation-forwarding notices and quota warnings about OUR mailbox. Pair it
    # with something that speaks to delivery.
    if from_daemon and (bad_subject or bad_body or permanent or dsn_header):
        return True
    # A genuine DSN report naming a permanent failure.
    if dsn_header and (permanent or bad_body or bad_subject):
        return True
    # Provider prose with no useful headers (common with Gmail's own notices).
    if bad_subject and (bad_body or permanent):
        return True
    # An explicit permanent SMTP status plus delivery language.
    if permanent and bad_body:
        return True
    return False


def bounced_address(snippet: str = "", headers: str = "",
                    exclude: str = "") -> Optional[str]:
    """The address that failed, so the dead contact can be cleared.

    Prefers the RFC 3464 'Final-Recipient' / 'Original-Recipient' field, which is
    the machine's own statement of who could not be reached. Falls back to the
    first address in the body that is not ours.

    `exclude` is our own sending address: a bounce report quotes it as the
    sender, and clearing that would be catastrophic.
    """
    mine = _norm(exclude)
    text = f"{headers or ''}\n{snippet or ''}"

    for field in ("final-recipient", "original-recipient", "x-failed-recipients"):
        m = re.search(rf"{field}\s*:\s*(?:rfc822\s*;\s*)?<?([\w.+-]+@[\w.-]+)>?",
                      text, re.I)
        if m:
            found = m.group(1).strip().lower()
            if found and found != mine:
                return found

    for cand in _EMAIL_RE.findall(snippet or ""):
        c = cand.strip().lower().rstrip(".,;:)")
        if c == mine:
            continue
        # Skip the provider's own support and daemon addresses.
        if any(d in c for d in _DAEMON_ADDRESSES):
            continue
        if c.endswith(("@google.com", "@gmail-smtp-in.l.google.com")):
            continue
        return c
    return None


def classify_delivery(subject: str = "", snippet: str = "", headers: str = "",
                      from_addr: str = "", our_address: str = "") -> dict:
    """One call for the sync: is this a bounce, and if so which address died?

    Returns {"is_bounce": bool, "address": str|None, "reason": str}.
    """
    if not is_bounce(subject, snippet, headers, from_addr):
        return {"is_bounce": False, "address": None, "reason": ""}
    addr = bounced_address(snippet, headers, exclude=our_address)
    blob = _norm(f"{subject} {snippet}")
    reason = next((p.strip() for p in _BOUNCE_PHRASES if p in blob), "")
    if not reason:
        m = _PERMANENT_STATUS.search(blob)
        reason = f"SMTP status {m.group(0)}" if m else "delivery failure reported"
    return {"is_bounce": True, "address": addr, "reason": reason}


def bounce_superseded(bounce_at: str, latest_send_at: str) -> bool:
    """Is this bounce stale history? True when we RE-SENT after it arrived.

    The Cezanne HR rule (28 Aug 2026): a bounce invalidates only the send it
    bounced against. A newer outbound send - usually to a corrected address -
    takes precedence, and its own fate (bounce / reply / not-sent) decides the
    stage. Timestamps are BigQuery CAST(... AS STRING) ISO forms, which order
    correctly as strings; missing values fail safe (not superseded).
    """
    b, s = str(bounce_at or ""), str(latest_send_at or "")
    return bool(b) and bool(s) and s > b
