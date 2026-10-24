"""
What an email chain tells us about a deal, PURE (zero AI, zero network).
Ishu, 24 Oct 2026: "whenever there's Project Rise or something like that,
an advisor is already involved ... we can always read the email chain about
the company and see if a call kind of started, but sometimes the advisor
would do a different email chain. So you have to take care of that."

Three signals, all learnt from mail the sync already files:

  CODENAME   "Project Rise" in a subject, attachment name or body of a
             thread that belongs to Placecube means Project Rise IS
             Placecube. Stored on the row; afterwards a new thread, or a
             Granola call, that says "Project Rise" links to Placecube.
  ADVISER    A counterparty on the company's thread who is neither us nor
             at the company's domain (chris@barnsgatesolutions.com on the
             Placecube thread). Stored on the row. When that adviser starts
             a NEW chain, the chain links to the company if it names the
             company or its codename; if the adviser is known for exactly
             one company and names neither, it links but is flagged for a
             person to confirm. An adviser known for several companies
             never links on their address alone.
  INVITE     A calendar invitation in a linked chain ("Invitation: ...",
             an .ics part) means a call is BOOKED. The start time is read
             from the .ics (DTSTART) or the Gmail subject. It is shown on the
             card; it never moves the stage (a cancelled invite must never
             leave a phantom Meeting).

`Aliases` is the stored shape (JSON on `targets.aliases`):
  {"codenames": ["Project Rise"], "advisers": ["chris@barnsgatesolutions.com"],
   "adviser_domains": ["barnsgatesolutions.com"]}
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ai.call_match import OWN_DOMAINS, PUBLIC_MAIL_DOMAINS, domain_of, core_name

# "Project <Name>": the name is capitalised and not a word that makes it an
# ordinary phrase. Codenames are Title Case single tokens ("Rise", "Falcon",
# "Glow"); "Project Update", "Project Plan" are not deals.
_CODENAME_RE = re.compile(r"\bProject\s+([A-Z][A-Za-z0-9]{2,}(?:[ -][A-Z][A-Za-z0-9]{2,})?)\b")
_NOT_CODENAMES = {
    "update", "updates", "plan", "plans", "planning", "manager", "management", "overview",
    "status", "team", "timeline", "proposal", "summary", "review", "scope", "brief",
    "kickoff", "kick", "budget", "report", "list", "name", "title", "x", "lead", "leads",
    "deck", "teaser", "cim", "memo", "notes", "call", "meeting", "agenda", "info",
    "details", "data", "room", "dataroom", "code", "codename", "number", "one", "two",
    "alpha", "beta", "phase", "stage", "finance", "financials", "model", "pipeline",
}

# Subjects Gmail / Outlook / Google Calendar put on invitations.
_INVITE_SUBJECT_RE = re.compile(
    r"^\s*(?:fwd?:\s*)?(invitation|updated invitation|accepted|tentatively accepted|declined|canceled event|cancelled event)\b",
    re.I)
_GCAL_WHEN_RE = re.compile(r"@\s*(?:[A-Z][a-z]{2}\s+)?(\d{1,2})\s+([A-Z][a-z]{2,8})\s+(\d{4})(?:\s+(\d{1,2}(?::\d{2})?)\s*(am|pm)?)?", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def codenames_in(text: str) -> List[str]:
    """Every 'Project X' codename in a piece of text, as written, deduped."""
    out: List[str] = []
    for m in _CODENAME_RE.finditer(text or ""):
        name = m.group(1).strip()
        first = name.split()[0].split("-")[0].lower()
        if first in _NOT_CODENAMES:
            continue
        label = f"Project {name}"
        if label.lower() not in {o.lower() for o in out}:
            out.append(label)
    return out


def mentions_codename(text: str, codenames: Iterable[str]) -> Optional[str]:
    t = (text or "").lower()
    for c in codenames or []:
        if c and re.search(r"(?<![a-z0-9])" + re.escape(c.lower()) + r"(?![a-z0-9])", t):
            return c
    return None


def mentions_company(text: str, company_name: str) -> bool:
    core = core_name(company_name)
    if not core:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(core) + r"(?![a-z0-9])", (text or "").lower()))


def is_adviser_address(email_addr: str, company_domains: Iterable[str], company_name: str = "") -> bool:
    """Not us, not the company, not a personal mailbox: a third party on the
    thread. An adviser, a lawyer, a banker; the rule treats them alike.
    A domain that carries the company's own name is the company on a sister
    domain (flagstoneim.com for Flagstone, 24 Oct 2026) or a same-named
    stranger (realtafusion.com for Realta), never an adviser."""
    d = domain_of(email_addr)
    if not d or d in OWN_DOMAINS or d in PUBLIC_MAIL_DOMAINS:
        return False
    if d in set(company_domains or []):
        return False
    stem = core_name(company_name).split()[0] if core_name(company_name) else ""
    if stem and len(stem) >= 4 and stem in d.split(".")[0]:
        return False
    return True


def parse_aliases(raw) -> Dict[str, List[str]]:
    base = {"codenames": [], "advisers": [], "adviser_domains": []}
    if not raw:
        return base
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return base
    for k in base:
        v = d.get(k) or []
        base[k] = [str(x) for x in v if x]
    return base


def merge_aliases(current, codenames: Iterable[str] = (), advisers: Iterable[str] = ()) -> Tuple[Dict[str, List[str]], bool]:
    """Union into the stored shape. Returns (aliases, changed)."""
    a = parse_aliases(current)
    changed = False
    for c in codenames or []:
        if c and c.lower() not in {x.lower() for x in a["codenames"]}:
            a["codenames"].append(c); changed = True
    for e in advisers or []:
        e = (e or "").strip().lower()
        if e and "@" in e and e not in a["advisers"]:
            a["advisers"].append(e); changed = True
            d = domain_of(e)
            if d and d not in a["adviser_domains"]:
                a["adviser_domains"].append(d)
    return a, changed


def link_by_alias(subject: str, body: str, counter_email: str,
                  codename_index: Dict[str, str],
                  adviser_index: Dict[str, List[str]]) -> Tuple[Optional[str], str]:
    """The FOURTH matching rung for a message no address, domain or thread
    header could place. Returns (company_name, matched_by) or (None, '').
      codename           the subject/body names a known codename
      adviser            a known adviser wrote it AND the text names the
                         company or its codename
      adviser-unconfirmed a known adviser for exactly ONE company wrote it
                         and named neither: linked, flagged for a person
    An adviser known for several companies and naming none links nothing."""
    text = f"{subject or ''}\n{body or ''}"
    hit = mentions_codename(text, codename_index.keys())
    if hit:
        return codename_index[hit], "codename"
    cands = adviser_index.get((counter_email or "").lower()) or []
    if not cands:
        return None, ""
    for company in cands:
        if mentions_company(text, company):
            return company, "adviser"
    if len(cands) == 1:
        return cands[0], "adviser-unconfirmed"
    return None, ""


def invite_from_subject(subject: str) -> Optional[Dict]:
    """Gmail: 'Invitation: Placecube <> Averroes @ Tue 28 Oct 2026 3pm - 3:30pm (BST)'."""
    s = subject or ""
    m = _INVITE_SUBJECT_RE.match(s)
    if not m:
        return None
    kind = m.group(1).lower()
    when = None
    w = _GCAL_WHEN_RE.search(s)
    if w:
        try:
            day, mon, year = int(w.group(1)), _MONTHS.get(w.group(2)[:3].lower()), int(w.group(3))
            hour, minute = 0, 0
            if w.group(4):
                hh, _, mm = w.group(4).partition(":")
                hour, minute = int(hh), int(mm or 0)
                if (w.group(5) or "").lower() == "pm" and hour < 12:
                    hour += 12
            if mon:
                when = datetime(year, mon, day, hour, minute, tzinfo=timezone.utc)
        except Exception:
            when = None
    title = re.sub(r"^\s*(?:fwd?:\s*)?[^:]+:\s*", "", s, count=1)
    title = re.split(r"\s@\s", title)[0].strip()
    status = "cancelled" if "cancel" in kind else ("declined" if kind == "declined" else "booked")
    return {"status": status, "start": when, "title": title}


def invite_from_ics(ics_text: str) -> Optional[Dict]:
    """DTSTART/SUMMARY/METHOD/STATUS from a text/calendar part."""
    if not ics_text or "BEGIN:VEVENT" not in ics_text:
        return None
    def _field(name):
        m = re.search(r"^" + name + r"(?:;[^:\n]*)?:(.+)$", ics_text, re.M)
        return (m.group(1).strip() if m else "")
    raw = _field("DTSTART")
    when = None
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", raw)
    if m:
        try:
            when = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=timezone.utc)
        except Exception:
            when = None
    method = _field("METHOD").upper()
    status = "cancelled" if method == "CANCEL" or _field("STATUS").upper() == "CANCELLED" else "booked"
    return {"status": status, "start": when, "title": _field("SUMMARY")}
