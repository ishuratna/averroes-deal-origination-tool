"""
Email documents: attachments founders send us.

Three jobs, in order:
  1. EXTRACT  every attachment from an inbound, company-matched email.
  2. FILE     the bytes in GCS under email-docs/<company>/, metadata in BQ
              (email_documents), deduped on (message_id, filename).
  3. READ     the document with AI (Document SmartFill, services/doc_smartfill.py):
              everything it states about the company plus year-tagged
              financials. Values the record lacks are written at once;
              values that DISAGREE with the record are held as pending and
              Ishu confirms them on the profile (current vs document, with
              evidence). Every write lands in the Activity Log with old
              value, new value and which document said so, then the fit
              score is recomputed locally (zero AI).

Manual uploads from a company's card ride this exact pipeline, so an
attachment and an upload can never be treated differently.
"""
import base64
import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional

from services.doc_smartfill import (
    COLUMN_TYPES, MIN_PDF_TEXT_CHARS, extraction_prompt, merge_writes, office_kind,
    office_to_text, pdf_pages_to_images, pdf_to_text, plan_updates,
)

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024   # one file arriving by email
# A manual upload from the company card is a deliberate act on a document
# Ishu has already judged worth reading, so it may be much larger (a 35MB
# image-heavy deck was the first real case, 7 Sep 2026). Large files travel
# browser -> Cloud Storage directly, never through Cloud Run's 32MB request
# ceiling. PDFs are read from their text layer; picture decks are rendered
# page by page to images (never the Files API - see analyse_document).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
GEMINI_INLINE_LIMIT = 18 * 1024 * 1024    # inline request parts stop at 20MB
MAX_ATTACHMENTS_PER_EMAIL = 10

# Types Gemini can read natively. Everything else is still FILED (per Ishu:
# everything attached is kept), just not analysed.
_AI_READABLE = ("application/pdf", "image/png", "image/jpeg", "image/webp")

# COST GUARDS. Most attachments are signature logos: the same small image on
# every message in a thread. Filing is near-free; AI-reading each one is pure
# waste (a 30KB logo holds no company data, and threads repeat it endlessly).
MIN_AI_IMAGE_BYTES = 100 * 1024      # images below this are filed, not read
AI_READS_PER_RUN = 10                # per sync run, a mass-attachment email cannot spike spend


def should_analyse(content_type: str, size_bytes: int, filename: str = "") -> bool:
    """Is AI-reading this file worth a call? Pure, testable.

    PDFs always (that is where decks and accounts live, ~1p each). Images only
    when large enough to plausibly be a scanned document or a chart, not a
    signature logo. Everything else Gemini cannot read natively anyway.
    """
    if content_type == "application/pdf":
        return True
    if content_type in _AI_READABLE:                 # the image types
        return size_bytes >= MIN_AI_IMAGE_BYTES
    if office_kind(content_type, filename):          # pptx/xlsx/docx -> text first
        return True
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


# ── Reading a document: Document SmartFill ──────────────────────────────────
# The rules (what is extracted, fill vs confirm, financial-year merge) live in
# services/doc_smartfill.py and are pure. This module does the I/O around them.



def analyse_document(company: Dict, filename: str, content_type: str,
                     data: bytes) -> Dict:
    """Read one document with Gemini (ungrounded, cheap) and return the raw
    extraction: {"summary": str, "company": {...}, "evidence": {...},
    "financial_years": [...]}. PDFs and images go to the model as bytes;
    pptx/xlsx/docx are converted to text first. Unreadable -> {}."""
    kind = office_kind(content_type, filename)
    text_doc = ""
    if kind:
        text_doc = office_to_text(kind, data)
        if not text_doc:
            return {"_error": f"could not extract text from the {kind} file"}
    elif content_type == "application/pdf":
        # Text layer first (cheap, no size limit); vision only for scans.
        t = pdf_to_text(data)
        if len(t) >= MIN_PDF_TEXT_CHARS:
            kind, text_doc = "pdf text", t
    elif content_type not in _AI_READABLE:
        return {}
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"_error": "GEMINI_API_KEY not configured"}
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, Part

        client = genai.Client(api_key=api_key)
        prompt = extraction_prompt(company, filename)
        if text_doc:
            path = f"text ({kind}, {len(text_doc)} chars)"
            contents = [f"DOCUMENT TEXT ({kind}):\n{text_doc}", prompt]
        elif content_type == "application/pdf" and len(data) > GEMINI_INLINE_LIMIT:
            # A big PDF with no text layer is a picture deck. Render the pages
            # and send them as images: every page seen, nothing uploaded to a
            # file store (the Files API route returned 400 INVALID_ARGUMENT on
            # a 60MB deck twice, 8 Sep 2026).
            pages = pdf_pages_to_images(data)
            if not pages:
                return {"_error": "large scanned PDF: could not render pages for the vision read"}
            path = f"vision ({len(pages)} page images, {sum(map(len, pages)) // 1024}KB)"
            contents = [f"The document's {len(pages)} pages follow as images, in page order (image 1 = page 1). Cite page numbers in evidence."] \
                + [Part.from_bytes(data=p, mime_type="image/jpeg") for p in pages] + [prompt]
        else:
            path = f"inline bytes ({len(data) // 1024}KB)"
            contents = [Part.from_bytes(data=data, mime_type=content_type), prompt]
        logger.info(f"[EmailDocs] reading {filename} via {path}")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        got = json.loads(text)
        if not isinstance(got, dict):
            return {}
        got["summary"] = (got.get("summary") or "")[:600]
        return got
    except Exception as e:
        logger.warning(f"[EmailDocs] AI read failed for {filename}: {e}")
        return {"_error": str(e)[:300]}


