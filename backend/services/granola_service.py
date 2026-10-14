"""
Granola meeting notes, read-only, over the official REST API
(https://docs.granola.ai/introduction). Three endpoints exist: list notes,
get note, list folders. We use the first two.

Key: GRANOLA_API_KEY (a personal key, `grn_...`, created in the desktop app
under Settings > Connectors > API keys with the personal + public scopes).
Set on Cloud Run with --update-env-vars, never in the repo.

The API returns only notes whose AI summary has finished, so a call that
ended minutes ago may appear on the next sync rather than this one. There
are no webhooks; we poll with `updated_after`.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.granola.ai/v1"
PAGE_SIZE = 30            # API maximum
_RATE_SLEEP = 0.25        # 5 req/s sustained


def configured() -> bool:
    return bool(os.getenv("GRANOLA_API_KEY", "").strip())


def _headers() -> Dict[str, str]:
    key = os.getenv("GRANOLA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GRANOLA_API_KEY not configured (set it with --update-env-vars)")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _get(path: str, params: Optional[Dict] = None, retries: int = 3) -> Dict:
    url = f"{BASE_URL}{path}"
    for attempt in range(retries):
        r = requests.get(url, headers=_headers(), params=params or {}, timeout=30)
        if r.status_code == 429 and attempt < retries - 1:
            time.sleep(2.0 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Granola {r.status_code} on {path}: {r.text[:300]}")
        time.sleep(_RATE_SLEEP)
        return r.json()
    raise RuntimeError(f"Granola rate limited on {path}")


def list_notes(days: int = 7, updated_after: Optional[datetime] = None) -> Iterator[Dict]:
    """Every note updated in the window, oldest page first as the API returns
    them. `updated_after` (UTC) beats `days` when given."""
    since = updated_after or (datetime.now(timezone.utc) - timedelta(days=max(1, days)))
    params: Dict = {"updated_after": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "page_size": PAGE_SIZE}
    cursor = None
    while True:
        if cursor:
            params["cursor"] = cursor
        data = _get("/notes", params)
        for n in data.get("notes") or []:
            yield n
        if not data.get("hasMore") or not data.get("cursor"):
            return
        cursor = data["cursor"]


def get_note(note_id: str) -> Dict:
    """Full note: summary (text + markdown), private notes, attendees,
    calendar event, web_url. No transcript (not requested; doctrine: summary
    and Ishu's notes only, per Ishu 15 Oct 2026)."""
    return _get(f"/notes/{note_id}")


def participant_emails(note: Dict) -> List[str]:
    """attendees[].email, calendar_event.invitees[].email, organiser, owner.
    Field names per the Get Note schema (docs.granola.ai/api-reference/get-note)."""
    out: List[str] = []

    def _add(e):
        e = (e or "").strip().lower()
        if e and "@" in e and e not in out:
            out.append(e)

    for a in (note.get("attendees") or []):
        _add(a.get("email") if isinstance(a, dict) else a)
    ev = note.get("calendar_event") or {}
    for a in (ev.get("invitees") or []):
        _add(a.get("email") if isinstance(a, dict) else a)
    _add(ev.get("organiser") or ev.get("organizer"))
    _add((note.get("owner") or {}).get("email"))
    return out


def note_body(note: Dict) -> str:
    """Granola's AI summary (markdown preferred) plus the owner's own private
    notes. `private_notes_*` is returned only when the key belongs to the
    note's owner (Ishu's key, Ishu's notes); for a colleague's note it is
    null and only the summary is stored."""
    parts: List[str] = []
    for key in ("summary_markdown", "summary_text"):
        v = note.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
            break
    for key in ("private_notes_markdown", "private_notes_text"):
        v = note.get(key)
        if isinstance(v, str) and v.strip():
            parts.append("### Private notes\n" + v.strip())
            break
    return "\n\n".join(parts)


def meeting_time(note: Dict) -> Optional[str]:
    """When the MEETING happened (doctrine 3: event time, not sync time):
    the calendar event's scheduled start, else the note's created_at."""
    ev = note.get("calendar_event") or {}
    v = ev.get("scheduled_start_time")
    if isinstance(v, str) and v:
        return v
    return note.get("created_at")
