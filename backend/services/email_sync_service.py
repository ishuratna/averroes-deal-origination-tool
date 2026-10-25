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
import re
import email.utils
import imaplib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from ai import gemini_client as _gemini_client

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


def _thread_refs(msg) -> List[str]:
    """Every Message-ID this message claims to be replying to, from its
    In-Reply-To and References headers. Pure parsing, testable directly."""
    refs = []
    for header in ("In-Reply-To", "References"):
        raw = _decode(msg.get(header) or "")
        for token in raw.split():
            token = token.strip()
            if token.startswith("<") and token.endswith(">") and token not in refs:
                refs.append(token)
    return refs


_MSGID_RE = re.compile(rb"Message-ID:\s*(<[^>]+>)", re.IGNORECASE)
_SEQ_RE = re.compile(rb"^(\d+) ")


def _prefetch_message_ids(mail, ids: List[bytes]) -> Dict[bytes, str]:
    """ONE round trip for the Message-ID header of every candidate, so the
    expensive RFC822 download happens only for mail not already in the log.
    Returns {seq_id: message_id}; ids whose header could not be read are
    simply absent (and therefore fetched in full)."""
    out: Dict[bytes, str] = {}
    if not ids:
        return out
    try:
        status, data = mail.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if status != "OK" or not data:
            return out
        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            m = _SEQ_RE.match(item[0] if isinstance(item[0], bytes) else b"")
            mid = _MSGID_RE.search(item[1] if isinstance(item[1], bytes) else b"")
            if m and mid:
                out[m.group(1)] = mid.group(1).decode("utf-8", "ignore").strip()
    except Exception as e:
        logger.warning(f"[EmailSync] Message-ID prefetch failed ({e}); falling back to full fetch")
    return out


def _invite_in(msg) -> Optional[dict]:
    """A calendar invitation, from an .ics part first, else the subject."""
    from services.thread_signals import invite_from_ics, invite_from_subject
    try:
        for part in msg.walk():
            if part.get_content_type() == "text/calendar":
                payload = part.get_payload(decode=True)
                got = invite_from_ics((payload or b"").decode("utf-8", "ignore"))
                if got:
                    return got
    except Exception:
        pass
    return invite_from_subject(_decode(msg.get("Subject")))


