"""
Investor miner v2: extracts the investors of universe companies from every
stored source and files them into the LP database plus the investor_links
connection layer.

Sources per company (most verifiable first):
  1. CH cap table (CS01)      -> equity_holder edges, with % stakes
  2. PitchBook active/former  -> pitchbook_active / pitchbook_former edges
  3. Inven "Investors"        -> inven_investor edges
  4. Inven "Current owners"   -> inven_owner edges

Cleaning is rules-first (zero AI); only ambiguous names go to ONE batched
ungrounded Gemini call per run for canonical naming + typing. Nominees,
crowdfunding vehicles and founder holdcos are excluded from the LP table
but still recorded as edges (the connection layer keeps the full truth).
No em dashes anywhere in this file.
"""
import os
import re
import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_JUNK = {".na", "n/a", "na", "-", "none", "unknown", "undisclosed", "private", "self-funded", "bootstrapped"}

_AGENCY_PAT = re.compile(
    r"enterprise ireland|british business|scottish enterprise|invest northern ireland|innovate uk|"
    r"development bank of wales|northern powerhouse|midlands engine|future fund|regional growth|"
    r"local authority|council pension|european investment", re.I)
_BANK_PAT = re.compile(r"\b(santander|hsbc|barclays|natwest|lloyds|rbs|citi|jpmorgan|goldman|silicon valley bank|svb|clydesdale|handelsbanken|oaknorth|boost ?& ?co|boostandco)\b|bank\b", re.I)
_CROWD_PAT = re.compile(r"seedrs|crowdcube|kickstarter|indiegogo|republic\b|crowd", re.I)
_NOMINEE_PAT = re.compile(r"nominee|trustees?\b|emi trust|employee benefit|share scheme|vestd", re.I)
_FUND_PAT = re.compile(
    r"\b(capital|ventures?|partners|equity|invest(?:ments?|ors)?|fund|vct|seed|angels?|growth|holdings?|"
    r"asset management|private equity|family office|sosv|accel|index|sequoia|balderton|octopus|mercia|bgf|ldc)\b", re.I)
_CORP_SUFFIX = re.compile(r"\b(ltd|limited|llp|lp|plc|gmbh|sarl|bv|inc|corp|sa|ag|ehf|slhf|as|oy|ab)\.?\b", re.I)


