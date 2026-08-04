"""
Contact finder: deterministic email discovery from the company's OWN website.

Runs before/alongside AI enrichment. First-party pages (contact, about, team,
legal, homepage) are the most trustworthy source of a founder email that
exists — better than any aggregator, free, and fast. No AI involved.
"""
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_PATHS = ["", "/contact", "/contact-us", "/about", "/about-us", "/team", "/company", "/legal", "/privacy", "/imprint"]
_TIMEOUT = 6
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AverroesIntel/1.0; +https://averroescapital.com)"}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Obvious non-contact addresses and file-name lookalikes
_JUNK_PREFIXES = ("noreply", "no-reply", "donotreply", "notifications", "example",
                  "sentry", "wixpress", "godaddy", "email@", "user@", "name@")
_JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
_GENERIC_PREFIXES = ("hello", "info", "contact", "team", "enquiries", "inquiries",
                     "office", "admin", "support", "sales", "press", "hi")


def _clean_domain(website: str) -> Optional[str]:
    if not website:
        return None
    host = urlparse(website if website.startswith("http") else f"https://{website}").netloc
    return host.replace("www.", "").lower() or None


def _extract_emails(html: str) -> List[str]:
    emails = set()
    # mailto: links first — the strongest signal a site publishes an address
    for m in re.finditer(r'mailto:([^"\'>?\s]+)', html, re.I):
        emails.add(m.group(1).strip().lower())
    for m in _EMAIL_RE.finditer(html):
        emails.add(m.group(0).strip().lower())
    out = []
    for e in emails:
        if any(e.startswith(p) for p in _JUNK_PREFIXES):
            continue
        if any(e.endswith(s) for s in _JUNK_SUFFIXES):
            continue
        out.append(e)
    return out


def find_site_emails(website: str, contact_name: str = "") -> Dict:
    """
    Scrape the company's own site for published emails.
    Returns {"email": best_pick or "", "source": page_url, "all": [...],
             "pages": {email: page_url}} — personal addresses at the company
    domain beat generic ones; the contact's first/last name (when known) beats
    other personal addresses. `pages` lets the caller cite the exact page for
    ANY address, not just the top pick (the waterfall needs that for the
    colleague and outreach fallbacks).
    """
    domain = _clean_domain(website)
    if not domain:
        return {"email": "", "source": "", "all": [], "pages": {}}

    base = f"https://{domain}"
    found: Dict[str, str] = {}  # email -> page found on
    for path in _PATHS:
        url = urljoin(base, path)
        try:
            resp = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS, allow_redirects=True)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                continue
            for e in _extract_emails(resp.text[:400_000]):
                found.setdefault(e, url)
        except Exception:
            continue

    if not found:
        return {"email": "", "source": "", "all": [], "pages": {}}

    # Rank: same-domain first; then name-matching personal > personal > generic
    name_bits = [w for w in re.sub(r"[^a-z ]", "", (contact_name or "").lower()).split() if len(w) > 2]

    def score(e: str) -> tuple:
        local, _, dom = e.partition("@")
        same_domain = dom == domain or dom.endswith("." + domain)
        name_match = any(b in local for b in name_bits) if name_bits else False
        generic = any(local == g or local.startswith(g) for g in _GENERIC_PREFIXES)
        return (same_domain, name_match, not generic)

    best = sorted(found.keys(), key=score, reverse=True)[0]
    logger.info(f"[ContactFinder] {domain}: {len(found)} email(s) on site; picked {best}")
    return {"email": best, "source": found[best], "all": sorted(found.keys()), "pages": dict(found)}


# ── Who does an address belong to? ─────────────────────────────────────────
# The waterfall is FOUNDER-FIRST, and the reason this classification exists is
# that a PERSONAL address is not automatically the FOUNDER'S address. The old
# v3 waterfall returned on the first personal address it saw, so a sales
# manager's email ended the search and the founder was never pursued. Now
# every address is labelled, non-founder addresses are only ever HELD as
# fallbacks, and the ladder keeps climbing towards the founder.

import os as _os

_FOUNDER_ROLE_PREFIXES = ("ceo", "founder", "founders", "cofounder", "co-founder",
                          "md", "managingdirector", "managing.director", "chairman")