def _fetch_folder(mail, folder: str, since: str, sender: str, known: Dict[str, dict],
                  known_domains: Dict[str, dict] = None, addresses: List[str] = None,
                  max_fetch: int = 500, thread_map: Dict[str, dict] = None,
                  skip_ids: set = None, alias_index: dict = None) -> List[dict]:
    """
    Fetch messages from one folder, keeping only known-contact exchanges.
    Direction is detected PER MESSAGE (From == our sender → 'sent', else
    'received') so scanning [Gmail]/All Mail catches replies that were
    archived or filtered out of the INBOX — those are otherwise invisible.

    THREE matching rungs, in order:
      1. exact sender address (known)
      2. sender's domain (known_domains)
      3. THREAD: the message's In-Reply-To/References name a Message-ID we have
         logged (thread_map: message_id -> entity). This is what catches a
         founder replying from a DIFFERENT domain — a personal address, a
         parent company — which address matching can never see. Found in
         production: ReadyGo's reply came from a foreign domain and was
         invisible for a month. The reply headers are cryptographic-grade
         evidence of which conversation a message belongs to.
      4. ALIAS (services/thread_signals): a codename ("Project Rise") in the
         subject or body, or a known ADVISER writing a NEW chain that names
         the company or its codename. An adviser is the third party who
         appears on a company's thread ("Project Rise" means an adviser is
         involved, and the adviser sometimes starts a different chain: Ishu,
         24 Oct 2026). An adviser known for ONE company who names neither
         links as 'adviser-unconfirmed' (flagged, never moves a stage).
    Thread-matched entries carry matched_by='thread' so the caller can apply
    the third-party guard before adopting the sender as the contact.

    Two modes:
    - Default: scan the newest messages in the window (bounded by max_fetch
      across the WHOLE mailbox — fine for regular syncs, misses old history).
    - Deep (addresses given): one IMAP search PER known address/domain, so
      only relevant messages are fetched and the full exchange history is
      captured regardless of how busy the mailbox is.
    """
    entries = []
    sender = (sender or "").lower()
    try:
        status, _ = mail.select(f'"{folder}"', readonly=True)
        if status != "OK":
            logger.warning(f"[EmailSync] Could not open folder {folder}")
            return []
        if addresses:
            seen_ids, ids = set(), []
            for addr in addresses:
                try:
                    status, data = mail.search(None, f'(SINCE {since}) (OR FROM "{addr}" TO "{addr}")')
                    if status == "OK" and data and data[0]:
                        for i in data[0].split():
                            if i not in seen_ids:
                                seen_ids.add(i)
                                ids.append(i)
                except Exception:
                    continue
            ids.sort(key=int)
        else:
            status, data = mail.search(None, f"(SINCE {since})")
            if status != "OK":
                return []
            ids = data[0].split()
        # The NEWEST max_fetch messages are the window, but they are READ
        # OLDEST FIRST. The thread rung needs our own send in thread_map
        # before the message that answers it is read, and on the first sync
        # after a send only this scan holds that send. Oxford Semantic
        # Technologies (25 Oct 2026): the bounce arrived a minute after the
        # send, was read first, matched nothing (mailer-daemon is no known
        # address and our Message-ID was not yet logged) and was dropped,
        # so the delivery check saw a clean send and the card stayed in
        # Contacted with a dead address.
        candidates = list(reversed(ids))[:max_fetch]
        candidates.reverse()
        # In-run thread map: Message-IDs filed by THIS scan, so a bounce or
        # reply later in the same scan can thread-match. The caller's map is
        # extended in place and is a per-run dict (never persisted).
        if thread_map is None:
            thread_map = {}
        # Already-logged mail is not downloaded again: one header round trip
        # tells us which of the ~500 candidates are new. A 30-day sync used to
        # pull every message in full every time (375s, 9 Sep 2026).
        skipped = 0
        if skip_ids:
            mids = _prefetch_message_ids(mail, candidates)
            keep = []
            for i in candidates:
                mid = mids.get(i)
                if mid and mid in skip_ids:
                    skipped += 1
                    continue
                keep.append(i)
            candidates = keep
            logger.info(f"[EmailSync] {folder}: {skipped} already logged, {len(candidates)} to read")
        for msg_id in candidates:
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
            matched_by = "address"
            entity = known.get(counter_email)
            if not entity and known_domains and "@" in counter_email:
                entity = known_domains.get(counter_email.split("@")[-1])
                if entity:
                    matched_by = "domain"
            if not entity and thread_map and direction == "received":
                for ref in _thread_refs(msg):
                    if ref in thread_map:
                        entity = thread_map[ref]
                        matched_by = "thread"
                        break
            if not entity and alias_index:
                from services.thread_signals import link_by_alias
                subj = _decode(msg.get("Subject"))
                company, how = link_by_alias(subj, _body_snippet(msg, limit=4000), counter_email,
                                             alias_index.get("codenames") or {},
                                             alias_index.get("advisers") or {})
                if company:
                    entity = (alias_index.get("entities") or {}).get(company) or {"type": "company", "name": company}
                    matched_by = how
            if not entity and direction == "received":
                # 5. BOUNCE BY QUOTED ADDRESS. Some delivery reports (Microsoft
                # 365 NDRs in particular) carry no In-Reply-To or References,
                # so the thread rung cannot place them; but every report quotes
                # the address that failed, and that address is a contact we
                # know. Only a mailer-daemon style sender qualifies, so an
                # ordinary email that happens to mention a founder's address
                # never links through this rung.
                from services.delivery_check import is_bounce, _DAEMON_ADDRESSES
                local = counter_email.split("@")[0] if "@" in counter_email else counter_email
                subj = _decode(msg.get("Subject"))
                if any(d in local for d in _DAEMON_ADDRESSES) or is_bounce(subj, ""):
                    # Raw bytes, not the snippet: the address usually sits in
                    # the message/delivery-status part (Final-Recipient),
                    # which has no decodable payload of its own.
                    try:
                        body_l = msg.as_bytes()[:200000].decode("utf-8", "ignore").lower()
                    except Exception:
                        body_l = _body_snippet(msg, limit=6000).lower()
                    for addr, ent in (known or {}).items():
                        if addr and addr in body_l:
                            entity, matched_by = ent, "bounce-address"
                            break
            if not entity:
                continue

            date_hdr = msg.get("Date")
            try:
                sent_at = email.utils.parsedate_to_datetime(date_hdr).astimezone(timezone.utc).isoformat()
            except Exception:
                sent_at = datetime.now(timezone.utc).isoformat()

            # Attachments: extracted ONLY for inbound company mail (the messages
            # whose documents we keep), so a 500-message scan does not hold
            # hundreds of payloads in memory.
            attachments = []
            pdf_links = []
            if direction == "received" and entity.get("type") == "company":
                from services.email_docs_service import extract_attachments, extract_pdf_links
                attachments = extract_attachments(msg)
                # Direct .pdf links in the body: founders often SHARE a deck
                # rather than attach it (Plastometrex did exactly this). The
                # snippet is truncated at 500 chars, so links are pulled from
                # a longer read of the same body. Downloading happens later in
                # the docs pipeline, behind its SSRF safety check.
                pdf_links = extract_pdf_links(_body_snippet(msg, limit=20000))

            invite = _invite_in(msg) if entity.get("type") == "company" else None
            real_mid = _decode(msg.get("Message-ID")) or ""
            if real_mid.startswith("<") and real_mid not in thread_map:
                thread_map[real_mid] = {"type": entity["type"], "name": entity["name"]}
            entries.append({
                "attachments": attachments,
                "pdf_links": pdf_links,
                "invite": invite,
                "message_id": _decode(msg.get("Message-ID")) or f"{folder}-{msg_id.decode()}",
                "thread_id": _decode(msg.get("References", "")).split()[0] if msg.get("References") else _decode(msg.get("Message-ID")) or "",
                "direction": direction,
                "counterparty_email": counter_email,
                "counterparty_name": _decode(counter_name),
                "entity_type": entity["type"],
                "entity_name": entity["name"],
                "matched_by": matched_by,
                "subject": _decode(msg.get("Subject")),
                "snippet": _body_snippet(msg),
                "sent_at": sent_at,
                # Autoresponder markers only (RFC 3834 and the vendor variants),
                # not the whole header block. These are the one unambiguous way
                # to tell an out-of-office from a real reply, and they cost
                # nothing to read since the message is already parsed. Consumed
                # by services/ooo_detect.py; not a column in email_log.
                "headers": "; ".join(
                    f"{h}: {_decode(msg.get(h))}" for h in (
                        "Auto-Submitted", "X-Autoreply", "X-Autorespond",
                        "X-Auto-Response-Suppress", "Precedence",
                    ) if msg.get(h)
                ),
            })
    except Exception as e:
        logger.error(f"[EmailSync] Folder {folder} failed: {e}")
    return entries


