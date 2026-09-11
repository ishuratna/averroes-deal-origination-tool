"""
InvestorFill: AI research on one investor. Mirrors SmartFill for companies.

ONE Gemini 2.5 Flash call with Google Search grounding. Its job is NOT to score
anything: `ai/investor_gate.py` decides eligibility and `ai/lp_priority.py`
decides rank, both pure and both free. This module exists ONLY to find the facts
those two need, plus the decision maker to write to.

WHAT IT MUST RETURN, AND WHY (three defects fixed 11 Sep 2026):

  * EVERY FIGURE IN USD MILLIONS. The prompt used to ask for GBP millions while
    the gate compares against USD thresholds. A GBP 900M family office read as
    900 against a 1000 ceiling and PASSED, when USD 1.17bn should have failed
    it; a stated GBP 250K ticket read as 0.25 against a 0.26 floor and was
    REFUSED as too small. A unit mismatch does not error, it just quietly
    qualifies the wrong people, which is the worst kind of bug.
  * THE FIELDS THE RANKING ACTUALLY WEIGHTS. `lp_priority` puts 0.30, its
    heaviest weight, on co-investment appetite, which it reads from
    `strategy_preferences`, `other_preferences` and `policy_description`. This
    module returned NONE of them, so every researched investor scored the
    default "no co-investment evidence yet" on the dimension that matters most.
  * `geo_preferences`, WITHOUT WHICH THE MANDATE ROUTE IS DEAD. The gate
    qualifies an investor whose mandate covers the UK, Ireland or Europe
    wherever they sit. That field was never returned, so research could never
    discover a mandate: only a PitchBook row that happened to carry it.

The four 0-1 scores remain, feeding the older informational `lp_fit_score`.
They are NOT the ranking and are never used to gate anything.
"""
import os
import re
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _response_text(response) -> str:
    """Collect text from a Gemini response, tolerating empty .text with populated parts."""
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    try:
        parts = []
        for cand in (response.candidates or []):
            for part in (cand.content.parts or []):
                if getattr(part, "text", None):
                    parts.append(part.text)
        return "\n".join(parts).strip()
    except Exception:
        return ""


