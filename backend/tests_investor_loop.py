#!/usr/bin/env python3
"""
The investor (LP) loop mirrors the founder loop. What must hold without
BigQuery or Gmail: the investor sender profile borrows the founder mailbox visibly until
configured, the LP email structure has no
em dashes and keeps its [confirm] markers, the follow-up template threads under
the original subject, stages/parked/stamps are consistent, and the shared
follow-up endpoint accepts the investor entity.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")
os.environ.pop("GEMINI_API_KEY", None)
for k in ("INVESTOR_OUTREACH_EMAIL", "INVESTOR_SMTP_PASSWORD", "INVESTOR_OUTREACH_NAME"):
    os.environ.pop(k, None)

from services import outreach_service as osvc  # noqa: E402
from storage.investor_handler import (  # noqa: E402
    INVESTOR_PARKED, INVESTOR_STAGES, INVESTOR_STAGE_STAMPS, InvestorBQHandler,
)

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── Sender profiles ──")
inv = osvc.sender_profile("investor")
chk("without INVESTOR_* env the investor profile borrows the founder mailbox, VISIBLY",
    (inv["email"], inv["fallback"]), (osvc.SENDER_EMAIL, True))
chk("...and the label says so", "founder mailbox" in osvc.sender_label("investor")
    if osvc.sender_profile("founder")["configured"] else "not configured" in osvc.sender_label("investor"))
os.environ["INVESTOR_OUTREACH_EMAIL"] = "lp@averroescapital.com"
os.environ["INVESTOR_SMTP_PASSWORD"] = "app-pass"
os.environ["INVESTOR_OUTREACH_NAME"] = "Ishu Ratna"
inv = osvc.sender_profile("investor")
chk("configured once both env vars exist, no fallback", (inv["configured"], inv["fallback"]), (True, False))
chk("label reads Name <address>", osvc.sender_label("investor"), "Ishu Ratna <lp@averroescapital.com>")
chk("founder profile untouched", osvc.sender_profile("founder")["email"], osvc.SENDER_EMAIL)
print()
print("── Two investor desks, routed by region (Ishu, 14 Sep 2026) ──")
intl = osvc.sender_profile("investor_intl")
chk("the UK/Europe/global desk IS the founder mailbox by design (Ishu: 'use the same'), not a fallback",
    (intl["email"], intl["fallback"], intl["kind"]), (osvc.SENDER_EMAIL, False, "investor_intl"))
chk("...so its label never says 'not configured yet'", "not configured yet" not in osvc.sender_label("investor_intl")
    if osvc.sender_profile("founder")["configured"] else True)
os.environ["INVESTOR_INTL_OUTREACH_EMAIL"] = "bea2@averroescapital.com"
os.environ["INVESTOR_INTL_SMTP_PASSWORD"] = "app-pass-2"
os.environ["INVESTOR_INTL_OUTREACH_NAME"] = "Beatrice Carrara"
chk("configured, no fallback", osvc.sender_profile("investor_intl")["fallback"], False)
chk("Riyadh family office -> Ellie's desk", osvc.investor_sender_kind({"hq_country": "Saudi Arabia"}), "investor")
chk("Dubai by city -> Ellie's desk", osvc.investor_sender_kind({"hq_city": "Dubai"}), "investor")
chk("GCC-tagged list -> Ellie's desk even without a location", osvc.investor_sender_kind({"network_tags": "GCC"}), "investor")
chk("London office -> Bea's secondary", osvc.investor_sender_kind({"hq_country": "United Kingdom"}), "investor_intl")
chk("Zurich -> Bea's secondary", osvc.investor_sender_kind({"hq_country": "Switzerland"}), "investor_intl")
chk("Singapore with a UK mandate -> Bea's secondary (global)", osvc.investor_sender_kind({"hq_country": "Singapore", "geo_preferences": "United Kingdom"}), "investor_intl")
chk("unknown region -> Bea's secondary, never Riyadh", osvc.investor_sender_kind({"name": "Mystery LP"}), "investor_intl")
chk("Romania is not Oman", osvc.investor_sender_kind({"hq_country": "Romania"}), "investor_intl")
d_me = osvc.draft_lp_outreach_email({"name": "Al X", "hq_country": "UAE", "contact_name": "Ahmed"})
d_uk = osvc.draft_lp_outreach_email({"name": "Y Office", "hq_country": "United Kingdom", "contact_name": "Tom"})
chk("Gulf draft is written as Ellie's desk", "lp@averroescapital.com" in d_me["from"])
chk("UK draft is written as Bea's secondary", "bea2@averroescapital.com" in d_uk["from"])
chk("follow-up follows the same routing", "bea2@" in osvc.draft_lp_followup_email({"name": "Y", "hq_country": "France"})["from"])
main_src = open("main.py").read()
chk("the send path derives the desk from the investor row, never from the caller",
    "sender=investor_sender_kind(_row)" in main_src and 'sender="investor")' not in main_src)
sync_src0 = open("services/email_sync_service.py").read()
chk("the sync reads all three mailboxes", 'sender_profile("investor_intl")' in sync_src0)
for k in ("INVESTOR_INTL_OUTREACH_EMAIL", "INVESTOR_INTL_SMTP_PASSWORD", "INVESTOR_INTL_OUTREACH_NAME"):
    os.environ.pop(k, None)

sig = osvc.build_signature("Ishu Ratna", "Associate", "lp@averroescapital.com")
chk("signature carries name, title, address and the disclaimer",
    all(x in sig["text"] for x in ("Ishu Ratna", "Associate", "lp@averroescapital.com", "Appointed Representative")))
chk("html signature embeds the logo by CID", f"cid:{osvc.SIG_LOGO_CID}" in sig["html"])

print()
print("── LP email structure v3 (the door opener) ──")
# v3 (Ishu, 11 Sep 2026) replaced v2's full letter. The email is not the pitch:
# its only job is to open a door that a coffee or a call then walks through.
d = osvc.draft_lp_outreach_email({"name": "Acme Family Office", "contact_name": "Jane Roe",
                                  "contact_email": "jane@acme.com", "contact_title": "Chief Investment Officer",
                                  "investor_type": "Single Family Office", "hq_country": "Saudi Arabia"})
body = d["body"]
chk("fallback (no API key) is marked so it is never persisted", d.get("is_fallback"), True)
chk("subject is a person's, not a mail merge's", d["subject"], "Averroes Capital, an introduction")
chk("greets by first name", body.startswith("Hi Jane,"))
chk("opens with a NAME and title, not a job description",
    "I am Ishu, " in body and "at Averroes Capital, based in London and Riyadh." in body)
chk("what we do: buy and back UK/IE software, deal by deal alongside co-investors",
    "buy and back software companies in the UK and Ireland" in body
    and "come in with us on each deal" in body)
chk("WHY NOW is the reason for writing: exits, more acquisitions, a wider Gulf pool",
    "promising exits lined up for next year" in body
    and "acquiring more UK companies" in body
    and "widening our investor group in the Gulf" in body)
chk("the ask is a coffee in either city, or a named fifteen minutes",
    "coffee, in London or Riyadh" in body and "fifteen minutes on a call" in body)
chk("SHORT: this is a door opener, not a letter", len(body.split()) < 120)
chk("NO corporate profile and no offer to send one (rule 5)",
    not any(w in body.lower() for w in ("attached", "deck", "brochure", "overview", "i can send",
                                        "short note on averroes")))
chk("no returns, multiples or performance claims", 
    not any(w in body.lower() for w in ("irr", "multiple", "% return", "outperform")))
chk("no placeholders left for the reader", "[confirm" not in body)
chk("ZERO em/en dashes anywhere", "—" not in body and "–" not in body)
chk("ends with Best, (signature added on send)", body.rstrip().endswith("Best,"))
chk("from line reports the investor mailbox", d["from"], "Ishu Ratna <lp@averroescapital.com>")
chk("a CIO raises no recipient warning", d.get("recipient_warning"), "")

# Rule 4: the tool says so when we are not writing to a decision maker.
chk("a gatekeeper title is flagged, not blocked",
    "unlikely to decide" in osvc.lp_recipient_warning(
        {"contact_name": "Sara Ahmed", "contact_email": "sara@x.com", "contact_title": "Executive Assistant"}))
chk("a general enquiries inbox is flagged",
    "general enquiries" in osvc.lp_recipient_warning({"contact_email": "info@x.com"}))
chk("no contact at all is flagged", "InvestorFill" in osvc.lp_recipient_warning({}))
chk("a principal passes clean",
    osvc.lp_recipient_warning({"contact_name": "N Al Thani", "contact_email": "n@x.qa",
                               "contact_title": "Principal"}), "")

# The "I" follows the mailbox that actually sends, so the email never claims a
# sender it is not.
for k in ("INVESTOR_OUTREACH_EMAIL", "INVESTOR_SMTP_PASSWORD", "INVESTOR_OUTREACH_NAME"):
    os.environ.pop(k, None)
d2 = osvc.draft_lp_outreach_email({"name": "Acme", "contact_name": "Jane Roe"})
chk("when Bea's mailbox sends, the name and title are hers",
    "I am Beatrice, Partner at Averroes Capital" in d2["body"])
os.environ["INVESTOR_OUTREACH_EMAIL"] = "lp@averroescapital.com"
os.environ["INVESTOR_SMTP_PASSWORD"] = "app-pass"
os.environ["INVESTOR_OUTREACH_NAME"] = "Ishu Ratna"
chk("no first name -> 'Hello,'", osvc.draft_lp_outreach_email({"name": "X Capital"})["body"].startswith("Hello,"))

f = osvc.draft_lp_followup_email({"name": "Acme", "contact_name": "Jane Roe",
                                  "outreach_draft_subject": "Averroes Capital, an introduction",
                                  "outreach_draft_to": "jane@acme.com"})
chk("follow-up threads as Re: the original subject", f["subject"], "Re: Averroes Capital, an introduction")
chk("follow-up goes to the address actually used", f["to"], "jane@acme.com")
chk("follow-up is a nudge, not a second pitch", len(f["body"].split()) < 35)
chk("follow-up repeats the same ask and offers nothing new",
    "coffee or fifteen minutes" in f["body"] and "attached" not in f["body"].lower())
chk("follow-up has no em dashes", "—" not in f["body"] and "–" not in f["body"])
f2 = osvc.draft_lp_followup_email({"name": "Acme", "outreach_draft_subject": "Re: Averroes Capital, an introduction"})
chk("Re: is never doubled", f2["subject"], "Re: Averroes Capital, an introduction")

print()
print("── Stages mirror the founder loop ──")
chk("Responded sits between Contacted and Meeting",
    INVESTOR_STAGES.index("Contacted") < INVESTOR_STAGES.index("Responded") < INVESTOR_STAGES.index("Meeting"))
chk("parked stages are exactly Passed and Talk Later", set(INVESTOR_PARKED), {"Passed", "Talk Later"})
chk("every parked stage is a real stage", all(p in INVESTOR_STAGES for p in INVESTOR_PARKED))
chk("first-entry stamps exist for Contacted and Responded",
    INVESTOR_STAGE_STAMPS.get("Contacted") == "contacted_at" and INVESTOR_STAGE_STAMPS.get("Responded") == "responded_at")
cols = {n for n, _ in InvestorBQHandler.SCHEMA}
chk("schema holds every column the shared outreach button reads",
    {"outreach_drafted_at", "outreach_sent_at", "contacted_at", "last_reply_at", "status"} <= cols)
chk("schema holds the stamp columns", set(INVESTOR_STAGE_STAMPS.values()) <= cols)
chk("schema holds park reason columns", {"park_reason", "park_reason_detail"} <= cols)
h = InvestorBQHandler(None, "p")
chk("no client -> status update refuses (no silent success)", h.update_status("X", "Contacted"), False)
chk("unknown stage refused", h.update_status("X", "Engaged"), False)

print()
print("── The shared follow-up endpoint knows the investor entity ──")
import inspect  # noqa: E402
import main  # noqa: E402
chk("test investor's emails are forced to Ishu", main.INVESTOR_TEST_RECIPIENT, "iratna@averroescapital.com")
send_src = inspect.getsource(main.send_investor_outreach)
chk("send path forces the test recipient by source = Internal Test",
    "source\") == \"Internal Test\"" in send_src and "INVESTOR_TEST_RECIPIENT" in send_src)
src = inspect.getsource(main.get_followups)
chk("entity parameter present", "entity: str = Query(\"company\"" in src)
chk("investor branch uses the investors table", "investor_handler.table_id" in src)
chk("investor stages filtered to the active loop", "('Contacted', 'Responded', 'Meeting')" in src)
chk("email_log side is filtered by the same entity", "WHERE entity_type = '{entity_type}'" in src)
sync_src = inspect.getsource(main._sync_emails_impl)
chk("sync advances investors only on a GENUINE reply (NON_REPLY_CLASSES)",
    "cls not in bq_handler.NON_REPLY_CLASSES and sender.get(\"status\") == \"Contacted\"" in sync_src)
chk("sync stamps the investor's last reply", "investor_handler.stamp_reply(" in sync_src)

print()
print("── Out-of-office for investors (Ishu, 14 Sep 2026) ──")
import inspect as _insp
ih_src = open("storage/investor_handler.py").read()
chk("investors carry the same OOO columns as targets", '("ooo_until", "STRING"), ("ooo_note", "STRING")' in ih_src)
chk("stamp_ooo clears reply state like the company UPDATE", "last_reply_at = NULL, reply_classification = NULL" in ih_src.split("def stamp_ooo")[1].split("def ")[0])
ooo_src = main_src.split("def _apply_ooo")[1].split("# ── Delivery verification")[0]
chk("_apply_ooo writes investors through investor_handler.stamp_ooo", "investor_handler.stamp_ooo(" in ooo_src)
chk("...pulls a Responded investor back to Contacted (or Researched if never sent)",
    '"Contacted" if row.get("outreach_sent_at") else "Researched"' in ooo_src)
chk("the sync no longer skips investor autoresponders", 'if e.get("entity_type") not in ("company", "investor"):' in main_src)
fu_src = main_src.split('if entity == "investor":')[1].split("else:")[0]
chk("the investor follow-up SQL defers on ooo_until exactly like companies", "NULLIF(t.ooo_until, '')" in fu_src)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