def sync_mailbox(known_contacts: Dict[str, dict], days: int = 30, alias_index: dict = None,
                 known_domains: Dict[str, dict] = None, deep: bool = False,
                 deep_addresses: List[str] = None,
                 thread_map: Dict[str, dict] = None,
                 skip_ids: set = None) -> List[dict]:
    """
    Read Beatrice's mailbox (IMAP, same App Password as sending) and return
    entries for messages exchanged with known contacts only.
    known_contacts: {email_lower: {"type": "company"|"investor", "name": str}}
    known_domains: {domain_lower: entity} fallback — a reply from ANY address
    at a company's domain matches that company (e.g. we email hello@x.com,
    the founder replies from jane@x.com). Free-mail domains excluded upstream.
    deep: search per address/domain instead of scanning the newest messages —
    captures the FULL exchange history from the very start. deep_addresses
    limits the search list to contacts we actually corresponded with (the
    full universe would mean thousands of IMAP searches and a timeout).
    """
    # Every configured sender mailbox is read: the founder mailbox (Bea) and,
    # when set up, the investor mailbox (per Ishu, 8 Sep 2026). Direction is
    # detected per message against the mailbox being read, so a reply to the
    # LP mailbox is matched and logged exactly like one to the founder mailbox.
    from services.outreach_service import sender_profile
    mailboxes, _seen_boxes = [], set()
    for prof in (sender_profile("founder"), sender_profile("investor"), sender_profile("investor_intl")):
        if prof["configured"] and prof["email"].lower() not in _seen_boxes:
            _seen_boxes.add(prof["email"].lower())
            mailboxes.append(prof)      # the investor fallback shares Bea's box: read once
    if not mailboxes:
        raise RuntimeError("OUTREACH_SMTP_PASSWORD not configured (same App Password as sending)")

    addresses = None
    max_fetch = 500
    if deep:
        addresses = sorted(set(deep_addresses)) if deep_addresses else sorted(
            {e for e in known_contacts} | {"@" + d for d in (known_domains or {})})
        addresses = addresses[:300]  # hard safety cap on IMAP searches
        max_fetch = 2000

    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    entries: List[dict] = []
    for box in mailboxes:
        sender, password = box["email"], box["password"]
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        try:
            mail.login(sender, password)
            # All Mail covers INBOX + Sent + archived + filtered/labelled mail in
            # one pass; direction is detected per message inside _fetch_folder.
            got = _fetch_folder(mail, "[Gmail]/All Mail", since, sender, known_contacts, known_domains,
                                addresses=addresses, max_fetch=max_fetch, thread_map=thread_map, skip_ids=skip_ids, alias_index=alias_index)
            if not got:
                # Fallback for non-Gmail IMAP layouts
                got = _fetch_folder(mail, "INBOX", since, sender, known_contacts, known_domains,
                                    addresses=addresses, max_fetch=max_fetch, thread_map=thread_map, skip_ids=skip_ids, alias_index=alias_index)
                got += _fetch_folder(mail, "[Gmail]/Sent Mail", since, sender, known_contacts, known_domains,
                                     addresses=addresses, max_fetch=max_fetch, thread_map=thread_map, skip_ids=skip_ids, alias_index=alias_index)
            for e in got:
                e["mailbox"] = sender
            entries += got
        except Exception as e:
            # One mailbox failing (bad app password, IMAP off) must not hide
            # the other's replies; the failure is logged and reported.
            logger.error(f"[EmailSync] mailbox {sender} failed: {e}")
            if box is mailboxes[0] and len(mailboxes) == 1:
                raise
        finally:
            try:
                mail.logout()
            except Exception:
                pass
    # Dedup by message id (All Mail + fallback can overlap; a CC across both
    # mailboxes appears once)
    seen_ids, unique = set(), []
    for e in entries:
        if e["message_id"] not in seen_ids:
            seen_ids.add(e["message_id"])
            unique.append(e)
    logger.info(f"[EmailSync] {len(unique)} known-contact messages found (last {days} days, "
                f"{len(mailboxes)} mailbox(es))")
    return unique