# Which shared inbox to prefer if we end up writing to one: the addresses a
# company actually watches for inbound enquiries, before internal functions.
_OUTREACH_PREFERENCE = ("hello", "hi", "info", "contact", "enquiries", "inquiries",
                        "office", "team", "admin", "sales", "support", "press")


def is_generic_address(email: str) -> bool:
    local = (email or "").split("@")[0].lower()
    return any(local == g or local.startswith(g) for g in _GENERIC_PREFIXES)


def _name_bits(name: str) -> List[str]:
    return [w for w in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(w) > 1]


def _local_tokens(email: str) -> List[str]:
    local = (email or "").split("@")[0].lower()
    return [t for t in re.split(r"[._\-+0-9]+", local) if t]


def is_founder_role_address(email: str) -> bool:
    """ceo@, founder@, md@ — a ROLE mailbox that reaches the person we want.
    Published by the company, so it is evidence, not a guess; it ranks below a
    named founder address but above any guesswork."""
    local = (email or "").split("@")[0].lower().replace(" ", "")
    return any(local == p or local.startswith(p) for p in _FOUNDER_ROLE_PREFIXES)


def founder_match_strength(email: str, person_name: str) -> int:
    """How strongly an address carries this person's name. 2 = first AND last
    (e.g. john.smith / jsmith for John Smith), 1 = one of the two, 0 = neither.
    Reported honestly downstream: a first-name-only match could be a namesake."""
    local = (email or "").split("@")[0].lower()
    bits = _name_bits(person_name)
    if not local or not bits:
        return 0
    first = bits[0]
    last = bits[-1] if len(bits) > 1 else ""
    tokens = _local_tokens(email)
    squashed = re.sub(r"[^a-z]", "", local)
    if last:
        both_forms = (f"{first}{last}", f"{first}.{last}", f"{first[0]}{last}",
                      f"{first}{last[0]}", f"{last}{first[0]}", f"{last}{first}")
        if squashed in [re.sub(r"[^a-z]", "", f) for f in both_forms]:
            return 2
        if first in tokens and last in tokens:
            return 2
        if first in tokens or last in tokens:
            return 1
    if first in tokens or squashed == first:
        return 1
    return 0


def classify_address(email: str, founder_name: str = "") -> str:
    """founder | founder_role | colleague | generic | personal_unknown.
    'personal_unknown' is the honest label for a personal address when we have
    no founder name to compare against — we cannot claim it is the founder's,
    and we cannot guess an alternative either."""
    if not email:
        return ""
    if is_generic_address(email):
        return "generic"
    if is_founder_role_address(email):
        return "founder_role"
    if not (founder_name or "").strip():
        return "personal_unknown"
    return "founder" if founder_match_strength(email, founder_name) > 0 else "colleague"


def display_name_from_address(email: str) -> str:
    """Best-effort human name from a personal address, used ONLY to greet a
    colleague by their own name. Returns '' when the local part does not
    decompose into something plainly name-like — we would rather write
    'Hello,' than invent a name."""
    tokens = [t for t in _local_tokens(email) if t.isalpha()]
    tokens = [t for t in tokens if len(t) > 2]
    if len(tokens) >= 2:
        return " ".join(t.capitalize() for t in tokens[:2])
    if len(tokens) == 1 and not is_generic_address(email) and not is_founder_role_address(email):
        return tokens[0].capitalize()
    return ""


# ── Hunter.io: verifier + email-finder ─────────────────────────────────────
# GCP blocks outbound SMTP, so mailbox checks go through Hunter. Configure with
# HUNTER_API_KEY. Two different endpoints, two different jobs:
#   email-verifier — "does THIS address exist?" (used to test our guesses)
#   email-finder   — "what is THIS PERSON's address at this domain?" (returns
#                    what Hunter has crawled from public sources, with a
#                    confidence score and the source URLs). That is EVIDENCE,
#                    so it sits above blind pattern guessing in the ladder.
# Without a key, both report unavailable and the ladder falls back to what it
# found in sources.

