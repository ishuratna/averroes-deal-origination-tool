#!/usr/bin/env python3
"""
Email documents: extraction, safe filing paths, and the update whitelist.

The whitelist is the part with teeth: a founder's document is excellent
evidence for some fields and terrible evidence for others, and a wrong
overwrite corrupts a verified record. Every doubtful case must be a no-op.
"""
import email
import email.mime.application
import email.mime.image
import email.mime.multipart
import email.mime.text
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services.email_docs_service import (  # noqa: E402
    decide_updates, doc_gcs_path, extract_attachments, sanitize_filename,
)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Filenames are made path-safe ──")
chk("plain name survives", sanitize_filename("Deck 2026.pdf"), "Deck 2026.pdf")
# Traversal collapses to the LAST segment - the only part that is a filename.
chk("path traversal is stripped", sanitize_filename("../../etc/passwd"), "passwd")
chk("separators are stripped", sanitize_filename("a/b\\c.pdf"), "c.pdf")
chk("empty becomes a name", sanitize_filename(""), "attachment")
chk("bounded length", len(sanitize_filename("x" * 300)), 120)

print()
print("── GCS paths are stable and browsable ──")
chk("company/date/filename shape",
    doc_gcs_path("Acme Ltd", "2026-08-20T10:00:00+00:00", "deck.pdf"),
    "email-docs/Acme Ltd/2026-08-20_deck.pdf")
chk("no date -> undated, never a crash",
    doc_gcs_path("Acme", "", "a.pdf"), "email-docs/Acme/undated_a.pdf")
chk("hostile company names are neutralised",
    "/" not in doc_gcs_path("A/B../C", "2026-01-01", "x.pdf").replace("email-docs/", "", 1).split("/")[0], True)

print()
print("── Attachment extraction from a real MIME message ──")
msg = email.mime.multipart.MIMEMultipart()
msg.attach(email.mime.text.MIMEText("Hi, deck attached."))
pdf = email.mime.application.MIMEApplication(b"%PDF-fake", _subtype="pdf")
pdf.add_header("Content-Disposition", "attachment", filename="Acme Deck.pdf")
msg.attach(pdf)
logo = email.mime.image.MIMEImage(b"\x89PNG-fake", _subtype="png")
logo.add_header("Content-Disposition", "inline", filename="logo.png")
msg.attach(logo)
parsed = email.message_from_bytes(msg.as_bytes())
atts = extract_attachments(parsed)
chk("both files found (everything attached, per Ishu)", len(atts), 2)
chk("pdf named and typed", (atts[0]["filename"], atts[0]["content_type"]),
    ("Acme Deck.pdf", "application/pdf"))
chk("bytes preserved", atts[0]["data"], b"%PDF-fake")
chk("a body-only email yields nothing",
    extract_attachments(email.message_from_string("Subject: hi\n\njust text")), [])

print()
print("── Cost guards: what earns an AI read ──")
from services.email_docs_service import AI_READS_PER_RUN, MIN_AI_IMAGE_BYTES, should_analyse  # noqa: E402
chk("a PDF is always read (decks and accounts live there)",
    should_analyse("application/pdf", 5_000), True)
chk("a signature-logo-sized image is filed but NOT read",
    should_analyse("image/png", 30 * 1024), False)
chk("a large image (scanned doc, chart) IS read",
    should_analyse("image/png", 400 * 1024), True)
chk("office docs are never sent to the model (it cannot read them natively)",
    should_analyse("application/vnd.openxmlformats-officedocument.wordprocessingml.document", 900_000), False)
chk("the image threshold is sane (50-500KB)",
    50 * 1024 <= MIN_AI_IMAGE_BYTES <= 500 * 1024, True)
chk("the per-run read budget is bounded", 1 <= AI_READS_PER_RUN <= 50, True)

print()
print("── The update whitelist ──")
company = {"name": "Acme", "revenue_estimate_m": 3.0, "employees": 20,
           "description": "A B2B SaaS platform for logistics teams.",
           "sector": "Logistics Tech", "website": "", "hq_city": None}

ok = decide_updates(company, [
    {"field": "revenue_estimate_m", "new": 5.2, "evidence": "FY25 revenue GBP 5.2m"},
    {"field": "employees", "new": "34", "evidence": "34 FTEs"},
    {"field": "website", "new": "https://acme.co.uk", "evidence": "footer"},
])
chk("document revenue replaces the estimate",
    next(u for u in ok if u["field"] == "revenue_estimate_m")["new"], 5.2)
