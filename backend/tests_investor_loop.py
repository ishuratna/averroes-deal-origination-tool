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
sig = osvc.build_signature("Ishu Ratna", "Associate", "lp@averroescapital.com")
chk("signature carries name, title, address and the disclaimer",
    all(x in sig["text"] for x in ("Ishu Ratna", "Associate", "lp@averroescapital.com", "Appointed Representative")))
chk("html signature embeds the logo by CID", f"cid:{osvc.SIG_LOGO_CID}" in sig["html"])

print()
print("── LP email structure v1 ──")
d = osvc.draft_lp_outreach_email({"name": "Acme Family Office", "contact_name": "Jane Roe",
                                  "contact_email": "jane@acme.com", "strategy_preferences": "Growth"})
chk("fallback (no API key) is marked so it is never persisted", d.get("is_fallback"), True)
chk("subject is the quiet house format", d["subject"], "Averroes Capital, Acme Family Office")
chk("greets by first name", d["body"].startswith("Hi Jane,"))
chk("signed by the configured investor sender", "Ishu Ratna" in d["body"])
chk("ZERO em/en dashes anywhere", "—" not in d["body"] and "–" not in d["body"])
chk("fund facts flagged for confirmation, not invented", "[confirm" in d["body"])
chk("portfolio proof present verbatim", osvc.PORTFOLIO_PROOF in d["body"])
chk("ends with Best, (signature added on send)", d["body"].rstrip().endswith("Best,"))
chk("from line reports the investor mailbox", d["from"], "Ishu Ratna <lp@averroescapital.com>")
f = osvc.draft_lp_followup_email({"name": "Acme", "contact_name": "Jane Roe",
                                  "outreach_draft_subject": "Averroes Capital, Acme",
                                  "outreach_draft_to": "jane@acme.com"})
chk("follow-up threads as Re: the original subject", f["subject"], "Re: Averroes Capital, Acme")
chk("follow-up goes to the address actually used", f["to"], "jane@acme.com")
chk("follow-up has no em dashes", "—" not in f["body"])
f2 = osvc.draft_lp_followup_email({"name": "Acme", "outreach_draft_subject": "Re: Averroes Capital, Acme"})
chk("Re: is never doubled", f2["subject"], "Re: Averroes Capital, Acme")

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
sync_src = inspect.getsource(main.sync_emails)
chk("sync advances investors only on a GENUINE reply (NON_REPLY_CLASSES)",
    "cls not in bq_handler.NON_REPLY_CLASSES and sender.get(\"status\") == \"Contacted\"" in sync_src)
chk("sync stamps the investor's last reply", "investor_handler.stamp_reply(" in sync_src)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