def verify_email_detail(email: str) -> Dict:
    """The verifier call with its failure mode intact.

    Returns {"status", "http", "detail"} where status is one of:
      deliverable | undeliverable | catch_all | unknown | unavailable | error

    WHY THIS EXISTS: the old version collapsed "Hunter says it is unclear" and
    "the Hunter call did not work at all" into the same 'unknown'. A rejected or
    exhausted key therefore looked exactly like a genuinely ambiguous mailbox,
    and the guessing rungs went silently inert. 'error' now means OUR call
    failed (auth, quota, network) and must be reported, never treated as a
    verdict about the address.

    catch_all means the domain answers "yes" to EVERY address, so the check
    proves nothing about this specific mailbox. Policy (Ishu, agreed): we still
    send to the guess on those domains, but we record it as unconfirmed.
    """
    api_key = _os.getenv("HUNTER_API_KEY", "") or _os.getenv("EMAIL_VERIFIER_API_KEY", "")
    if not api_key:
        return {"status": "unavailable", "http": 0, "detail": "no verifier key configured on this service"}
    if not email:
        return {"status": "unavailable", "http": 0, "detail": "no email given"}
    try:
        resp = requests.get("https://api.hunter.io/v2/email-verifier",
                            params={"email": email, "api_key": api_key}, timeout=15)
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        # Hunter reports auth/quota problems as HTTP 4xx with an errors[] body.
        if resp.status_code >= 400:
            errs = payload.get("errors") or []
            msg = "; ".join(str((e or {}).get("details") or e) for e in errs) or resp.text[:200]
            logger.warning(f"[ContactFinder] verifier HTTP {resp.status_code} for {email}: {msg}")
            return {"status": "error", "http": resp.status_code, "detail": msg}
        data = payload.get("data", {}) or {}
        status, result = data.get("status", ""), data.get("result", "")
        if status == "accept_all":
            return {"status": "catch_all", "http": resp.status_code, "detail": "domain accepts mail at any address"}
        if result == "deliverable" or status == "valid":
            return {"status": "deliverable", "http": resp.status_code, "detail": status or result}
        if result == "undeliverable" or status == "invalid":
            return {"status": "undeliverable", "http": resp.status_code, "detail": status or result}
        return {"status": "unknown", "http": resp.status_code, "detail": status or result or "no verdict returned"}
    except Exception as e:
        logger.warning(f"[ContactFinder] verifier call failed for {email}: {e}")
        return {"status": "error", "http": 0, "detail": f"{type(e).__name__}: {e}"}


def verify_email(email: str) -> str:
    """Back-compatible wrapper: just the status string."""
    return verify_email_detail(email)["status"]


def find_email_by_name(domain: str, person_name: str) -> Dict:
    """Hunter email-finder: domain + person -> the address Hunter has on record.

    Returns {"email", "score", "sources": n, "url": first source url}.
    `sources` > 0 means Hunter has actually seen this address published
    somewhere, which we treat as found-in-source. With a score but no sources
    it is Hunter's own prediction, so we push it into the verify queue
    instead of trusting it outright. Costs one Hunter request.
    """
    blank = {"email": "", "score": 0, "sources": 0, "url": "", "error": ""}
    api_key = _os.getenv("HUNTER_API_KEY", "") or _os.getenv("EMAIL_VERIFIER_API_KEY", "")
    bits = _name_bits(person_name)
    if not api_key or not domain or not bits:
        return blank
    params = {"domain": domain, "api_key": api_key, "first_name": bits[0]}
    if len(bits) > 1:
        params["last_name"] = bits[-1]
    try:
        resp = requests.get("https://api.hunter.io/v2/email-finder", params=params, timeout=15)
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        if resp.status_code >= 400:
            errs = payload.get("errors") or []
            msg = "; ".join(str((e or {}).get("details") or e) for e in errs) or resp.text[:200]
            logger.warning(f"[ContactFinder] email-finder HTTP {resp.status_code}: {msg}")
            return {**blank, "error": f"HTTP {resp.status_code}: {msg}"}
        data = payload.get("data", {}) or {}
        srcs = data.get("sources") or []
        return {"email": (data.get("email") or "").strip().lower(),
                "score": int(data.get("score") or 0),
                "sources": len(srcs),
                "url": (srcs[0].get("uri") if srcs and isinstance(srcs[0], dict) else "") or "",
                "error": ""}
    except Exception as e:
        logger.warning(f"[ContactFinder] email-finder failed for {person_name}@{domain}: {e}")
        return {**blank, "error": f"{type(e).__name__}: {e}"}