def apply_document_writes(bq_handler, company_row: Dict, items: List[Dict],
                          filename: str, created_by: str = "email-docs") -> Dict:
    """Write the columns of the given items, log each as an Activity Log note
    with old -> new and the document's evidence, then rescore locally (zero
    AI). ONE implementation for both the automatic fills and the changes Ishu
    confirms in the review, so the two can never drift.

    Returns the updated row (in memory) plus the rescore result."""
    from google.cloud import bigquery as bq_lib
    company = company_row.get("name")
    writes = merge_writes(items)
    writes = {c: v for c, v in writes.items() if c in COLUMN_TYPES}
    if not writes:
        return {"row": company_row, "rescore": None, "written": 0}
    if writes.get("revenue_source"):
        writes["revenue_source"] = f"Company document: {filename}"
    sets, params = [], []
    for i, (col, val) in enumerate(writes.items()):
        sets.append(f"{col} = @v{i}")
        t = COLUMN_TYPES[col]
        if val is not None:
            val = float(val) if t == "FLOAT64" else int(val) if t == "INT64" else str(val)
        params.append(bq_lib.ScalarQueryParameter(f"v{i}", t, val))
    params.append(bq_lib.ScalarQueryParameter("name", "STRING", company))
    bq_handler.client.query(
        f"UPDATE `{bq_handler.table_id}` SET {', '.join(sets)} WHERE name = @name",
        job_config=bq_lib.QueryJobConfig(query_parameters=params)).result()
    for it in items:
        bq_handler.add_activity_note(
            company,
            f"Updated from document \"{filename}\": {it.get('label') or it.get('key')} "
            f"{it.get('old') or '(empty)'} -> {it.get('new')}. "
            f"Evidence: {it.get('evidence') or 'stated in the document'}",
            created_by=created_by)

    row = dict(company_row)
    row.update(writes)
    rescore = rescore_after_document(bq_handler, row, filename, created_by)
    return {"row": row, "rescore": rescore, "written": len(writes)}