chk("headcount is coerced to an integer",
    next(u for u in ok if u["field"] == "employees")["new"], 34)
chk("an empty website is filled",
    next(u for u in ok if u["field"] == "website")["new"], "https://acme.co.uk")

chk("a SHORTER description never wins (longer-wins rule)",
    decide_updates(company, [{"field": "description", "new": "SaaS company."}]), [])
chk("a longer description does win",
    decide_updates(company, [{"field": "description",
                              "new": company["description"] + " Serves 200 enterprise customers across the UK."}])[0]["field"],
    "description")
chk("a filled sector is never replaced",
    decide_updates(company, [{"field": "sector", "new": "Fintech"}]), [])
chk("fields off the whitelist are ignored entirely",
    decide_updates(company, [{"field": "revenue_y1", "new": 999},
                             {"field": "status", "new": "Won"},
                             {"field": "averroes_fit_score", "new": 1.0}]), [])
chk("zero and negative numbers are refused",
    decide_updates(company, [{"field": "revenue_estimate_m", "new": 0},
                             {"field": "employees", "new": -5}]), [])
chk("garbage numbers are refused",
    decide_updates(company, [{"field": "employees", "new": "about forty"}]), [])
chk("no-change proposals are dropped",
    decide_updates(company, [{"field": "employees", "new": 20}]), [])
chk("empty proposals are safe", decide_updates(company, []), [])
chk("None proposals are safe", decide_updates(company, None), [])

print()
print("── PDF links in the body (the Plastometrex case) ──")
from services.email_docs_service import _is_safe_url, extract_pdf_links  # noqa: E402

chk("a direct pdf link is found",
    extract_pdf_links("Our deck: https://plastometrex.com/files/PIP-deck.pdf - enjoy"),
    ["https://plastometrex.com/files/PIP-deck.pdf"])
chk("query strings survive",
    extract_pdf_links("see https://a.co/x.pdf?dl=1&v=2 now"), ["https://a.co/x.pdf?dl=1&v=2"])
chk("drive/dropbox share links are NOT treated as pdfs (they serve HTML)",
    extract_pdf_links("https://drive.google.com/file/d/abc/view and https://www.dropbox.com/s/x/deck"), [])
chk("capped at 2 per email",
    len(extract_pdf_links(" ".join(f"https://a.co/{i}.pdf" for i in range(5)))), 2)
chk("duplicates collapse",
    extract_pdf_links("https://a.co/x.pdf and again https://a.co/x.pdf"), ["https://a.co/x.pdf"])
chk("no links, no crash", extract_pdf_links(""), [])

print()
print("── The SSRF guard: external mail must never reach anything internal ──")
pub = lambda h: ["93.184.216.34"]
chk("a normal public URL passes", _is_safe_url("https://plastometrex.com/deck.pdf", pub), True)
chk("http (not just https) is allowed", _is_safe_url("http://a.co/x.pdf", pub), True)
chk("the metadata server is refused",
    _is_safe_url("http://metadata.google.internal/computeMetadata/v1/x.pdf", pub), False)
chk("any .internal host is refused", _is_safe_url("https://x.svc.internal/a.pdf", pub), False)
chk("localhost is refused", _is_safe_url("http://localhost/x.pdf", pub), False)
chk("raw-IP hosts are refused outright", _is_safe_url("https://93.184.216.34/x.pdf", pub), False)
chk("a host resolving to the metadata IP is refused",
    _is_safe_url("https://evil.example/x.pdf", lambda h: ["169.254.169.254"]), False)
chk("a host resolving to a private range is refused",
    _is_safe_url("https://evil.example/x.pdf", lambda h: ["10.0.0.5"]), False)
chk("ONE private address among many public poisons the whole host",
    _is_safe_url("https://evil.example/x.pdf", lambda h: ["93.184.216.34", "127.0.0.1"]), False)
chk("credentials in the URL are refused", _is_safe_url("https://user@a.co/x.pdf", pub), False)
chk("non-http schemes are refused", _is_safe_url("ftp://a.co/x.pdf", pub), False)
chk("resolution failure fails CLOSED", _is_safe_url("https://a.co/x.pdf", lambda h: []), False)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
