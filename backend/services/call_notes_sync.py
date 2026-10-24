"""
Pull Granola meetings and attach them to the companies they were about.

The loop, in order (mirrors the email sync in shape):
  1. List notes updated in the window (Granola API, read-only).
  2. Skip any note whose Granola updated_at we already hold (no re-read).
  3. Fetch the full note; build the match input: title, participant emails,
     summary + Ishu's notes.
  4. `call_match.match_meeting` (PURE) against Responded-and-later companies.
  5. For each strong match (domain / title / body) upsert a call_notes row and
     log a `note` activity at the MEETING time. A `weak` match is reported in
     the result for Ishu to read, never written.
  6. Stage: a strong match PROMOTES Responded -> Meeting through the one
     writer (`update_company_status`, which logs status_change). Ishu, 15 Oct
     2026, chose this over notes-only. Meeting and later are never touched;
     Contacted and earlier are never candidates (a call before a reply is not
     a thing the tool can see, and the Responded page owns that gate).

Dry run by default: everything is computed and reported, nothing written.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ai.call_match import CompanyRef, Meeting, match_meeting
from services import granola_service as granola

logger = logging.getLogger(__name__)

STRONG = ("domain", "title", "body")
# Only a meeting WITH the company moves it: someone from the company on the
# call (domain) or the company named in the title. A body match is usually
# our own internal discussion about them ("[Internal] Deal Origination
# Call", 24 Oct 2026): the notes go on the card, the stage does not move.
PROMOTE_ON = ("domain", "title")
PROMOTE_FROM = "Responded"
PROMOTE_TO = "Meeting"
CREATED_BY = "granola-sync"


def _iso_to_dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        s = v.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _participants_line(note: Dict) -> str:
    seen, out = set(), []
    for a in (note.get("attendees") or []):
        e = (a.get("email") or "").strip().lower()
        n = (a.get("name") or "").strip()
        key = e or n
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(f"{n} <{e}>" if n and e else (n or e))
    for e in granola.participant_emails(note):
        if e not in seen:
            seen.add(e)
            out.append(e)
    return ", ".join(out)


def sync_call_notes(bq_handler, days: int = 7, dry_run: bool = True,
                    time_budget_s: int = 240) -> Dict:
    if not granola.configured():
        return {"status": "Unconfigured", "dry_run": dry_run,
                "message": "GRANOLA_API_KEY is not set on this service."}

    started = datetime.now(timezone.utc)
    cand_rows = bq_handler.call_note_candidates()
    companies = [CompanyRef(name=r["name"], website=r.get("website") or "",
                            contact_email=r.get("contact_email") or "",
                            status=r.get("status") or "")
                 for r in cand_rows]
    status_of = {c.name: c.status for c in companies}
    # Parked (Talk later / Not interested) companies take the notes but are
    # never promoted: a person closed them (doctrine 2d).
    parked = {r["name"] for r in cand_rows if (r.get("track") or "") in ("kill", "later")}
    held = bq_handler.call_note_versions()   # granola_id -> updated_at we already copied

    listed = fetched = skipped_unchanged = 0
    matched: List[Dict] = []
    weak: List[Dict] = []
    unmatched: List[Dict] = []
    promoted: List[str] = []
    errors: List[str] = []

    for stub in granola.list_notes(days=days):
        listed += 1
        if (datetime.now(timezone.utc) - started).total_seconds() > time_budget_s:
            errors.append("time budget reached; the rest is picked up on the next run")
            break
        gid = stub.get("id") or ""
        gupd = stub.get("updated_at") or ""
        if gid and held.get(gid) and _iso_to_dt(held[gid]) and _iso_to_dt(gupd) \
                and _iso_to_dt(gupd) <= _iso_to_dt(held[gid]):
            skipped_unchanged += 1
            continue
        try:
            note = granola.get_note(gid)
        except Exception as e:
            errors.append(f"{gid}: {e}")
            continue
        fetched += 1
        meeting = Meeting(granola_id=gid, title=note.get("title") or stub.get("title") or "",
                          participant_emails=granola.participant_emails(note),
                          body=granola.note_body(note))
        matches = match_meeting(meeting, companies)
        strong = [m for m in matches if m.confidence in STRONG]
        if not strong:
            target = weak if matches else unmatched
            target.append({"granola_id": gid, "title": meeting.title,
                           "candidates": [f"{m.company} ({m.evidence})" for m in matches[:3]]})
            continue
        when = granola.meeting_time(note) or stub.get("created_at")
        when_dt = _iso_to_dt(when)
        for m in strong:
            row = {
                "granola_id": gid, "entity_type": "company", "entity_name": m.company,
                "title": meeting.title, "meeting_at": when_dt,
                "participants": _participants_line(note), "body": meeting.body,
                "web_url": note.get("web_url") or f"https://notes.granola.ai/d/{gid}",
                "owner_email": (note.get("owner") or {}).get("email", ""),
                "match_confidence": m.confidence, "match_evidence": m.evidence,
                "granola_updated_at": _iso_to_dt(gupd),
            }
            will_promote = (status_of.get(m.company) == PROMOTE_FROM and m.company not in parked
                            and m.confidence in PROMOTE_ON)
            matched.append({"company": m.company, "title": meeting.title, "meeting_at": when,
                            "confidence": m.confidence, "evidence": m.evidence,
                            "promote": will_promote, "new": gid not in held})
            if dry_run:
                continue
            if not bq_handler.upsert_call_note(row):
                errors.append(f"{gid}: write failed for {m.company}")
                continue
            if gid not in held:
                bq_handler._log_activity(
                    m.company, "note", CREATED_BY,
                    note_text=f"Call: {meeting.title or 'Granola meeting'} ({m.evidence})",
                    event_time=when_dt.isoformat() if when_dt else None)
            if will_promote:
                if bq_handler.update_company_status(m.company, PROMOTE_TO, created_by=CREATED_BY):
                    status_of[m.company] = PROMOTE_TO
                    promoted.append(m.company)
        held[gid] = gupd

    return {
        "status": "Preview" if dry_run else "Success",
        "dry_run": dry_run, "days": days,
        "notes_listed": listed, "notes_read": fetched, "unchanged_skipped": skipped_unchanged,
        "matched": matched, "would_promote" if dry_run else "promoted":
            [m["company"] for m in matched if m["promote"]] if dry_run else promoted,
        "weak_matches": weak, "unmatched": unmatched, "errors": errors,
        "message": (f"{len(matched)} call note(s) across {len({m['company'] for m in matched})} "
                    f"compan{'y' if len({m['company'] for m in matched}) == 1 else 'ies'}; "
                    f"{len(weak)} weak, {len(unmatched)} unmatched."
                    + (" Nothing was written." if dry_run else "")),
    }
