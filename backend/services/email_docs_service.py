"""
Email documents: attachments founders send us.

Three jobs, in order:
  1. EXTRACT  every attachment from an inbound, company-matched email.
  2. FILE     the bytes in GCS under email-docs/<company>/, metadata in BQ
              (email_documents), deduped on (message_id, filename).
  3. READ     the document with AI and apply what it teaches us - but only to
              a WHITELIST of fields, each with its own overwrite rule, and
              every change written to the Activity Log with old value, new
              value and which document said so.

The whitelist exists because a founder's deck is excellent evidence for some
fields and terrible evidence for others. Their own revenue figure beats our
estimate; their marketing copy does not beat a longer stored description.
AI never touches CH-filed figures (revenue_y1 etc.): a deck's numbers are
unaudited claims, so they land in the ESTIMATE fields, clearly sourced.
"""
import base64
import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024   # one file
MAX_ATTACHMENTS_PER_EMAIL = 10

# Types Gemini can read natively. Everything else is still FILED (per Ishu:
# everything attached is kept), just not analysed.
_AI_READABLE = ("application/pdf", "image/png", "image/jpeg", "image/webp")

# COST GUARDS. Most attachments are signature logos: the same small image on
# every message in a thread. Filing is near-free; AI-reading each one is pure
# waste (a 30KB logo holds no company data, and threads repeat it endlessly).
MIN_AI_IMAGE_BYTES = 100 * 1024      # images below this are filed, not read
AI_READS_PER_RUN = 10                # per sync run, a mass-attachment email cannot spike spend


def should_analyse(content_type: str, size_bytes: int) -> bool:
    """Is AI-reading this file worth a call? Pure, testable.

    PDFs always (that is where decks and accounts live, ~1p each). Images only
    when large enough to plausibly be a scanned document or a chart, not a
    signature logo. Everything else Gemini cannot read natively anyway.
    """
    if content_type == "application/pdf":
        return True
    if content_type in _AI_READABLE:                 # the image types
        return size_bytes >= MIN_AI_IMAGE_BYTES
    return False


# ── PDF links in the email body (per Ishu, 27 Aug 2026: Plastometrex sent
#    their deck as a LINK, which no attachment pipeline can see) ─────────────
#
# Only bare, direct .pdf URLs are followed. Drive/Dropbox/WeTransfer links
# need access grants and return HTML, so they are ignored rather than half
# fetched. And because these URLs come from EXTERNAL email, the fetch must
# never be usable to reach anything internal (SSRF): scheme, host and every
# resolved address are checked before a single byte is requested.

