#!/usr/bin/env python3
"""
The Responded page's ONE derivation: _responded_group (main.py).

Every header stat and every section list renders whatever this function says,
so its decision tree is pinned here case by case. v3, agreed 21 Aug 2026:

  Nurture (Ishu) -> Assignment ready (Ishu's click) -> routed to Bea's
  Thursday list (A) or the associates' Wednesday list (B) -> confirmed to an
  owner -> a Meeting takes it off the page. Talk-later sleeps 6 months and
  wakes into Assignment ready; kill/Lost/Not a Fit close it.
"""
import os
import sys
import warnings
from datetime import date, timedelta

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from main import _responded_group, TALK_LATER_DAYS  # noqa: E402

fails = 0


def chk(label, row, want):
    global fails
    got = _responded_group({"status": "Responded", **row})
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Section 1: Nurture (Ishu) ──")
chk("fresh reply, nothing decided -> Nurture", {}, "nurture")
chk("empty-string track is no track", {"track": ""}, "nurture")
chk("Ishu clicked Ready to assign", {"assignment_ready_at": "2026-08-21T10:00:00"}, "assignment_ready")

print()
print("── Section 2: the weekly lists (two-step, owner confirms) ──")
chk("routed A, unconfirmed -> Thursday discussion", {"track": "A"}, "bea_review")
chk("routed A, confirmed to Bea -> Section 3", {"track": "A", "owner": "Bea"}, "bea_assigned")
chk("routed A, someone ELSE as owner is still unconfirmed", {"track": "A", "owner": "Issam"}, "bea_review")
chk("routed B, unallocated -> Wednesday discussion", {"track": "B"}, "assoc_review")
chk("routed B, allocated to Issam -> call pending", {"track": "B", "owner": "Issam"}, "assoc_pending")
chk("routed B, allocated to Marianna -> call pending", {"track": "B", "owner": "Marianna"}, "assoc_pending")
chk("routed B, Bea as owner is NOT an allocation", {"track": "B", "owner": "Bea"}, "assoc_review")

print()
print("── Progressed: a booked meeting takes it off the page ──")
for st in ("Meeting", "DD", "Offer", "Won"):
    chk(f"{st} is progressed even mid-route", {"status": st, "track": "B", "owner": "Issam"}, "progressed")

print()
print("── Talk later: asleep 6 months, then wakes needing a routing decision ──")
fresh = (date.today() - timedelta(days=5)).isoformat()
expired = (date.today() - timedelta(days=TALK_LATER_DAYS + 1)).isoformat()
boundary = (date.today() - timedelta(days=TALK_LATER_DAYS)).isoformat()
chk("parked 5 days ago -> asleep", {"track": "later", "triaged_at": fresh}, "talk_later")
chk("parked 6+ months ago -> wakes into Assignment ready", {"track": "later", "triaged_at": expired}, "assignment_ready")
chk("exactly at the boundary -> awake (>= is the rule)", {"track": "later", "triaged_at": boundary}, "assignment_ready")
# The falsy guard: _as_date falls back to TODAY for unparseable input, which
# would make a missing timestamp look freshly parked and sleep it forever.
chk("parked with NO timestamp -> awake, never lost", {"track": "later", "triaged_at": None}, "assignment_ready")
chk("parked with empty timestamp -> awake", {"track": "later", "triaged_at": ""}, "assignment_ready")

print()
print("── Closed beats everything ──")
chk("killed -> closed", {"track": "kill"}, "closed")
chk("killed even when routed first", {"track": "kill", "owner": "Bea"}, "closed")
chk("Lost -> closed regardless of track", {"status": "Lost", "track": "A", "owner": "Bea"}, "closed")
chk("Not a Fit -> closed", {"status": "Not a Fit"}, "closed")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
