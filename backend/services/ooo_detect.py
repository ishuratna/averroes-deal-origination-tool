"""
Out-of-office detection for synced email.

WHY THIS EXISTS: an autoresponder is not a reply. Before this, an "I'm away
until the 15th" bounced a company all the way to Responded, consumed two Gemini
calls (classify + action bucket) and landed on the triage queue as though the
founder had actually engaged. It also reset nothing about the follow-up clock,
so we would chase them again 14 days after our email regardless of the fact
they had told us they were away for a month.

WHAT IT DOES:
  1. Decides whether a received message is an autoresponder. Deterministic:
     header signals first (the RFC-3834 ones real mail systems set), then
     subject/body phrasing. No AI, no cost.
  2. Reads the return date out of the text. Patterns cover the phrasings that
     actually turn up in UK business mail. If none match, ONE cheap ungrounded
     Gemini call is allowed as a fallback (Ishu's call). If that also fails we
     return no date and the caller keeps the plain 14-day rule, rather than
     inventing a date.

THE REMINDER RULE (agreed with Ishu):
    length = days from our email to their stated return date
    if length > 14: remind at length + 1 days after our email
    else:           remind at 14 days
    never sooner than 14 days
`followup_due_date()` is the single implementation of that arithmetic.
"""
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

BASE_FOLLOWUP_DAYS = 14

# ── 1. Is this an autoresponder? ─────────────────────────────────────────────

# Headers a conforming mail system sets on an automatic reply. Checked first
# because they are unambiguous, unlike prose.
_AUTO_HEADER_HINTS = (
    "auto-submitted: auto-replied",
    "auto-submitted: auto-generated",
    "x-autoreply",
    "x-autorespond",
    "x-auto-response-suppress",
    "precedence: auto_reply",
)

# Phrases that mean "I am away", not "here is my answer". Deliberately requires
# a real away-phrase: a founder writing "I'll be out of the office Thursday but
# happy to talk Friday" is a genuine reply, so single weak words are not enough.
_OOO_PHRASES = (
    "out of office", "out of the office", "outofoffice",
    "away from the office", "away from my desk", "currently away",
    "on annual leave", "annual leave", "on holiday", "on vacation",
    "on parental leave", "on maternity leave", "on paternity leave",
    "on sick leave", "off sick",
    "automatic reply", "auto reply", "auto-reply", "autoreply",
    "automatic response", "this is an automated",
    "i am not in the office", "i'm not in the office",
    "limited access to email", "limited access to my email",
    "no access to email", "not checking email", "not be checking email",
    "reduced availability",
)

# Subject prefixes mail clients add to autoresponders, incl. common non-English.
_OOO_SUBJECT_PREFIXES = (
    "automatic reply", "auto reply", "auto-reply", "autoreply",
    "out of office", "out of the office", "away",
    "abwesenheit", "abwesenheitsnotiz",        # German
    "réponse automatique", "absence",          # French
    "respuesta automática", "ausencia",        # Spanish
    "risposta automatica",                     # Italian
    "automatisch antwoord",                    # Dutch
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip().lower()


def is_auto_reply(subject: str = "", body: str = "", headers: str = "") -> bool:
    """True if this looks like an autoresponder rather than a person's reply."""
    h = _norm(headers)
    if any(hint in h for hint in _AUTO_HEADER_HINTS):
        return True

    subj = _norm(subject)
    # Strip Re:/Fwd: so "Re: Automatic reply: ..." still matches.
    subj_core = re.sub(r"^((re|fw|fwd|aw|tr)\s*:\s*)+", "", subj)
    if any(subj_core.startswith(p) for p in _OOO_SUBJECT_PREFIXES):
        return True
    if any(p in subj_core for p in ("out of office", "automatic reply", "auto-reply", "autoreply")):
        return True

    text = _norm(body)
    return any(p in text for p in _OOO_PHRASES)


# ── 2. When are they back? ───────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# Words that introduce a return date. "until"/"till"/"through" describe the
# LAST day away; "on"/"from" describe the first day back. Tracked separately
# because it shifts the answer by a day.
_UNTIL_WORDS = r"(?:until|untill|till|til|thru|through|up to|up until)"
_BACK_WORDS = r"(?:back|return(?:ing)?|returns|available again|in the office|reachable)"

_DATE_NUMERIC = r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?"
_DATE_ISO = r"(\d{4})-(\d{1,2})-(\d{1,2})"
_DAY_MONTH = r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})"
_MONTH_DAY = r"([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?"


def _mk(y: int, m: int, d: int, ref: date) -> Optional[date]:
    try:
        if y < 100:
            y += 2000
        return date(y, m, d)
    except ValueError:
        return None


def _roll_forward(d: Optional[date], ref: date) -> Optional[date]:
    """A date with no year stated means the next occurrence, not the past."""
    if not d:
        return None
    if d >= ref:
        return d
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return None