# ── Pattern learning ──────────────────────────────────────────────────────
# We learn the company's address SHAPE from an address we already found, then
# render the FOUNDER's name into that shape. This is the part that was dead
# code in v3: the branch meant to mirror a colleague's pattern fell through to
# `pass`, so guessing was really just a fixed list in a blind order.

_DEFAULT_SHAPES = ["first.last", "first", "flast", "firstlast", "f.last"]
_MAX_VERIFY = 5


def observed_shape(email: str) -> Optional[str]:
    """Infer the shape of a company's addresses from ONE observed address,
    without knowing whose name it is. 'john.smith' -> first.last,
    'j.smith' -> f.last. A single unseparated token stays ambiguous (None):
    'jsmith', 'johns' and 'john' cannot be told apart without the name."""
    local = (email or "").split("@")[0].lower()
    for sep, shape in ((".", "first.last"), ("_", "first_last"), ("-", "first-last")):
        if sep in local:
            a, _, b = local.partition(sep)
            if not (a and b) or not (a.isalpha() and b.isalpha()):
                return None
            if sep == ".":
                if len(a) == 1:
                    return "f.last"
                if len(b) == 1:
                    return "first.l"
            return shape
    return None


def render_shape(shape: str, first: str, last: str) -> str:
    if not first:
        return ""
    if not last:
        return first if shape == "first" else ""
    return {
        "first.last": f"{first}.{last}",
        "first_last": f"{first}_{last}",
        "first-last": f"{first}-{last}",
        "f.last": f"{first[0]}.{last}",
        "first.l": f"{first}.{last[0]}",
        "first": first,
        "flast": f"{first[0]}{last}",
        "firstlast": f"{first}{last}",
        "firstl": f"{first}{last[0]}",
    }.get(shape, "")


def _guess_candidates(founder_name: str, domain: str, observed: List[str]) -> List[tuple]:
    """Founder-address candidates as (email, why), best first.

    Shapes learned from colleagues at THIS domain come first (most frequently
    observed shape wins), then the common formats. Guesses only — the caller
    must test them.
    """
    bits = _name_bits(founder_name)
    if not bits or not domain:
        return []
    first, last = bits[0], (bits[-1] if len(bits) > 1 else "")

    counts: Dict[str, int] = {}
    for obs in observed or []:
        if obs.split("@")[-1].lower() != domain or is_generic_address(obs) or is_founder_role_address(obs):
            continue
        shape = observed_shape(obs)
        if shape:
            counts[shape] = counts.get(shape, 0) + 1
    learned = sorted(counts, key=lambda s: -counts[s])

    out: List[tuple] = []
    seen = set()

    def add(shape: str, why: str):
        local = render_shape(shape, first, last)
        if not local:
            return
        email = f"{local}@{domain}"
        if email in seen:
            return
        seen.add(email)
        out.append((email, why))

    for s in learned:
        add(s, f"matches the {s} pattern used by other people at this company")
    for s in _DEFAULT_SHAPES:
        add(s, f"common {s} format")
    return out[:_MAX_VERIFY]


# ── The waterfall ─────────────────────────────────────────────────────────

