"""
Identity guard: is the company the AI researched THE SAME COMPANY as the row?

The failure this prevents (per Ishu, 27 Aug 2026): two companies share a name,
the grounded search finds the wrong one, and its founder/city/description are
written over the right company's seed data. The fix is never "search harder";
it is VERIFICATION in code:

  1. The enrichment prompt receives the seed anchors as CONSTRAINTS and must
     echo back the identity of the company it actually researched.
  2. check_identity() - pure, no AI - compares that echo against the seed
     anchors. TWO-ANCHOR RULE: at least 2 points of independent agreement
     (domain and CH number are near-unique so they score 2; founder surname,
     city and founding year score 1) and agreement must outweigh conflict.
  3. A mismatch strips every researched field before anything is written.

audit_row() is the ZERO-AI retro pass over rows enriched before this guard
existed: it looks for internal contradictions the mixup would have left
behind, so only genuine suspects ever cost a re-research call.
"""
import re
from typing import Dict, List, Optional

# Free mail providers prove nothing about company identity.
GENERIC_MAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
    "yahoo.co.uk", "icloud.com", "aol.com", "protonmail.com", "proton.me",
    "live.com", "live.co.uk", "me.com", "msn.com", "mail.com",
}

_WWW = re.compile(r"^www\d?\.")

# Domains that can never identify a company: AI-fabricated placeholders from
# old enrichments (a real bug this audit surfaced) and multi-tenant/service
# domains that hundreds of unrelated businesses share.
PLACEHOLDER_MAIL = {"example.com", "company.com", "email.com", "acme.com",
                    "yourbusiness.com", "studio.com", "domain.com", "test.com"}
SHARED_SERVICE_MAIL = {"wixpress.com", "onmicrosoft.com", "sentry.io",
                       "hostingersite.com", "uk.com", "gsi.gov.uk", "nhs.net",
                       "wordpress.com", "squarespace.com", "godaddy.com"}