def _canonical_key(name: str) -> str:
    s = (name or "").strip().lower()
    s = _CORP_SUFFIX.sub("", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _domain_to_name(token: str) -> Tuple[str, str]:
    """'rockpoolinvestments.co.uk' -> ('Rockpoolinvestments', 'https://rockpoolinvestments.co.uk')"""
    dom = token.strip().lower().rstrip("/").replace("https://", "").replace("http://", "").replace("www.", "")
    sld = dom.split(".")[0]
    return sld.capitalize(), f"https://{dom}"


def _looks_like_domain(token: str) -> bool:
    return bool(re.match(r"^(https?://)?(www\.)?[a-z0-9-]+\.[a-z.]{2,6}/?$", token.strip().lower()))


def _looks_like_person(name: str) -> bool:
    """Two or three capitalised words, no corporate/fund vocabulary."""
    if _CORP_SUFFIX.search(name) or _FUND_PAT.search(name):
        return False
    parts = name.strip().split()
    return 2 <= len(parts) <= 3 and all(p[:1].isalpha() for p in parts)


def _rule_classify(name: str) -> Optional[str]:
    if _NOMINEE_PAT.search(name):
        return "Nominee"
    if _CROWD_PAT.search(name):
        return "Crowdfunding"
    if _AGENCY_PAT.search(name):
        return "Agency"
    if _BANK_PAT.search(name):
        return "Bank"
    if re.search(r"family office", name, re.I):
        return "Family Office"
    if _FUND_PAT.search(name):
        return "Fund"
    if _looks_like_person(name):
        return "Angel"
    return None  # -> AI batch


def _founder_vehicle(name: str, company: dict) -> bool:
    """Holdco named after the founder (e.g. 'J Dean Holdings')."""
    contact = (company.get("contact_name") or "")
    surnames = [w.lower() for w in contact.split() if len(w) > 2][1:]
    if not surnames:
        return False
    low = name.lower()
    return any(s in low for s in surnames) and bool(re.search(r"holding|invest|capital|ltd|limited", low))


def _split_tokens(raw: str) -> List[str]:
    out = []
    for tok in re.split(r"[;,]", raw or ""):
        tok = tok.strip().strip(".")
        if not tok or tok.lower() in _JUNK or len(tok) < 3:
            continue
        out.append(tok)
    return out


def extract_candidates(company: dict) -> List[Dict]:
    """All investor candidates for one company, with link_type + evidence."""
    cands, seen = [], set()
    cname_key = _canonical_key(company.get("name") or "")

    def add(name: str, link_type: str, pct=None, detail: str = "", website: str = ""):
        name = re.sub(r"\s+", " ", (name or "").strip())
        if not name or name.lower() in _JUNK:
            return
        if _looks_like_domain(name):
            name, website = _domain_to_name(name)
        key = _canonical_key(name)
        if not key or key == cname_key:  # never the company itself
            return
        if (key, link_type) in seen:
            return
        seen.add((key, link_type))
        itype = "FounderVehicle" if _founder_vehicle(name, company) else _rule_classify(name)
        cands.append({"investor_key": key, "investor_name": name, "investor_type": itype,
                      "link_type": link_type, "pct": pct, "detail": detail[:300], "website": website})

    # 1. CH cap table: the verified source, with stakes
    raw_ct = company.get("ch_cap_table")
    if raw_ct:
        try:
            parsed = json.loads(raw_ct) if isinstance(raw_ct, str) else raw_ct
            for h in (parsed.get("holders") or parsed.get("shareholders") or []):
                if not isinstance(h, dict):
                    continue
                nm = h.get("name") or ""
                if not nm or nm.strip().lower() in ("others", "other"):
                    continue
                pct = h.get("pct") or h.get("percent") or h.get("percentage")
                try:
                    pct = float(pct) if pct is not None else None
                except (TypeError, ValueError):
                    pct = None
                add(nm, "equity_holder", pct=pct,
                    detail=f"CS01 cap table {str(company.get('ch_cap_table_date') or '')[:10]}")
        except Exception:
            pass

    # 2. PitchBook investor lists
    for field, lt in (("active_investors", "pitchbook_active"), ("former_investors", "pitchbook_former")):
        for tok in _split_tokens(company.get(field) or ""):
            add(tok, lt, detail="PitchBook")

    # 3./4. Inven columns
    for field, lt in (("investors_raw", "inven_investor"), ("current_owners", "inven_owner")):
        for tok in _split_tokens(company.get(field) or ""):
            add(tok, lt, detail="Inven export")

    return cands


def ai_classify_batch(names: List[str]) -> Dict[str, Dict]:
    """One ungrounded call per ~60 unknowns: canonical name + type.
    Returns {raw_name: {"name": canonical, "type": t}}. Empty dict on failure."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not names:
        return {}
    out = {}
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client(api_key=api_key)
        for i in range(0, len(names), 60):
            chunk = names[i:i + 60]
            prompt = ("These strings were extracted as investors/shareholders of UK and Ireland software companies. "
                      "For each, return the canonical organisation or person name (fix casing, expand obvious domain names) "
                      "and classify as one of: Fund, PE, VC, Family Office, Angel, Corporate, Agency, Bank, Crowdfunding, Nominee, Unknown. "
                      "Do not invent information; if unsure use Unknown. "
                      'Return ONLY a JSON array: [{"raw": "...", "name": "...", "type": "..."}].\n\n'
                      + "\n".join(f"- {n}" for n in chunk))
            resp = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=GenerateContentConfig(temperature=0.1, response_mime_type="application/json"))
            text = (resp.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            start, end = text.find("["), text.rfind("]")
            for row in json.loads(text[start:end + 1]):
                if isinstance(row, dict) and row.get("raw"):
                    out[row["raw"]] = {"name": row.get("name") or row["raw"],
                                       "type": row.get("type") or "Unknown"}
    except Exception as e:
        logger.warning(f"[InvestorMiner] AI classification failed (rules-only fallback): {e}")
    return out


# Types that stay OUT of the LP table (edges keep them for the graph)
_EXCLUDE_FROM_LP = {"Nominee", "FounderVehicle"}


def mine_companies(companies: List[dict]) -> Dict:
    """Extract, classify, and shape results for persistence. Pure function:
    returns {"per_company": {name: [links]}, "investors": {key: profile}}."""
    per_company: Dict[str, List[Dict]] = {}
    profiles: Dict[str, Dict] = {}
    unknown_names: Dict[str, None] = {}

    for c in companies:
        cands = extract_candidates(c)
        if not cands:
            continue
        per_company[c["name"]] = cands
        for cand in cands:
            if cand["investor_type"] is None:
                unknown_names[cand["investor_name"]] = None

    ai_map = ai_classify_batch(list(unknown_names.keys()))

    for cname, cands in per_company.items():
        for cand in cands:
            if cand["investor_type"] is None:
                fix = ai_map.get(cand["investor_name"])
                if fix:
                    cand["investor_name"] = fix["name"]
                    cand["investor_key"] = _canonical_key(fix["name"]) or cand["investor_key"]
                    cand["investor_type"] = fix["type"]
                else:
                    cand["investor_type"] = "Unknown"
            if cand["investor_type"] in _EXCLUDE_FROM_LP:
                continue
            p = profiles.setdefault(cand["investor_key"], {
                "name": cand["investor_name"], "investor_type": cand["investor_type"],
                "website": cand.get("website") or "", "companies": set(),
                "source": "Portfolio mining v2",
            })
            p["companies"].add(cname)
            if not p["website"] and cand.get("website"):
                p["website"] = cand["website"]

    return {"per_company": per_company, "investors": profiles}