def resolve_contact_email(website: str, contact_name: str, ai_email: str, ai_source: str,
                          retry_fn=None) -> Dict:
    """
    FOUNDER-FIRST contact waterfall (v4). Rungs, in order:

      1. A NAMED FOUNDER address already found in a source (grounded web search
         result, or the company's own site).
      2. RETRY LADDER — one sharper grounded search for the founder.
      3. HUNTER EMAIL-FINDER — the address Hunter has crawled for this person.
         With public sources behind it, that is evidence, so it is accepted.
      4. FOUNDER ROLE MAILBOX published by the company (ceo@, founder@).
      5. PATTERN GUESS, tested with Hunter: shapes learned from colleagues at
         the same domain first, then common formats. A strict `deliverable`
         accepts it. On a catch-all domain (every address "passes") we accept
         the best-ranked guess but record it as unconfirmed — Ishu's call.
      6. A COLLEAGUE: a real person who works there, found in a source.
      7. The company's OUTREACH inbox (hello@ / info@ / contact@).

    THE POINT OF v4: rungs 6 and 7 are only ever HELD as fallbacks. Finding a
    colleague's address no longer ends the search — we keep working on the
    founder, and we use the colleague's address to LEARN the company's email
    pattern (rung 5), which is exactly what makes the guess worth testing.

    retry_fn: zero-arg callable returning {"contact_email","email_source"}.

    Returns {"email", "source", "verification", "kind", "recipient_name",
             "step", "founder_guess", "founder_guess_status"} where kind is
    founder | colleague | generic and recipient_name is who the To: belongs to
    ('' for a shared inbox). Both drive the greeting in outreach_service.
    """
    ai_email = (ai_email or "").strip().lower()
    site = find_site_emails(website, contact_name)
    pages = site.get("pages", {}) or {}
    domain = _clean_domain(website) or (ai_email.split("@")[-1] if "@" in ai_email else "")
    verifier_on = bool(_os.getenv("HUNTER_API_KEY", "") or _os.getenv("EMAIL_VERIFIER_API_KEY", ""))
    founder_named = bool((contact_name or "").strip())

    def _out(email, source, verification, kind, step, recipient_name="",
             guess="", guess_status=""):
        return {"email": email or "", "source": source or "", "verification": verification,
                "kind": kind, "recipient_name": recipient_name, "step": step,
                "founder_guess": guess, "founder_guess_status": guess_status}

    # Everything seen so far, each with a citation for where it came from.
    seen: List[tuple] = []
    if ai_email:
        seen.append((ai_email, ai_source or "AI web search"))
    for e in site.get("all", []):
        seen.append((e, f"company website ({pages.get(e) or website})"))

    def _best(kind_wanted: str) -> tuple:
        """Highest-quality address of a given kind, with its citation."""
        cands = [(e, s) for e, s in seen if classify_address(e, contact_name) == kind_wanted]
        if not cands:
            return ("", "")
        if kind_wanted in ("founder", "personal_unknown"):
            cands.sort(key=lambda p: -founder_match_strength(p[0], contact_name))
        elif kind_wanted == "colleague":
            # A local part we can turn into a name lets us greet them properly.
            cands.sort(key=lambda p: (bool(display_name_from_address(p[0])),
                                      p[0].split("@")[-1] == domain), reverse=True)
        elif kind_wanted == "generic":
            def rank(e):
                local = e.split("@")[0].lower()
                for i, p in enumerate(_OUTREACH_PREFERENCE):
                    if local.startswith(p):
                        return (0 if e.split("@")[-1] == domain else 1, i)
                return (2, 99)
            cands.sort(key=lambda p: rank(p[0]))
        return cands[0]

    # ── Rung 1: a named founder address that already exists in a source ────
    f_email, f_src = _best("founder")
    if f_email:
        strength = founder_match_strength(f_email, contact_name)
        note = ("found in source, matches the founder's name" if strength == 2
                else "found in source, matches the founder's first name only")
        return _out(f_email, f_src, note, "founder", "1. founder found in source", contact_name)

    # No founder name on record: we cannot tell a founder's address from a
    # colleague's, and we cannot guess one either. Take the personal address
    # and say plainly what we do and do not know.
    if not founder_named:
        p_email, p_src = _best("personal_unknown")
        if p_email:
            return _out(p_email, p_src, "found in source; no founder name on record, so we cannot confirm whose address this is",
                        "colleague", "1. personal address found (founder unknown)",
                        display_name_from_address(p_email))

    # ── Rung 2: retry ladder — one sharper grounded search ────────────────
    retry_email = ""
    if retry_fn and founder_named:
        try:
            retry = retry_fn() or {}
            retry_email = (retry.get("contact_email") or "").strip().lower()
            if retry_email:
                r_src = retry.get("email_source") or "retry web search"
                seen.append((retry_email, r_src))
                if classify_address(retry_email, contact_name) == "founder":
                    return _out(retry_email, r_src, "found in source on a second, sharper search",
                                "founder", "2. retry search", contact_name)
        except Exception as e:
            logger.warning(f"[ContactFinder] retry ladder failed: {e}")

    # ── Rung 3: Hunter email-finder — what Hunter has crawled for this person
    finder = {"email": "", "score": 0, "sources": 0, "url": "", "error": ""}
    if verifier_on and founder_named and domain:
        finder = find_email_by_name(domain, contact_name)
        if finder["email"] and finder["sources"] > 0:
            return _out(finder["email"], finder["url"] or "Hunter email-finder",
                        f"published in {finder['sources']} public source(s) Hunter has crawled (confidence {finder['score']}%)",
                        "founder", "3. Hunter email-finder", contact_name)

    # ── Rung 4: a founder ROLE mailbox the company publishes itself ────────
    role_email, role_src = _best("founder_role")

    # ── Rung 5: pattern guess, tested ─────────────────────────────────────
    guess, guess_status, verifier_broken = "", "", False
    if verifier_on and founder_named and domain:
        observed = [e for e, _ in seen]
        candidates = _guess_candidates(contact_name, domain, observed)
        # Hunter's own unsourced prediction is worth testing first.
        if finder["email"] and finder["email"] not in [c for c, _ in candidates]:
            candidates.insert(0, (finder["email"],
                                  f"Hunter's predicted address (confidence {finder['score']}%, no public source)"))
        for cand, why in candidates[:_MAX_VERIFY]:
            if cand in observed:
                continue
            detail = verify_email_detail(cand)
            v = detail["status"]
            logger.info(f"[ContactFinder] guess {cand} ({why}) -> {v}")
            if v == "error":
                # OUR call failed (auth, quota, network). That is not a verdict
                # about the address, so testing more candidates would just
                # repeat the same failure. Stop, and say so out loud rather than
                # reporting "no guess passed" as if we had actually checked.
                guess, guess_status = cand, f"not checked, verifier error: {detail['detail']}"[:200]
                verifier_broken = True
                break
            if v == "deliverable":
                return _out(cand, f"inferred: {why}", "mailbox confirmed by Hunter (deliverable)",
                            "founder", "5. pattern guess, confirmed", contact_name)
            if v == "catch_all":
                # Every address on this domain "passes", so this proves nothing.
                # Agreed policy: send to it anyway, flagged as unconfirmed, and
                # stop burning credits on further candidates here.
                return _out(cand, f"inferred: {why}",
                            "NOT confirmed: this domain accepts mail at any address, so the guess could not be checked",
                            "founder", "5. pattern guess, unconfirmed (catch-all domain)", contact_name,
                            guess=cand, guess_status="catch_all")
            if not guess:
                guess, guess_status = cand, v  # remember the top guess we rejected

    if role_email:
        return _out(role_email, role_src,
                    "found in source: a role mailbox the company publishes for its founder/CEO",
                    "founder", "4. founder role mailbox", contact_name,
                    guess=guess, guess_status=guess_status)

    # ── Rung 6: a colleague — a real person who works there ───────────────
    c_email, c_src = _best("colleague")
    if c_email:
        return _out(c_email, c_src, "found in source: a person who works there, not the founder",
                    "colleague", "6. colleague at the company",
                    display_name_from_address(c_email), guess=guess, guess_status=guess_status)

    # ── Rung 7: the company's outreach inbox ──────────────────────────────
    g_email, g_src = _best("generic")
    if g_email:
        return _out(g_email, g_src, "found in source: the company's shared enquiries inbox",
                    "generic", "7. company outreach inbox", "",
                    guess=guess, guess_status=guess_status)

    if not verifier_on:
        return _out("", "", "no published email found (no Hunter key configured, so no guess could be tested)",
                    "", "exhausted")
    if not founder_named:
        return _out("", "", "no published email found, and no founder name on record to guess from",
                    "", "exhausted")
    if verifier_broken:
        # Never report this as "the guess failed": we never got to check it.
        return _out("", "", f"no published email found, and the guess could NOT be tested ({guess_status})",
                    "", "exhausted: verifier not working", guess=guess, guess_status=guess_status)
    return _out("", "", "no published email found; no pattern guess passed",
                "", "exhausted", guess=guess, guess_status=guess_status)


def choose_best_email(site: Dict, ai_email: str, ai_source: str) -> tuple:
    """
    Pick between the site-scraped email and the AI-searched one.
    First-party personal beats everything; a personal address from search
    beats a generic site address; first-party generic beats nothing at all.
    Returns (email, source_description).
    """
    site_email = (site or {}).get("email", "")
    site_src = f"company website ({(site or {}).get('source', '')})"
    ai_email = (ai_email or "").strip()
    if site_email and not ai_email:
        return site_email, site_src
    if not site_email:
        return ai_email, ai_source
    if not is_generic_address(site_email):
        return site_email, site_src
    if not is_generic_address(ai_email):
        return ai_email, ai_source
    return site_email, site_src
