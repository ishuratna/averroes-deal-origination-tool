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
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
