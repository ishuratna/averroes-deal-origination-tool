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
    Returns {"email": best_pick or "", "source": page_url, "all": [...]} —
    personal addresses at the company domain beat generic ones; the contact's
    first/last name (when known) beats other personal addresses.
    """
    domain = _clean_domain(website)
    if not domain:
        return {"email": "", "source": "", "all": []}

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
        return {"email": "", "source": "", "all": []}

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
    return {"email": best, "source": found[best], "all": sorted(found.keys())}


# ── Step 3: mailbox verification (Hunter.io email-verifier) ─────────────────
# GCP blocks outbound SMTP, so server-level mailbox checks go through a
# verifier API. Configure with HUNTER_API_KEY (hunter.io, free tier available).
# Without a key, verification reports "unavailable" and the waterfall falls
# back to found-in-source behaviour (patterns are NEVER stored unverified).

import os as _os


def verify_email(email: str) -> str:
    """Returns: deliverable | undeliverable | catch_all | unknown | unavailable"""
    api_key = _os.getenv("HUNTER_API_KEY", "") or _os.getenv("EMAIL_VERIFIER_API_KEY", "")
    if not api_key or not email:
        return "unavailable"
    try:
        resp = requests.get("https://api.hunter.io/v2/email-verifier",
                            params={"email": email, "api_key": api_key}, timeout=15)
        data = (resp.json() or {}).get("data", {})
        status, result = data.get("status", ""), data.get("result", "")
        if status == "accept_all":
            return "catch_all"
        if result == "deliverable" or status == "valid":
            return "deliverable"
        if result == "undeliverable" or status == "invalid":
            return "undeliverable"
        return "unknown"
    except Exception as e:
        logger.warning(f"[ContactFinder] verifier call failed for {email}: {e}")
        return "unknown"


def _pattern_candidates(contact_name: str, domain: str, observed: List[str]) -> List[str]:
    """
    Step 4: infer candidates from the contact's name. If other addresses at
    the domain are observed, mirror their pattern first; then common formats.
    These are GUESSES — the caller must verify before storing any of them.
    """
    bits = [w for w in re.sub(r"[^a-z ]", "", (contact_name or "").lower()).split() if w]
    if len(bits) < 1 or not domain:
        return []
    first, last = bits[0], (bits[-1] if len(bits) > 1 else "")
    cands: List[str] = []

    def add(local):
        if local:
            e = f"{local}@{domain}"
            if e not in cands:
                cands.append(e)

    # Mirror the observed pattern at this domain (from a colleague's address)
    for obs in observed or []:
        local, _, dom = obs.partition("@")
        if dom != domain or is_generic_address(obs):
            continue
        if "." in local and last:
            add(f"{first}.{last}")
        elif last and local.startswith(local[:1]) and len(local) > 1 and not local.isalpha() is False:
            pass  # ambiguous; common formats below cover it
    if last:
        add(f"{first}.{last}")
        add(first)
        add(f"{first[0]}{last}")
        add(f"{first}{last}")
        add(f"{first[0]}.{last}")
    else:
        add(first)
    return cands[:4]


def resolve_contact_email(website: str, contact_name: str, ai_email: str, ai_source: str,
                          retry_fn=None) -> Dict:
    """
    The contact waterfall, in strict priority order:
      1. personal email found on the WEB (the AI first-pass result)
      2. the company's OWN website (personal, then generic)
      3. retry ladder — one sharper grounded search (only if 1-2 yield nothing usable)
      4. name-pattern inference (only ever stored if verified)
    VERIFICATION POLICY: found-in-source candidates (steps 1-3) are accepted
    as-is — they exist in evidence, and verifier credits are saved for where
    they add real information. Mailbox verification runs ONLY at step 4:
    a pattern guess must PROVE a mailbox exists (strict deliverable) or it is
    discarded. Without a verifier key, step 4 is skipped entirely.
    retry_fn: zero-arg callable returning {"contact_email","email_source"}.
    Returns {"email", "source", "verification"}.
    """
    site = find_site_emails(website, contact_name)
    domain = _clean_domain(website) or (ai_email.split("@")[-1] if "@" in (ai_email or "") else "")
    ai_email = (ai_email or "").strip().lower()

    verifier_on = bool(_os.getenv("HUNTER_API_KEY", "") or _os.getenv("EMAIL_VERIFIER_API_KEY", ""))

    def _found(email: str, source: str):
        return {"email": email, "source": source, "verification": "found in source"}

    # Step 1: personal email found on the web (AI first pass)
    if ai_email and not is_generic_address(ai_email):
        return _found(ai_email, ai_source or "AI web search")

    # Step 2: the company's own website — personal first, then one generic
    if site.get("email") and not is_generic_address(site["email"]):
        return _found(site["email"], f"company website ({site['source']})")
    site_generic = next((e for e in site.get("all", []) if is_generic_address(e)), "")
    ai_generic = ai_email if (ai_email and is_generic_address(ai_email)) else ""

    # Step 3: retry ladder — one sharper grounded search (before settling for a generic)
    retry_email = ""
    if retry_fn:
        try:
            retry = retry_fn() or {}
            retry_email = (retry.get("contact_email") or "").strip().lower()
            if retry_email and not is_generic_address(retry_email):
                return _found(retry_email, retry.get("email_source") or "retry web search")
        except Exception as e:
            logger.warning(f"[ContactFinder] retry ladder failed: {e}")

    # Step 4: pattern inference — the ONLY step where verification runs.
    # A guess exists in no source, so it must prove a mailbox exists.
    if verifier_on:
        observed = site.get("all", []) + [e for e in (ai_email, retry_email) if e]
        checked = 0
        for p in _pattern_candidates(contact_name, domain, observed):
            if p in observed or checked >= 4:
                continue
            v = verify_email(p)
            checked += 1
            logger.info(f"[ContactFinder] verify pattern {p} -> {v}")
            if v == "deliverable":
                return {"email": p, "source": "inferred from name pattern",
                        "verification": "mailbox verified (deliverable)"}

    # Fall back to a generic found-in-source address rather than nothing
    if ai_generic or site_generic:
        email = ai_generic or site_generic
        src = (ai_source or "AI web search") if ai_generic else f"company website ({site.get('source') or 'site'})"
        return _found(email, src)

    if not verifier_on:
        return {"email": "", "source": "", "verification": "no published email found (verifier not configured, patterns skipped)"}
    return {"email": "", "source": "", "verification": "no published email found; no pattern guess verified"}


def is_generic_address(email: str) -> bool:
    local = (email or "").split("@")[0].lower()
    return any(local == g or local.startswith(g) for g in _GENERIC_PREFIXES)


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