def assess_sender_relation(company_name: str, sender_name: str, sender_email: str,
                           subject: str, snippet: str) -> str:
    """Who is this cross-domain sender to the company? 'company' | 'third_party'
    | 'unknown'. Cheap ungrounded Gemini call, made ONLY for thread-matched
    replies from unrecognised domains — a handful of messages, ever.

    Decides whether the sender's address may be ADOPTED as the company contact.
    A founder replying from a personal address is 'company' and worth adopting;
    a lawyer, advisor, PR agency or someone the email was forwarded to is
    'third_party' and must never overwrite the real contact. The reply itself is
    logged either way; only the adoption hangs on this answer, so 'unknown'
    (and any failure) safely means do not adopt.
    """
    api_key = _gemini_client.credential()
    if not api_key or not snippet:
        return "unknown"
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = _gemini_client.make_client()
        prompt = f"""We emailed the company "{company_name}". A reply arrived from an address
whose domain we do not recognise.

Sender name: {sender_name or 'unknown'}
Sender address: {sender_email}
Subject: {subject}
Reply text:
{snippet[:1200]}

Is the SENDER someone AT the company (founder, executive, employee, or the
company clearly speaking for itself, even from a personal address), or a THIRD
PARTY (advisor, lawyer, accountant, PR or agency, investor, someone the email
was forwarded to)?

Signals for 'company': signs off with the company name or a role at it, says
"we" about the company's business, personal address but same person we wrote to.
Signals for 'third_party': names a different firm, says they act on behalf of or
advise the company, a forwarding chain, a different person introducing themselves
from elsewhere.

Return ONLY valid JSON: {{"relation": "company" or "third_party" or "unknown",
"reason": "one short sentence"}}"""
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.1),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        relation = (json.loads(text).get("relation") or "unknown").strip().lower()
        return relation if relation in ("company", "third_party") else "unknown"
    except Exception as e:
        logger.warning(f"[EmailSync] sender relation check failed for {company_name}: {e}")
        return "unknown"


def classify_reply(subject: str, snippet: str, entity_name: str) -> dict:
    """
    Classify a received reply with Gemini (ungrounded, cheap).
    Returns {"classification": ..., "summary": ...} or {} on failure.
    Classes: interested / not_now / declined / question / other
    """
    api_key = _gemini_client.credential()
    if not api_key or not snippet:
        return {}
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = _gemini_client.make_client()
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
