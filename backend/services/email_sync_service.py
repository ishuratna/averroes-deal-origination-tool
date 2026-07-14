"""
Email communications log - syncs Beatrice's Gmail via IMAP.

Uses the SAME App Password as SMTP sending (no new auth). Reads INBOX and
Sent Mail, keeps ONLY messages exchanged with known contacts (companies and
LPs in our database), and returns structured entries for the email_log table.

Received replies are classified with Gemini (ungrounded, ~0.5p each):
interested / not_now / declined / question / other.
"""
import os
import email
import email.utils
import imaplib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"


def _decode(value) -> str:
    if value is None:
        return ""
    try:
        parts = email.header.decode_header(value)
        out = ""
        for text, enc in parts:
            out += text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text
        return out
    except Exception:
        return str(value)


def _strip_html(html: str) -> str:
    """Crude but effective: HTML → readable text."""
    import re
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _body_snippet(msg, limit: int = 500) -> str:
    """
    Extract a text snippet from an email message.
    Prefers text/plain; falls back to stripped text/html — HTML-only replies
    (common from Outlook/branded clients) must still yield text, otherwise
    the AI classifier has nothing to read and the reply stays unclassified.
    """
    html_fallback = ""
    try:
        parts = msg.walk() if msg.is_multipart() else [msg]
        for part in parts:
            ctype = part.get_content_type()
            if "attachment" in str(part.get("Content-Disposition", "")):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/plain":
                return decoded[:limit].strip()
            if ctype == "text/html" and not html_fallback:
                html_fallback = _strip_html(decoded)
    except Exception:
        pass
    return html_fallback[:limit].strip()


def _fetch_folder(mail, folder: str, since: str, sender: str, known: Dict[str, dict]) -> List[dict]:
    """
    Fetch messages from one folder, keeping only known-contact exchanges.
    Direction is detected PER MESSAGE (From == our sender → 'sent', else
    'received') so scanning [Gmail]/All Mail catches replies that were
    archived or filtered out of the INBOX — those are otherwise invisible.
    """
    entries = []
    sender = (sender or "").lower()
    try:
        status, _ = mail.select(f'"{folder}"', readonly=True)
        if status != "OK":
            logger.warning(f"[EmailSync] Could not open folder {folder}")
            return []
        status, data = mail.search(None, f"(SINCE {since})")
        if status != "OK":
            return []
        ids = data[0].split()
        # Most recent first, bounded to keep runs fast
        for msg_id in list(reversed(ids))[:500]:
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            _, from_email = email.utils.parseaddr(msg.get("From") or "")
            direction = "sent" if (from_email or "").lower() == sender else "received"

            # Counterparty: To for sent, From for received
            raw_addr = msg.get("To") if direction == "sent" else msg.get("From")
            counter_name, counter_email = email.utils.parseaddr(raw_addr or "")
            counter_email = (counter_email or "").lower()
            if counter_email not in known:
                continue

            date_hdr = msg.get("Date")
            try:
                sent_at = email.utils.parsedate_to_datetime(date_hdr).astimezone(timezone.utc).isoformat()
            except Exception:
                sent_at = datetime.now(timezone.utc).isoformat()

            entity = known[counter_email]
            entries.append({
                "message_id": _decode(msg.get("Message-ID")) or f"{folder}-{msg_id.decode()}",
                "thread_id": _decode(msg.get("References", "")).split()[0] if msg.get("References") else _decode(msg.get("Message-ID")) or "",
                "direction": direction,
                "counterparty_email": counter_email,
                "counterparty_name": _decode(counter_name),
                "entity_type": entity["type"],
                "entity_name": entity["name"],
                "subject": _decode(msg.get("Subject")),
                "snippet": _body_snippet(msg),
                "sent_at": sent_at,
            })
    except Exception as e:
        logger.error(f"[EmailSync] Folder {folder} failed: {e}")
    return entries


def sync_mailbox(known_contacts: Dict[str, dict], days: int = 30) -> List[dict]:
    """
    Read Beatrice's mailbox (IMAP, same App Password as sending) and return
    entries for messages exchanged with known contacts only.
    known_contacts: {email_lower: {"type": "company"|"investor", "name": str}}
    """
    sender = os.getenv("OUTREACH_EMAIL", "beatrice@averroescapital.com")
    password = os.getenv("OUTREACH_SMTP_PASSWORD", "")
    if not password:
        raise RuntimeError("OUTREACH_SMTP_PASSWORD not configured (same App Password as sending)")

    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        mail.login(sender, password)
        # All Mail covers INBOX + Sent + archived + filtered/labelled mail in
        # one pass; direction is detected per message inside _fetch_folder.
        entries = _fetch_folder(mail, "[Gmail]/All Mail", since, sender, known_contacts)
        if not entries:
            # Fallback for non-Gmail IMAP layouts
            entries = _fetch_folder(mail, "INBOX", since, sender, known_contacts)
            entries += _fetch_folder(mail, "[Gmail]/Sent Mail", since, sender, known_contacts)
        # Dedup by message id (All Mail + fallback can overlap)
        seen_ids, unique = set(), []
        for e in entries:
            if e["message_id"] not in seen_ids:
                seen_ids.add(e["message_id"])
                unique.append(e)
        logger.info(f"[EmailSync] {len(unique)} known-contact messages found (last {days} days)")
        return unique
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def classify_reply(subject: str, snippet: str, entity_name: str) -> dict:
    """
    Classify a received reply with Gemini (ungrounded, cheap).
    Returns {"classification": ..., "summary": ...} or {} on failure.
    Classes: interested / not_now / declined / question / other
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not snippet:
        return {}
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)
        prompt = f"""An investor emailed the founder of "{entity_name}". This is the founder's reply.

Subject: {subject}
Reply text:
{snippet}

Classify the reply as exactly one of:
- "interested": open to a call/meeting or wants to engage
- "not_now": positive but timing is wrong / revisit later
- "declined": not interested
- "question": asking something before deciding
- "other": auto-reply, forward, unclear

Return ONLY valid JSON: {{"classification": "...", "summary": "one plain sentence on what they said"}}"""
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.1),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        result = json.loads(text[start:end + 1])
        if result.get("classification") in ("interested", "not_now", "declined", "question", "other"):
            return result
        return {}
    except Exception as e:
        logger.warning(f"[EmailSync] Classification failed: {e}")
        return {}