def _find_dates(text: str, ref: date) -> list:
    """Every plausible date in the text, with the position it was found at."""
    out = []
    for m in re.finditer(_DATE_ISO, text):
        y, mo, d = (int(g) for g in m.groups())
        got = _mk(y, mo, d, ref)
        if got:
            out.append((m.start(), got))
    for m in re.finditer(_DATE_NUMERIC, text):
        a, b, c = m.group(1), m.group(2), m.group(3)
        # UK convention: day first.
        got = _mk(int(c) if c else ref.year, int(b), int(a), ref)
        got = got if c else _roll_forward(got, ref)
        if got:
            out.append((m.start(), got))
    for m in re.finditer(_DAY_MONTH, text):
        mon = _MONTHS.get(m.group(2)[:9])
        if mon:
            got = _roll_forward(_mk(ref.year, mon, int(m.group(1)), ref), ref)
            if got:
                out.append((m.start(), got))
    for m in re.finditer(_MONTH_DAY, text):
        mon = _MONTHS.get(m.group(1)[:9])
        if mon:
            got = _roll_forward(_mk(ref.year, mon, int(m.group(2)), ref), ref)
            if got:
                out.append((m.start(), got))
    return sorted(out)


def parse_return_date(subject: str, body: str, received_on: date) -> Optional[date]:
    """The date they are next reachable, read from the OOO text.

    Returns the first day BACK. A stated "until Friday" means away through
    Friday, so the day back is the Saturday; "back on Monday" is already the
    day back. None when nothing reliable can be read.
    """
    text = _norm(f"{subject} . {body}")
    if not text:
        return None

    # Prefer a date that sits right after an until/back cue, since an OOO body
    # often also carries unrelated dates (a signature, an event, a phone list).
    best: Optional[date] = None
    for pattern, is_until in ((_UNTIL_WORDS, True), (_BACK_WORDS, False)):
        for cue in re.finditer(pattern, text):
            window = text[cue.end():cue.end() + 60]
            found = _find_dates(window, received_on)
            if found:
                d = found[0][1]
                return d + timedelta(days=1) if is_until else d
            # "back on Monday" / "until Friday" — a weekday, no date given.
            wd = re.search(r"\b([a-z]{3,9})day\b", window) or re.search(r"\b(mon|tue|tues|wed|thu|thurs|fri|sat|sun)\b", window)
            if wd:
                key = wd.group(0)
                target = _WEEKDAYS.get(key)
                if target is not None:
                    ahead = (target - received_on.weekday()) % 7 or 7
                    d = received_on + timedelta(days=ahead)
                    return d + timedelta(days=1) if is_until else d

    # No cue matched: fall back to the latest future date mentioned, which for
    # an OOO is nearly always the return date.
    future = [d for _, d in _find_dates(text, received_on) if d >= received_on]
    if future:
        best = max(future)
    return best


def _ai_return_date(subject: str, body: str, received_on: date) -> Optional[date]:
    """ONE ungrounded Gemini call, only when the patterns above found nothing.

    Ungrounded and tiny, so it does not touch the grounded daily budget. The
    model is told to return an empty string rather than guess, and anything
    outside a sane window is discarded.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)
        prompt = f"""This is an out-of-office automatic email reply, received on {received_on.isoformat()}.

Subject: {subject}
Body:
{(body or '')[:1500]}

On what date is the person next available or back at work?
Rules:
- Answer with the FIRST DAY THEY ARE BACK, as YYYY-MM-DD.
- If the text says they are away "until" a day, they are back the day after.
- If no return date is stated, answer with an empty string. Do NOT guess.

Return ONLY valid JSON: {{"return_date": "YYYY-MM-DD or empty string"}}"""
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.0),
        )
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        import json
        raw = json.loads(text[text.find("{"):text.rfind("}") + 1]).get("return_date") or ""
        if not raw.strip():
            return None
        parsed = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
        # Sanity: a return date in the past, or more than a year out, is wrong.
        if parsed < received_on or parsed > received_on + timedelta(days=365):
            logger.info(f"[OOO] discarding implausible AI return date {parsed} (received {received_on})")
            return None
        return parsed
    except Exception as e:
        logger.warning(f"[OOO] AI return-date fallback failed: {e}")
        return None


def detect(subject: str = "", body: str = "", headers: str = "",
           received_on: date = None, allow_ai: bool = True) -> Dict:
    """Full check on one received message.

    Returns {"is_ooo": bool, "until": date|None, "date_source": str}.
    `until` is the first day they are back.
    """
    received_on = received_on or date.today()
    if not is_auto_reply(subject, body, headers):
        return {"is_ooo": False, "until": None, "date_source": ""}

    until = parse_return_date(subject, body, received_on)
    source = "pattern" if until else ""
    if not until and allow_ai:
        until = _ai_return_date(subject, body, received_on)
        source = "ai" if until else ""
    return {"is_ooo": True, "until": until, "date_source": source}


# ── 3. When do we chase them? ────────────────────────────────────────────────

def followup_due_date(sent_on: date, ooo_until: Optional[date] = None) -> date:
    """The agreed reminder rule, in one place.

        length = days from our email to their return date
        if length > 14: remind at length + 1 days after our email
        else:           remind at 14 days
        never sooner than 14 days

    Note that "length + 1 days after our email" is the same day as "the day
    after they are back", which is how it reads on screen.
    """
    base = sent_on + timedelta(days=BASE_FOLLOWUP_DAYS)
    if not ooo_until:
        return base
    length = (ooo_until - sent_on).days
    if length > BASE_FOLLOWUP_DAYS:
        return sent_on + timedelta(days=length + 1)
    return base


def followup_days(sent_on: date, ooo_until: Optional[date] = None) -> int:
    """Same rule expressed as a number of days, for display."""
    return (followup_due_date(sent_on, ooo_until) - sent_on).days
