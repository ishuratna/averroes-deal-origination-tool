"""
InvestorFill — AI enrichment + LP fit scoring for investors.

Mirrors SmartFill for companies. One Gemini 2.5 Flash call with Google Search
grounding per investor: classifies type, finds HQ/AUM/ticket size/contacts,
and scores fit against the Averroes LP criteria:

  1. Geography    — UK / Europe / Saudi Arabia (KSA) based
  2. PE appetite  — history of investing in PE funds, direct deals or SPVs
  3. Ticket fit   — typical commitment £250K–£5M
  4. Tech affinity — exposure/interest in B2B software & tech

Composite = average of assessable criteria; minimum 3 of 4 assessable,
otherwise lp_fit_score = null (never a guessed number).
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


def investor_fill(name: str, context: Dict = None) -> Dict:
    """
    Enrich + score one investor. Returns dict of fields for
    InvestorBQHandler.update_enrichment, plus 'error' key on failure.
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

        prompt = f"""You are an investor-relations analyst at Averroes Capital, a UK lower-mid-market
private equity firm investing in founder-led B2B SaaS companies (£2.5-10M revenue).
We are researching potential LIMITED PARTNERS (LPs) — investors who might commit capital
to our fund or co-invest in our deals.

INVESTOR TO RESEARCH: "{name}"
{f'Known portfolio overlap (companies in our pipeline they have backed): {portfolio}' if portfolio else ''}

Search the web thoroughly for this investor and determine:

1. TYPE — classify as exactly one of: "Family Office", "Fund of Funds", "HNWI", "UHNWI",
   "VC", "PE", "Angel", "Corporate", "Sovereign/Institutional", "Unknown"
2. PROFILE — HQ city & country, website, AUM in £ millions (convert currencies),
   typical investment/commitment size range in £ millions, 1-2 sentence description
3. CONTACT — key principal or IR contact name, email if public, LinkedIn URL
4. LP FIT SCORING — score each criterion 0.0-1.0 based on EVIDENCE found:

   a) geography: UK=1.0, Ireland/Western Europe=0.8, Saudi Arabia/GCC=0.9,
      rest of Europe=0.6, US/other=0.3
   b) pe_appetite: proven LP commitments to PE funds or direct private deals=0.9-1.0,
      some private markets activity=0.5-0.8, public markets only=0.1-0.3
   c) ticket_fit: typical commitments within £250K-£5M=0.9-1.0, £5-20M=0.6
      (could still write smaller cheques), >£50M institutional minimums=0.2, unknown=null
   d) tech_affinity: significant B2B software/tech investments=0.8-1.0,
      some tech exposure=0.5-0.7, no tech interest evident=0.2

IMPORTANT:
- Base every score on evidence found via search. If you cannot find evidence for
  a criterion, set its score to null — do NOT guess.
- If you cannot confidently identify this investor at all (too generic a name,
  no online presence), set "identified" to false.

Return ONLY valid JSON:
{{
  "identified": true or false,
  "investor_type": "one of the types above",
  "hq_city": "string or null",
  "hq_country": "string or null",
  "region": "UK" | "Europe" | "KSA/GCC" | "US" | "Other",
  "website": "string or null",
  "aum_m": number or null,
  "ticket_min_m": number or null,
  "ticket_max_m": number or null,
  "description": "1-2 sentences",
  "contact_name": "string or null",
  "contact_email": "string or null",
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

        return {
            "investor_type": result.get("investor_type") or "Unknown",
            "aum_m": _f(result.get("aum_m")),
            "ticket_min_m": _f(result.get("ticket_min_m")),
            "ticket_max_m": _f(result.get("ticket_max_m")),
            "region": result.get("region") or "",
            "hq_city": result.get("hq_city") or "",
            "hq_country": result.get("hq_country") or "",
            "website": result.get("website") or "",
            "description": result.get("description") or "",
            "contact_name": result.get("contact_name") or "",
            "contact_email": result.get("contact_email") or "",
            "linkedin_url": result.get("linkedin_url") or "",
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
