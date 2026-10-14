"""
Match a Granola meeting to a company in the pipeline. PURE: zero AI, zero
network, so it is testable without BigQuery or Granola (doctrine 4).

A meeting is matched to a company by evidence in this order:

  1. DOMAIN. A participant's email domain equals the company's website or
     contact-email domain. Near-unique, so it alone is enough.
  2. NAME IN TITLE. The company's name appears as a whole phrase in the
     meeting title ("Averroes Capital >< Cavendish (Cyjax)").
  3. NAME IN BODY. The name appears in the AI summary or Ishu's own notes.
     Meetings are often anonymised as "Project X" in the title (Ishu, 15 Oct
     2026: "make sure you read the real name in the notes"), so the body is
     read too. A single mention of a short or common name in a long body is
     only `weak` and is reported, never written.

Our own domain and the obvious third parties (advisers, boardroom rooms) are
never matched: a Cavendish banker on the Cyjax call does not make the meeting
Cavendish's. Generic single-word names ("Bounce", "Beryl") need title evidence
or two body mentions; a multi-word name is distinctive enough on one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

OWN_DOMAINS = {"averroescapital.com"}

# Mail hosts a founder may use; a shared domain says nothing about the company.
PUBLIC_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk",
    "live.com", "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "aol.com", "msn.com", "group.calendar.google.com",
}

# Words that make a one-word company name too generic to match on a single
# body mention (the name is a dictionary word).
_GENERIC_WORDS = {
    "bounce", "beryl", "journey", "glow", "stream", "send", "record", "quantum",
    "fusion", "edge", "open", "dynamic", "charity", "criminal", "games", "capital",
    "group", "global", "digital", "tech", "software", "systems", "solutions",
    "cloud", "data", "labs", "studio", "media", "works", "project",
}

_SUFFIXES = re.compile(
    r"\b(ltd|limited|plc|llp|llc|inc|co|group|holdings|uk|technologies|technology)\b\.?",
    re.I,
)


def domain_of(value: Optional[str]) -> str:
    """'https://www.Foo.co.uk/x' or 'a@Foo.co.uk' -> 'foo.co.uk'. '' when none."""
    if not value:
        return ""
    v = value.strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v.strip(".")


def core_name(name: str) -> str:
    """Strip legal suffixes and punctuation: 'Cyjax Ltd.' -> 'cyjax'."""
    n = _SUFFIXES.sub(" ", name or "")
    n = re.sub(r"[^a-z0-9&' ]+", " ", n.lower())
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _mentions(text: str, phrase: str) -> int:
    if not text or not phrase:
        return 0
    pat = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
    return len(re.findall(pat, text.lower()))


@dataclass
class CompanyRef:
    name: str
    website: str = ""
    contact_email: str = ""
    status: str = ""

    @property
    def domains(self) -> set:
        out = set()
        for v in (self.website, self.contact_email):
            d = domain_of(v)
            if d and d not in PUBLIC_MAIL_DOMAINS and d not in OWN_DOMAINS:
                out.add(d)
        return out


@dataclass
class Match:
    company: str
    confidence: str          # domain | title | body | weak
    evidence: str
    score: int = 0


@dataclass
class Meeting:
    granola_id: str
    title: str = ""
    participant_emails: Sequence[str] = field(default_factory=list)
    body: str = ""           # AI summary + private notes, any order


def _is_generic(core: str) -> bool:
    words = core.split()
    if len(words) >= 2:
        return False
    return len(core) < 4 or core in _GENERIC_WORDS


def match_meeting(meeting: Meeting, companies: Iterable[CompanyRef]) -> List[Match]:
    """Every company the meeting is evidence for, best first. Callers act on
    `confidence in ('domain', 'title', 'body')`; `weak` is report-only."""
    emails = [e.lower() for e in meeting.participant_emails if e and "@" in e]
    part_domains = {domain_of(e) for e in emails}
    part_domains -= OWN_DOMAINS | PUBLIC_MAIL_DOMAINS
    title = (meeting.title or "").lower()
    body = (meeting.body or "").lower()

    out: List[Match] = []
    for c in companies:
        core = core_name(c.name)
        if not core:
            continue
        hit = c.domains & part_domains
        if hit:
            out.append(Match(c.name, "domain", f"participant at {sorted(hit)[0]}", 100))
            continue
        in_title = _mentions(title, core)
        if in_title:
            out.append(Match(c.name, "title", f"'{c.name}' in the meeting title", 80))
            continue
        in_body = _mentions(body, core)
        if not in_body:
            continue
        if _is_generic(core) and in_body < 2:
            out.append(Match(c.name, "weak", f"one mention of '{c.name}' in the notes", 20))
        else:
            out.append(Match(c.name, "body", f"'{c.name}' mentioned {in_body}x in the notes", 60))
    out.sort(key=lambda m: -m.score)
    return out