def rescore_after_document(bq_handler, row: Dict, filename: str,
                           created_by: str = "email-docs") -> Optional[Dict]:
    """Refresh the revenue-size and revenue-growth metrics from the row as it
    now stands, then the local rescore (the same function the book-wide
    rescore uses). Zero AI. Returns the score movement or None."""
    try:
        from ai.scoring import (_compute_revenue_growth, _compute_revenue_size,
                                compute_revenue_band, rescore_company_local)
        from google.cloud import bigquery as bq_lib
        try:
            details = json.loads(row.get("score_details") or "{}")
        except (ValueError, TypeError):
            details = {}
        rs = _compute_revenue_size(row)
        if rs:
            details["revenue_size"] = rs
        rg = _compute_revenue_growth(row)
        if rg:
            details["revenue_growth"] = rg
        row["score_details"] = json.dumps(details)
        upd = rescore_company_local(row)
        if not upd:
            return None
        old = row.get("averroes_fit_score")
        band = compute_revenue_band(rs["value"]) if rs else row.get("revenue_band")
        bq_handler.client.query(
            f"""UPDATE `{bq_handler.table_id}` SET
                    averroes_fit_score = @fit, score_revenue_size = @rs,
                    score_revenue_growth = @rg, score_employee_growth = @eg,
                    score_details = @sd, revenue_band = @band
                WHERE name = @name""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("fit", "FLOAT64", upd["averroes_fit_score"]),
                bq_lib.ScalarQueryParameter("rs", "FLOAT64", upd["score_revenue_size"]),
                bq_lib.ScalarQueryParameter("rg", "FLOAT64", upd["score_revenue_growth"]),
                bq_lib.ScalarQueryParameter("eg", "FLOAT64", upd["score_employee_growth"]),
                bq_lib.ScalarQueryParameter("sd", "STRING", upd["score_details"]),
                bq_lib.ScalarQueryParameter("band", "STRING", band),
                bq_lib.ScalarQueryParameter("name", "STRING", row.get("name")),
            ])).result()
        new = upd["averroes_fit_score"]
        if old is not None and new is not None and abs(float(old) - new) < 0.0005:
            return {"old": old, "new": new}
        bq_handler.add_activity_note(
            row.get("name"),
            f"Fit score recomputed after document \"{filename}\": "
            f"{('%.2f' % float(old)) if old is not None else 'unscored'} -> "
            f"{('%.2f' % new) if new is not None else 'unscored'} (local rules, no AI).",
            created_by=created_by)
        return {"old": old, "new": new}
    except Exception as e:
        logger.warning(f"[EmailDocs] rescore after document failed for {row.get('name')}: {e}")
        return None


def process_email_documents(bq_handler, gcs_handler, entry: Dict,
                            company_row: Optional[Dict],
                            ai_budget: Optional[List[int]] = None,
                            errors: Optional[List[str]] = None,
                            pending_out: Optional[List[Dict]] = None) -> List[str]:
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

            extracted: Dict = {}
            if should_analyse(att["content_type"], len(att["data"]), att["filename"]) \
                    and (ai_budget is None or ai_budget[0] > 0):
                if ai_budget is not None:
                    ai_budget[0] -= 1
                extracted = analyse_document(company_row or {"name": company},
                                             att["filename"], att["content_type"], att["data"])
            plan = plan_updates(company_row or {"name": company}, extracted)
            summary = (extracted.get("summary") or "") if extracted else ""
            read_error = (extracted or {}).get("_error") or ""
            if read_error:
                bq_handler.add_activity_note(
                    company, f"Document \"{att['filename']}\" was filed but the AI read FAILED: {read_error}",
                    created_by="email-docs")

            # Fills (the record held nothing) are written now; conflicts (the
            # record holds a different value) wait on the review. Same apply
            # path for both - see apply_document_writes.
            applied_fills = []
            if plan["fills"]:
                res = apply_document_writes(bq_handler, company_row or {"name": company},
                                            plan["fills"], att["filename"])
                applied_fills = plan["fills"]
                if company_row is not None:
                    company_row.update(res["row"])      # later attachments see the new state

            bq_handler.save_email_doc({
                "company_name": company, "filename": att["filename"],
                "gcs_path": path, "content_type": att["content_type"],
                "content_sha256": sha,
                "size_bytes": len(att["data"]), "message_id": entry["message_id"],
                "email_subject": entry.get("subject") or "",
                "sender_email": entry.get("counterparty_email") or "",
                "received_at": entry.get("sent_at"),
                "ai_summary": summary,
                "ai_updates": json.dumps([{k: v for k, v in i.items() if k != "writes"}
                                          for i in applied_fills]) if applied_fills else "",
                "pending_updates": json.dumps(plan["conflicts"]) if plan["conflicts"] else "",
                "read_error": read_error,
            })
            if plan["conflicts"]:
                bq_handler.add_activity_note(
                    company,
                    f"Document \"{att['filename']}\" disagrees with {len(plan['conflicts'])} stored "
                    f"value(s): {', '.join(i['label'] for i in plan['conflicts'])}. "
                    f"Awaiting review on the profile (Email documents).",
                    created_by="email-docs")
            if pending_out is not None:
                pending_out.append({"gcs_path": path, "filename": att["filename"],
                                    "fills": len(applied_fills),
                                    "filled": [{k: v for k, v in i.items() if k != "writes"} for i in applied_fills],
                                    "pending": plan["conflicts"],
                                    "summary": summary, "read_error": read_error})
            how = ("downloaded from a link in their email" if att.get("origin") == "link"
                   else "received by email")
            bq_handler.add_activity_note(
                company,
                f"Document {how}: \"{att['filename']}\" "
                f"({att['content_type']}, {len(att['data']) // 1024}KB) filed to Email documents."
                + (f" {summary}" if summary else ""),
                created_by="email-docs")
            saved.append(att["filename"])
        except Exception as e:
            logger.warning(f"[EmailDocs] failed to file {att.get('filename')} for {company}: {e}")
            if errors is not None:
                errors.append(f"{att.get('filename')}: {e}")
    return saved