def registrable_domain(value: str) -> str:
    """example.co.uk from a URL or an email address. '' when unparseable."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "@" in v:
        v = v.rsplit("@", 1)[-1]
    else:
        v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split(":")[0]
    v = _WWW.sub("", v).strip(".")
    if "." not in v:
        return ""
    parts = v.split(".")
    # Keep 3 labels for the common two-part UK/IE suffixes, else 2.
    if len(parts) >= 3 and parts[-2] in ("co", "org", "ac", "gov", "net", "com") \
            and parts[-1] in ("uk", "ie"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def surname(name: str) -> str:
    parts = re.sub(r"[^\w\s'-]", " ", name or "").split()
    return parts[-1].lower() if len(parts) >= 2 else (parts[0].lower() if parts else "")


def norm_city(s: str) -> str:
    s = (s or "").strip().lower()
    for junk in ("greater ", "city of ", "borough of "):
        if s.startswith(junk):
            s = s[len(junk):]
    return s.split(",")[0].strip()


def _norm_ch(n: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (n or "").upper()).lstrip("0")


def seed_anchors(company: Dict) -> Dict[str, str]:
    """What we KNOW about which company we mean, from the row as it stands
    BEFORE enrichment (ingest sources fill these: Inven/Gain/PitchBook CSVs,
    conference lists, CH verification)."""
    return {
        "domain": registrable_domain(company.get("website") or ""),
        "hq_city": norm_city(company.get("hq_city") or ""),
        "founder": surname(company.get("contact_name") or ""),
        "year_founded": str(company.get("year_founded") or "").strip(),
        "ch_number": _norm_ch(company.get("ch_company_number") or ""),
    }


# anchor -> points. Domain and CH number are near-unique identifiers; the
# human-level anchors corroborate but never confirm alone.
_WEIGHTS = {"domain": 2, "ch_number": 2, "founder": 1, "hq_city": 1, "year_founded": 1}


def check_identity(seed: Dict, echo: Dict) -> Dict:
    """The two-anchor rule. Pure, testable, no AI.

    Returns {"verdict": confirmed|unverified|mismatch, "matched": [...],
             "conflicts": [...], "note": str}.

      confirmed   >=2 points agree AND agreement outweighs conflict
      mismatch    conflicts >= agreement (and at least one conflict)
      unverified  not enough overlapping data to say either way - the write
                  is allowed but the row is flagged, never silently trusted
    """
    echo = echo or {}
    norm_echo = {
        "domain": registrable_domain(echo.get("website") or ""),
        "hq_city": norm_city(echo.get("hq_city") or ""),
        "founder": surname(echo.get("founder_name") or ""),
        "year_founded": str(echo.get("year_founded") or "").strip(),
        "ch_number": _norm_ch(echo.get("company_number") or ""),
    }
    matched, conflicts = [], []
    m_pts = c_pts = 0
    for key, w in _WEIGHTS.items():
        s, e = seed.get(key) or "", norm_echo.get(key) or ""
        if not s or not e:
            continue                      # nothing to compare on this anchor
        if key == "year_founded":
            try:
                ok = abs(int(s) - int(e)) <= 1   # sources disagree by a year all the time
            except ValueError:
                continue
        else:
            ok = s == e
        if ok:
            matched.append(key); m_pts += w
        else:
            conflicts.append(f"{key}: ours '{s}' vs found '{e}'"); c_pts += w

    if conflicts and c_pts >= m_pts:
        verdict = "mismatch"
    elif m_pts >= 2:
        verdict = "confirmed"
    else:
        verdict = "unverified"
    note = ""
    if verdict == "confirmed":
        note = "matched on " + " + ".join(matched)
        if conflicts:
            note += "; stale anchor ignored (" + "; ".join(conflicts) + ")"
    elif verdict == "mismatch":
        note = "; ".join(conflicts)
    else:
        note = "not enough overlapping anchors to verify" + \
               (f" (matched only {' + '.join(matched)})" if matched else "")
    return {"verdict": verdict, "matched": matched, "conflicts": conflicts, "note": note}


# ── The ZERO-AI retro audit ──────────────────────────────────────────────────

def _squash(name: str) -> str:
    """Everything but letters+digits, lowercased: 'VU.CITY' == 'VUCITY LIMITED'
    once suffixes are gone. Kills the false positives where the CH name is the
    same brand merely spaced or punctuated differently (SailTies vs SAIL TIES).
    """
    stop = ("limited", "ltd", "plc", "llp", "the ", "group", "holdings")
    s = (name or "").lower()
    for w in stop:
        s = s.replace(w, " ")
    return re.sub(r"[^a-z0-9]", "", s)


def _core_tokens(name: str) -> set:
    stop = {"ltd", "limited", "plc", "llp", "uk", "the", "group", "holdings",
            "technologies", "technology", "tech", "software", "solutions",
            "systems", "labs", "digital", "and", "co"}
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in stop and len(t) > 2}


def audit_row(company: Dict, domain_owners: Optional[Dict[str, str]] = None) -> Dict:
    """Contradictions a past mixup would have left in the row. Pure, free.

    domain_owners: registrable domain -> company name for the whole universe,
    so a contact email pointing at ANOTHER company's domain - the smoking gun
    of cross-contamination - is caught.
    """
    signals: List[str] = []
    name = company.get("name") or ""
    site_dom = registrable_domain(company.get("website") or "")
    mail_dom = registrable_domain(company.get("contact_email") or "")

    if mail_dom in PLACEHOLDER_MAIL:
        signals.append(f"contact email is a FABRICATED placeholder ('{mail_dom}') - clear it and re-run the waterfall")
    elif mail_dom and site_dom and mail_dom != site_dom \
            and mail_dom not in GENERIC_MAIL and mail_dom not in SHARED_SERVICE_MAIL:
        owner = (domain_owners or {}).get(mail_dom, "")
        if owner and owner != name:
            signals.append(f"contact email domain '{mail_dom}' belongs to ANOTHER company in the universe: {owner}")
        else:
            signals.append(f"contact email domain '{mail_dom}' does not match the website '{site_dom}'")

    ch_name = company.get("ch_official_name") or ""
    if ch_name and name:
        ours, theirs = _core_tokens(name), _core_tokens(ch_name)
        a, b = _squash(name), _squash(ch_name)
        squash_ok = bool(a) and bool(b) and (a in b or b in a)
        if ours and theirs and not (ours & theirs) and not squash_ok:
            signals.append(f"CH match '{ch_name}' shares no core word with '{name}'")

    return {"suspect": bool(signals), "signals": signals}