MAX_PDF_LINKS_PER_EMAIL = 2
_PDF_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+\.pdf(?:\?[^\s<>\"')\]]*)?", re.IGNORECASE)


def extract_pdf_links(text: str) -> List[str]:
    """Direct .pdf URLs in an email body. Pure, capped, deduplicated."""
    out, seen = [], set()
    for m in _PDF_URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;")
        if url.lower() not in seen:
            seen.add(url.lower())
            out.append(url)
        if len(out) >= MAX_PDF_LINKS_PER_EMAIL:
            break
    return out


def _is_safe_url(url: str, resolver=None) -> bool:
    """May the backend fetch this URL at all?

    The URL came from an external email, so this guard is what stands between
    'file the founder's deck' and 'let an attacker read the Cloud Run metadata
    server'. http/https only, no credentials in the URL, no raw-IP hosts, and
    EVERY address the hostname resolves to must be public.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            return False
        host = parts.hostname.lower()
        if host in ("metadata.google.internal", "localhost") or host.endswith(".internal"):
            return False
        try:
            ipaddress.ip_address(host)
            return False           # raw-IP hosts are never a founder's website
        except ValueError:
            pass
        resolver = resolver or (lambda h: [ai[4][0] for ai in socket.getaddrinfo(h, 443)])
        addrs = resolver(host)
        if not addrs:
            return False
        for a in addrs:
            ip = ipaddress.ip_address(a)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False


def fetch_pdf_link(url: str) -> Optional[Dict]:
    """Download one vetted PDF link. Returns an attachment-shaped dict or None.

    Belt and braces: the safety check first, a hard size cap while streaming,
    and the bytes must actually BE a PDF (%PDF magic) - a login page served
    with a .pdf URL is silently dropped.
    """
    if not _is_safe_url(url):
        logger.info(f"[EmailDocs] link refused by safety check: {url[:120]}")
        return None
    try:
        import requests
        with requests.get(url, timeout=20, stream=True,
                          headers={"User-Agent": "AverroesIntel document fetch"}) as r:
            if r.status_code != 200:
                return None
            data, cap = b"", MAX_ATTACHMENT_BYTES
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > cap:
                    return None
        if not data.startswith(b"%PDF"):
            return None
        from urllib.parse import urlsplit
        name = sanitize_filename(urlsplit(url).path.split("/")[-1] or "document.pdf")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return {"filename": name, "content_type": "application/pdf", "data": data}
    except Exception as e:
        logger.warning(f"[EmailDocs] link fetch failed ({url[:120]}): {e}")
        return None


def sanitize_filename(name: str) -> str:
    """A filename safe for a GCS path: no separators, no traversal, bounded."""
    name = (name or "attachment").strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    return (name or "attachment")[:120]


def doc_gcs_path(company: str, received_at: str, filename: str) -> str:
    """email-docs/<company>/<date>_<filename> - stable, human-browsable."""
    safe_company = re.sub(r"[^\w\- ]", "_", (company or "unknown")).strip()[:80]
    day = (received_at or "")[:10] or "undated"
    return f"email-docs/{safe_company}/{day}_{sanitize_filename(filename)}"


def extract_attachments(msg) -> List[Dict]:
    """Every attached file in a parsed email.message. Pure, testable.

    'Everything attached' per Ishu: any part carrying a filename counts,
    signature logos included. Bounded per file and per email so one enormous
    mail cannot blow up a sync run.
    """
    out = []
    try:
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True)
            if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
                continue
            out.append({
                "filename": sanitize_filename(filename),
                "content_type": part.get_content_type() or "application/octet-stream",
                "data": payload,
            })
            if len(out) >= MAX_ATTACHMENTS_PER_EMAIL:
                break
    except Exception as e:
        logger.warning(f"[EmailDocs] attachment extraction failed: {e}")
    return out


# ── The update whitelist ──────────────────────────────────────────────────────
# field -> rule for when a document's value may replace the stored one.
#   estimate   documents beat estimates, never beat CH-filed figures
#   longer     the existing "longer wins" description rule
#   if_empty   only fills a blank, never replaces
UPDATABLE_FIELDS = {
    "revenue_estimate_m": "estimate",
    "employees": "estimate",
    "description": "longer",
    "sector": "if_empty",
    "website": "if_empty",
    "hq_city": "if_empty",
}


def decide_updates(company: Dict, proposed: List[Dict]) -> List[Dict]:
    """Which AI-proposed changes are actually allowed. Pure, testable.

    Every rejection is silent by design: a document that fails to change a
    field costs nothing, while a wrong overwrite corrupts a verified record.
    """
    allowed = []
    for p in proposed or []:
        field = (p.get("field") or "").strip()
        new = p.get("new")
        rule = UPDATABLE_FIELDS.get(field)
        if not rule or new in (None, "", 0):
            continue
        old = company.get(field)
        if rule == "if_empty" and old not in (None, ""):
            continue
        if rule == "longer" and isinstance(old, str) and isinstance(new, str) \
                and len(new.strip()) <= len(old.strip()):
            continue
        if field in ("revenue_estimate_m", "employees"):
            try:
                new = float(new) if field == "revenue_estimate_m" else int(float(new))
            except (ValueError, TypeError):
                continue
            if new <= 0:
                continue
        if old == new:
            continue
        allowed.append({"field": field, "old": old, "new": new,
                        "evidence": (p.get("evidence") or "")[:300]})
    return allowed


def analyse_document(company: Dict, filename: str, content_type: str,
                     data: bytes) -> Dict:
    """Read one document with Gemini (ungrounded, cheap) and propose updates.

    Returns {"summary": str, "proposed": [{field, new, evidence}]}. Types the
    model cannot read natively come back unanalysed rather than guessed at.
    """
    if content_type not in _AI_READABLE:
        return {"summary": "", "proposed": []}
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"summary": "", "proposed": []}
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, Part

        client = genai.Client(api_key=api_key)
        current = {f: company.get(f) for f in UPDATABLE_FIELDS}
        prompt = f"""A company we are evaluating, "{company.get('name')}", emailed us the attached
document ("{filename}"). Our current record holds:
{json.dumps(current, default=str)}

1. Summarise the document in one or two sentences (what it is and its key facts).
2. Propose field updates ONLY where the document clearly states a better value.
   Allowed fields and meanings:
   - revenue_estimate_m: annual revenue in GBP MILLIONS (convert if needed)
   - employees: current headcount
   - description: what the company does (only if the document supports a richer one)
   - sector, website, hq_city: only if we hold nothing
   Do NOT invent values. Every proposal needs a short quote or figure from the
   document as evidence.

Return ONLY valid JSON:
{{"summary": "...", "proposed": [{{"field": "...", "new": ..., "evidence": "..."}}]}}"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[Part.from_bytes(data=data, mime_type=content_type), prompt],
            config=GenerateContentConfig(temperature=0.1),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        got = json.loads(text)
        return {"summary": (got.get("summary") or "")[:600],
                "proposed": got.get("proposed") or []}
    except Exception as e:
        logger.warning(f"[EmailDocs] AI read failed for {filename}: {e}")
        return {"summary": "", "proposed": []}


def process_email_documents(bq_handler, gcs_handler, entry: Dict,
                            company_row: Optional[Dict],
                            ai_budget: Optional[List[int]] = None) -> List[str]:
    """File and read every attachment on one inbound email. Returns saved names.

    Idempotent twice over: (message_id, filename) already stored is skipped,
    and IDENTICAL BYTES already filed for this company are skipped entirely
    (content hash) - the signature logo attached to every message in a thread
    files once, not once per email, and never costs a second AI read.

    ai_budget is a single-element list shared by the caller across one run
    (mutable on purpose): each AI read decrements it, and at zero the remaining
    documents are filed without analysis.
    """
    saved = []
    if entry.get("entity_type") != "company":
        return saved
    attachments = list(entry.get("attachments") or [])
    # Direct .pdf links in the body ride the same pipeline as attachments:
    # fetched (behind the SSRF guard), then filed, hashed and AI-read exactly
    # like a file the founder attached.
    for url in (entry.get("pdf_links") or [])[:MAX_PDF_LINKS_PER_EMAIL]:
        att = fetch_pdf_link(url)
        if att:
            att["origin"] = "link"
            attachments.append(att)
    if not attachments:
        return saved
    company = entry["entity_name"]
    for att in attachments:
        try:
            if bq_handler.email_doc_exists(entry["message_id"], att["filename"]):
                continue
            sha = hashlib.sha256(att["data"]).hexdigest()
            if bq_handler.email_doc_hash_exists(company, sha):
                continue  # same bytes already on file for this company
            path = doc_gcs_path(company, entry.get("sent_at") or "", att["filename"])
            bucket = gcs_handler.storage_client.bucket(gcs_handler.bucket_name)
            blob = bucket.blob(path)
            blob.upload_from_string(att["data"], content_type=att["content_type"])

            analysis = {"summary": "", "proposed": []}
            if should_analyse(att["content_type"], len(att["data"])) \
                    and (ai_budget is None or ai_budget[0] > 0):
                if ai_budget is not None:
                    ai_budget[0] -= 1
                analysis = analyse_document(company_row or {"name": company},
                                            att["filename"], att["content_type"], att["data"])
            applied = decide_updates(company_row or {}, analysis.get("proposed"))

            if applied:
                from google.cloud import bigquery as bq_lib
                sets, params = [], []
                for i, u in enumerate(applied):
                    sets.append(f"{u['field']} = @v{i}")
                    kind = "FLOAT64" if u["field"] == "revenue_estimate_m" \
                        else "INT64" if u["field"] == "employees" else "STRING"
                    params.append(bq_lib.ScalarQueryParameter(f"v{i}", kind, u["new"]))
                if any(u["field"] == "revenue_estimate_m" for u in applied):
                    sets.append("revenue_source = @rs")
                    params.append(bq_lib.ScalarQueryParameter(
                        "rs", "STRING", f"Company document: {att['filename']}"))
                params.append(bq_lib.ScalarQueryParameter("name", "STRING", company))
                bq_handler.client.query(
                    f"UPDATE `{bq_handler.table_id}` SET {', '.join(sets)} WHERE name = @name",
                    job_config=bq_lib.QueryJobConfig(query_parameters=params)).result()
                for u in applied:
                    bq_handler.add_activity_note(
                        company,
                        f"Updated from document \"{att['filename']}\": {u['field']} "
                        f"{u['old'] if u['old'] not in (None, '') else '(empty)'} -> {u['new']}. "
                        f"Evidence: {u['evidence'] or 'stated in the document'}",
                        created_by="email-docs")

            bq_handler.save_email_doc({
                "company_name": company, "filename": att["filename"],
                "gcs_path": path, "content_type": att["content_type"],
                "content_sha256": sha,
                "size_bytes": len(att["data"]), "message_id": entry["message_id"],
                "email_subject": entry.get("subject") or "",
                "sender_email": entry.get("counterparty_email") or "",
                "received_at": entry.get("sent_at"),
                "ai_summary": analysis.get("summary") or "",
                "ai_updates": json.dumps(applied) if applied else "",
            })
            how = ("downloaded from a link in their email" if att.get("origin") == "link"
                   else "received by email")
            bq_handler.add_activity_note(
                company,
                f"Document {how}: \"{att['filename']}\" "
                f"({att['content_type']}, {len(att['data']) // 1024}KB) filed to Email documents."
                + (f" {analysis['summary']}" if analysis.get("summary") else ""),
                created_by="email-docs")
            saved.append(att["filename"])
        except Exception as e:
            logger.warning(f"[EmailDocs] failed to file {att.get('filename')} for {company}: {e}")
    return saved