def _extract_json(text: str) -> dict:
    """
    Parse JSON from an LLM response that may include markdown fences or
    surrounding prose (common with Search-grounded responses).
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back: first '{' to last '}' — the JSON body inside surrounding prose
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise json.JSONDecodeError("No JSON object found in response", text[:80], 0)


def _core_words(text: str) -> set:
    """Distinctive words in an investor name, ignoring the furniture."""
    generic = {"the", "and", "of", "for", "group", "holding", "holdings", "family", "office",
               "offices", "investment", "investments", "investor", "investors", "capital",
               "partners", "partner", "ltd", "limited", "llc", "llp", "inc", "plc", "sa", "ag",
               "bv", "gmbh", "company", "co", "management", "advisors", "advisers", "trust",
               "fund", "funds", "ventures", "equity", "private", "wealth", "asset"}
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in generic and len(w) > 2}


def _identity_ok(asked: str, found: str) -> tuple:
    """Did the research come back about the investor we ASKED for?

    Doctrine 4a exists on the company side and was missing here. "Al Rasheed
    Investment" and "Al Rasheed Family Office" are different entities, and the
    cost of confusing them is another investor's assets and another person's
    email landing on this row.

    Deliberately light: we require ONE shared distinctive word, because
    investor names are short and full of furniture ("Capital", "Family
    Office", "Partners"). Returns (verdict, note).
    """
    if not (found or "").strip():
        return "unverified", "Research did not echo back a name."
    a, b = _core_words(asked), _core_words(found)
    if not a or not b:
        return "unverified", f"Nothing distinctive to compare ('{asked}' vs '{found}')."
    if a & b:
        return "confirmed", f"'{found}' shares {sorted(a & b)} with '{asked}'."
    return "mismatch", (f"Research returned '{found}', which shares no distinctive word with "
                        f"'{asked}'. Refused rather than written.")


def _found_email_only(result: Dict) -> str:
    """The contact email ONLY when the research actually found it published.

    Anything the model marked inferred, or shaped like a construction from the
    person's name, is dropped. The NAME and TITLE are kept: knowing that the
    CIO is Faisal Al Rasheed is most of the value, and a wrong address is worse
    than none because it bounces and burns the approach.
    """
    email = (result.get("contact_email") or "").strip()
    if not email or "@" not in email:
        return ""
    conf = (result.get("contact_confidence") or "").strip().lower()
    if conf and conf != "found":
        logger.info(f"[InvestorFill] Dropping {conf} email {email!r}: we do not guess addresses.")
        return ""
    if not conf:
        # No confidence stated is not a licence to trust it.
        logger.info(f"[InvestorFill] Dropping email {email!r}: research did not confirm it was published.")
        return ""
    local = email.split("@", 1)[0].lower()
    if local in ("info", "contact", "hello", "enquiries", "enquiry", "general", "admin", "office", "mail"):
        logger.info(f"[InvestorFill] Dropping general inbox {email!r}: not a decision maker.")
        return ""
    return email


def investor_fill(name: str, context: Dict = None, target_brief: str = "") -> Dict:
    """
    Enrich + score one investor. Returns dict of fields for
    InvestorBQHandler.update_enrichment, plus 'error' key on failure.

    target_brief: who the DECISION MAKER is for this kind of investor, from
    ai.investor_gate.target_brief. A family office is decided by its CIO, a
    wealthy individual decides for themselves, a multi-family office by its
    head of private markets. Getting this wrong wastes the one email we get,
    so the ladder is passed in rather than left to the model's instincts.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}

    ctx = context or {}
    portfolio = ctx.get("source_companies", "")

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)

        prompt = f"""You are an investor-relations analyst at Averroes Capital, a London technology
investor that buys and backs software companies in the UK and Ireland.

We do NOT raise a blind fund. We invest DEAL BY DEAL, and on each deal a small group of investors
comes in beside us at GBP 200,000 to GBP 10 million each. So the question about any investor is
always: would they write a single direct cheque of that size into one private company, and can we
reach the person who decides?

INVESTOR TO RESEARCH: "{name}"
{f'Known portfolio overlap (companies in our pipeline they have backed): {portfolio}' if portfolio else ''}

Search the web thoroughly and return what you can EVIDENCE. Never guess.

1. IDENTITY, so we can check you researched the right entity. Many investors share a name.
   Echo back the full legal or trading name exactly as the source gives it, the country, and the
   website you used.

2. TYPE, exactly one of: "Single Family Office", "Multi-Family Office", "Family Office", "HNWI",
   "UHNWI", "Angel", "Angel Syndicate", "Investment Company", "Holding Company", "VC", "PE",
   "Fund of Funds", "Pension", "Insurance", "Sovereign/Institutional", "Corporate", "Unknown"

3. SIZE, AND EVERY MONEY FIGURE IN **USD MILLIONS**. This matters: convert from any other
   currency and state what you converted from. GBP 900 million becomes 1170, not 900. A typical
   cheque of GBP 250,000 becomes 0.33, not 0.25.
     aum_usd_m            assets under management or total assets
     ticket_min_usd_m     smallest single investment they typically make
     ticket_max_usd_m     largest

4. CO-INVESTMENT APPETITE, which is the single most important thing you can find.
   Do they take DIRECT positions in private companies, or co-invest alongside managers, or only
   commit to funds? Quote their own words where you can:
     strategy_preferences   their stated strategy: direct, co-investment, funds, growth, buyout
     other_preferences      anything else about how they deploy, in their words
     policy_description     their stated investment policy or mandate, if published

5. GEOGRAPHIC MANDATE, separate from where they are based.
     geo_preferences        the regions and countries they INVEST IN.
   An investor in Singapore whose mandate covers the UK is interesting to us; one whose mandate
   is Asia only is not. Getting this wrong loses real prospects, so look for it specifically.

6. TRACK RECORD AND RECENCY
     num_pe_commitments     count of private equity fund commitments, if reported
     num_vc_commitments     count of venture fund commitments, if reported
     last_commitment_date   the date of their most recent known investment, YYYY-MM-DD or YYYY

7. THE DECISION MAKER, the single person who could say yes to a co-investment, with their exact
   job title.
   {target_brief or "Prefer the Chief Investment Officer, head of investments, managing partner or principal."}
   DO NOT GUESS AN EMAIL ADDRESS. Return contact_email ONLY if you actually found that exact
   address published somewhere. Never construct one from a name and a domain, never offer
   firstname.lastname@, and never substitute a general enquiries address. If you cannot find a
   published address, return null and set contact_confidence to "not_found" while still returning
   the person's NAME and TITLE, which are the valuable part.

8. FOUR 0.0-1.0 SCORES, informational only, each with one sentence of evidence:
   a) geography: UK/Ireland=1.0, GCC=0.9, Western Europe=0.8, rest of Europe=0.6, elsewhere but
      with a UK or European mandate=0.6, elsewhere=0.2
   b) pe_appetite: proven DIRECT private investments or co-investments=0.9-1.0, fund commitments
      only=0.4-0.6, public markets only=0.1-0.2
   c) ticket_fit: typically writes within GBP 200K to 10M=0.9-1.0, adjacent=0.5, far outside=0.15
   d) tech_affinity: real software or technology holdings=0.8-1.0, some exposure=0.5-0.7, none
      evident=0.2

RULES
- Anything you cannot evidence is null. A null is a good answer; an invented number is not.
- If you cannot confidently identify this investor at all (name too generic, no online presence),
  set identified to false and return nothing else.

Return ONLY valid JSON:
{{
  "identified": true or false,
  "name_as_found": "string or null",
  "investor_type": "one of the types above",
  "hq_city": "string or null",
  "hq_country": "string or null",
  "region": "UK" | "Europe" | "GCC" | "US" | "Other",
  "website": "string or null",
  "aum_usd_m": number or null,
  "aum_converted_from": "e.g. GBP 900m, or null if already USD",
  "ticket_min_usd_m": number or null,
  "ticket_max_usd_m": number or null,
  "strategy_preferences": "string or null",
  "other_preferences": "string or null",
  "policy_description": "string or null",
  "geo_preferences": "string or null",
  "num_pe_commitments": number or null,
  "num_vc_commitments": number or null,
  "last_commitment_date": "YYYY-MM-DD or YYYY or null",
  "description": "1-2 sentences",
  "contact_name": "string or null",
  "contact_title": "their exact job title, or null",
  "contact_email": "string or null",
  "contact_confidence": "found" | "not_found",
  "linkedin_url": "string or null",
  "scores": {{
    "geography": {{"score": 0.0-1.0 or null, "explanation": "one sentence"}},
    "pe_appetite": {{"score": 0.0-1.0 or null, "explanation": "one sentence"}},
    "ticket_fit": {{"score": 0.0-1.0 or null, "explanation": "one sentence"}},
    "tech_affinity": {{"score": 0.0-1.0 or null, "explanation": "one sentence"}}
  }}
}}"""

        logger.info(f"[InvestorFill] Researching '{name}'...")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())]),
        )

        text = _response_text(response)
        if not text:
            return {"error": "AI returned an empty response — retry in a moment"}
        result = _extract_json(text)

        if not result.get("identified"):
            return {"error": f"Could not confidently identify investor '{name}' via web search"}

        # Extract + validate scores
        raw_scores = result.get("scores", {})
        scores = {}
        details = {}
        for key in ["geography", "pe_appetite", "ticket_fit", "tech_affinity"]:
            metric = raw_scores.get(key) or {}
            s = metric.get("score")
            if s is not None:
                try:
                    s = max(0.0, min(1.0, float(s)))
                    scores[key] = round(s, 3)
                    details[key] = {"score": scores[key], "explanation": metric.get("explanation", "")}
                except (ValueError, TypeError):
                    pass

        # Composite: need at least 3 of 4 assessable
        lp_fit = None
        if len(scores) >= 3:
            lp_fit = round(sum(scores.values()) / len(scores), 3)
        logger.info(f"[InvestorFill] '{name}': fit={lp_fit} ({len(scores)}/4 criteria)")

        def _f(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        # Identity FIRST: a mismatch is refused outright, not corrected.
        verdict, note = _identity_ok(name, result.get("name_as_found") or "")
        if verdict == "mismatch":
            logger.warning(f"[InvestorFill] {note}")
            return {"error": note, "identity_status": "mismatch", "identity_note": note}

        def _i(v):
            try:
                return int(float(v)) if v is not None else None
            except (ValueError, TypeError):
                return None

        conv = (result.get("aum_converted_from") or "").strip()
        if conv:
            logger.info(f"[InvestorFill] '{name}': AUM converted from {conv} to USD.")

        return {
            "investor_type": result.get("investor_type") or "Unknown",
            # USD millions throughout. The column names keep the historical
            # "_m" suffix; the UNIT is USD, matching the gate and lp_priority.
            "aum_m": _f(result.get("aum_usd_m")),
            "ticket_min_m": _f(result.get("ticket_min_usd_m")),
            "ticket_max_m": _f(result.get("ticket_max_usd_m")),
            "region": result.get("region") or "",
            "hq_city": result.get("hq_city") or "",
            "hq_country": result.get("hq_country") or "",
            "website": result.get("website") or "",
            "description": result.get("description") or "",
            # The heaviest dimension in lp_priority (co-invest appetite, 0.30)
            # reads these three. Returning them is the whole point of the pass.
            "strategy_preferences": result.get("strategy_preferences") or "",
            "other_preferences": result.get("other_preferences") or "",
            "policy_description": result.get("policy_description") or "",
            # Without this the gate's mandate route can never fire from research.
            "geo_preferences": result.get("geo_preferences") or "",
            "num_pe_commitments": _i(result.get("num_pe_commitments")),
            "num_vc_commitments": _i(result.get("num_vc_commitments")),
            "last_commitment_date": (result.get("last_commitment_date") or "")[:10],
            "contact_name": result.get("contact_name") or "",
            "contact_title": result.get("contact_title") or "",
            # Ishu, 11 Sep 2026: "lets not infer email addresses, we will
            # either find them or keep them out". Enforced HERE and not only in
            # the prompt, because an instruction is a request and this is a
            # rule: a guessed address bounces, and a bounce burns the one
            # approach we get with that investor.
            "contact_email": _found_email_only(result),
            "contact_confidence": "found" if _found_email_only(result) else "not_found",
            "linkedin_url": result.get("linkedin_url") or "",
            "identity_status": verdict,
            "identity_note": note,
            "lp_fit_score": lp_fit,
            "score_geography": scores.get("geography"),
            "score_pe_appetite": scores.get("pe_appetite"),
            "score_ticket_fit": scores.get("ticket_fit"),
            "score_tech_affinity": scores.get("tech_affinity"),
            "fit_details": json.dumps(details),
            "criteria_assessed": len(scores),
            "error": None,
        }

    except json.JSONDecodeError as e:
        logger.error(f"[InvestorFill] JSON parse failed for '{name}': {e}")
        return {"error": f"AI response parse failure: {e}"}
    except Exception as e:
        logger.error(f"[InvestorFill] Failed for '{name}': {e}")
        return {"error": str(e)}


def ch_enrich_investor(name: str, registration_number: str = "") -> Dict:
    """
    Companies House enrichment for UK investor entities (free API calls +
    at most one Gemini call for accounts parsing):
      - PSC register → who controls the vehicle (UHNWI discovery)
      - Officers → principals to contact
      - Latest filed net assets → AUM proxy (£M)
    Returns {psc_summary, officers_summary, net_assets_m, principal_name} (fields may be empty).
    """
    from services.companies_house_service import (
        get_psc_summary, get_officers_summary, _search_company, _pick_best_match,
        _get_accounts_filings, _download_accounts_pdf, _parse_accounts_pdf_with_gemini,
    )

    out = {"psc_summary": "", "officers_summary": "", "net_assets_m": None, "principal_name": ""}

    number = (registration_number or "").strip()
    if not number:
        # Try to find the entity on the register (strict name gate — no wrong matches)
        results = _search_company(name)
        best = _pick_best_match(results, name) if results else None
        if not best or best.get("_match_gate") not in ("exact", "exact-core", "contains"):
            return out
        number = best.get("company_number", "")
    if not number:
        return out

    psc = get_psc_summary(number)
    officers = get_officers_summary(number)
    out["psc_summary"] = psc["psc_summary"]
    out["officers_summary"] = officers["officers_summary"]

    # Principal: first individual PSC, else first director
    if psc["psc_individuals"]:
        out["principal_name"] = psc["psc_individuals"][0]
    elif officers["directors"]:
        out["principal_name"] = officers["directors"][0]["name"]

    # Net assets from the latest accounts filing (1 Gemini PDF parse)
    try:
        filings = _get_accounts_filings(number, max_items=2)
        if filings:
            pdf = _download_accounts_pdf(filings[0])
            if pdf:
                parsed = _parse_accounts_pdf_with_gemini(pdf, name, number, filings[0].get("date", ""))
                if parsed and not parsed.get("error"):
                    na = parsed.get("net_assets_current")
                    if na is not None:
                        out["net_assets_m"] = round(float(na) / 1_000_000, 2)
    except Exception as e:
        logger.warning(f"[InvestorFill/CH] Net assets extraction failed for '{name}': {e}")

    logger.info(f"[InvestorFill/CH] '{name}' (#{number}): principal={out['principal_name'] or 'n/a'}, net_assets_m={out['net_assets_m']}")
    return out


def mine_investors_from_companies(companies: list, min_fit_score: float = 0.4) -> list:
    """
    Extract investor names from high-fit companies' PitchBook data
    (active_investors / former_investors comma-separated fields). NO AI —
    raw extraction; InvestorFill enriches per-investor on demand.
    """
    investors: Dict[str, Dict] = {}

    # Names that are noise, not investors
    skip = {"undisclosed", "undisclosed investors", "n/a", "none", "unknown", "-", "angel investors", "individual investors", "management"}

    for c in companies:
        fit = c.get("averroes_fit_score")
        status = c.get("status", "")
        # High-fit = scored well, or qualified when unscored
        if fit is not None and fit < min_fit_score:
            continue
        if fit is None and status != "Qualified":
            continue

        company_name = c.get("name", "")
        for field, label in [("active_investors", "active"), ("former_investors", "former")]:
            raw = c.get(field) or ""
            for inv_name in raw.split(","):
                inv_name = inv_name.strip()
                if not inv_name or len(inv_name) < 3 or inv_name.lower() in skip:
                    continue
                key = inv_name.lower()
                if key in investors:
                    existing = investors[key]["source_companies"]
                    if company_name not in existing:
                        investors[key]["source_companies"] = f"{existing}, {company_name}"
                else:
                    investors[key] = {
                        "name": inv_name,
                        "investor_type": "Unknown",
                        "source": "Mined from portfolio",
                        "source_companies": company_name,
                        "description": f"Backs {company_name} ({label} investor per PitchBook).",
                        "status": "Identified",
                    }

    return list(investors.values())
