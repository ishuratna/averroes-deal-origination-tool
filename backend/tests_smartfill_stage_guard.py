#!/usr/bin/env python3
"""
SmartFill must never move a company backwards out of a stage that records work
already done.

Anchored on a REAL loss (FoundIt!, 10 Sep 2026). The row sat at Responded: we
had emailed them and they had replied. A SmartFill re-run, triggered only to
attach the Companies House number we had just found, wrote status = 'Qualified'
and reset stage_entered_at. Nothing was logged, so the demotion left no trace
and reconciliation could not see it. The conversation simply vanished from the
Responded queue.

The cause is structural, not a typo: qualify_company_with_gemini reads the
RECORD. It has no idea an email was ever sent, so for a company mid-conversation
it correctly answers "Qualified" — and the write took that as an instruction.

Enrichment improves what we KNOW. It does not undo what we DID.
"""
import inspect
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from storage.bq_handler import BigQueryHandler  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("-- which stages are protected --")
W = set(BigQueryHandler.WORK_DONE_STAGES)
chk("every stage that means we contacted or spoke to them",
    {"Contacted", "Responded", "Meeting", "DD", "Offer"} <= W)
chk("closed outcomes too: a decision is work", {"Won", "Lost"} <= W)
chk("Qualified is NOT protected: nothing has happened yet", "Qualified" in W, False)
chk("Not a Fit is NOT protected: it is a verdict, and re-judging it is the point",
    "Not a Fit" in W, False)
chk("every protected stage is a real stage", W <= set(BigQueryHandler.DEAL_STAGES))

print()
print("-- the SmartFill write --")
import main  # noqa: E402
src = inspect.getsource(main.smartfill_company)
chk("status is written through the guard, never bare",
    "status = CASE WHEN status IN UNNEST(@protected) THEN status ELSE @status END" in src)
chk("no bare 'status = @status,' survives anywhere in the write",
    re.search(r"^\s*status = @status,", src, re.M) is None)
chk("the protected list comes from the handler, not a second copy",
    "bq_handler.WORK_DONE_STAGES" in src)
chk("unfit_reason is not cleared on a protected row either",
    "unfit_reason = CASE WHEN status IN UNNEST(@protected) THEN unfit_reason ELSE '' END" in src)
chk("stage_entered_at is not reset on a protected row",
    "stage_entered_at = CASE WHEN status NOT IN UNNEST(@protected)" in src)
chk("the parameter is actually bound", 'ArrayQueryParameter("protected", "STRING", _protected)' in src)
chk("the returned new_status reports what was KEPT, not what was proposed",
    "new_status = _prior_status" in src)

print()
print("-- doctrine 2a: a status write is logged or it is invisible --")
chk("SmartFill logs status_change when the stage really moved",
    '_log_activity(company_name, "status_change", "smartfill"' in src)
chk("...and only when it moved", 'if _prior_status and _prior_status != new_status:' in src)

print()
print("-- the same rule on the Quick Research verdict path --")
chk("a Not a Fit verdict cannot demote a company we have already contacted",
    "WHERE name = @n AND status NOT IN UNNEST(@protected)" in src)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
