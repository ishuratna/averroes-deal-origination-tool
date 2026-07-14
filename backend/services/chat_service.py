"""
Deal Intelligence Chat: answers questions STRICTLY from the database
(companies + LPs). Never guesses. If the data doesn't contain the answer,
it says so and offers a web search; the grounded search only runs when the
user explicitly asks for it (separate call, budget-enforced in main.py).
"""
import json
import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# Fields worth showing the model for a matched company (skip huge/internal ones)
_COMPANY_FIELDS = [
    "name", "status", "source", "sector", "region", "website", "description",
    "hq_city", "hq_country", "employees", "employees_ch", "year_founded",
    "keywords", "verticals", "industry_group", "business_status", "ownership",
    "contact_name", "contact_email", "contact_title", "linkedin_url",
    "revenue_m", "revenue_y1", "revenue_y1_date", "revenue_y2", "revenue_y2_date",
    "revenue_estimate_m", "revenue_source", "revenue_confidence", "revenue_band",
    "gross_profit_y1", "profit_y1", "total_assets_y1", "net_assets_y1", "cash_y1",
    "estimated_ebitda", "enterprise_value_m", "valuation_estimate_m",
    "total_raised_m", "financing_status", "last_financing_date", "last_financing_size_m",
    "last_financing_type", "active_investors", "former_investors", "competitors",
    "ch_company_number", "ch_official_name", "ch_status", "ch_incorporated_date",
    "ch_sic_codes", "filing_type", "ch_match_confidence",
    "ch_psc_summary", "ch_ownership_verified", "ch_charges_count", "ch_charges_summary",
    "ch_last_share_allotment", "ch_accounts_next_due",
    "averroes_fit_score", "score_employee_growth", "score_revenue_growth",
    "score_revenue_size", "score_business_fit", "score_market_sentiment",
    "unfit_reason", "size_bucket",
    "last_smartfill_at", "outreach_drafted_at", "outreach_sent_at",
    "last_reply_at", "reply_classification", "ingested_at",
    "stage_entered_at", "qualified_at", "contacted_at",
]

_INVESTOR_FIELDS = [
    "name", "investor_type", "status", "source", "hq_city", "hq_country", "region",
    "global_region", "website", "description", "contact_name", "contact_email",
    "contact_title", "aum_m", "net_assets_m", "ticket_min_m", "ticket_max_m",
    "year_founded", "strategy_preferences", "geo_preferences", "open_to_first_time",
    "num_commitments", "num_active_commitments", "num_pe_commitments",
    "total_commitments_m", "other_preferences", "lp_fit_score", "score_geography",
    "score_pe_appetite", "score_ticket_fit", "score_tech_affinity", "fit_details",
    "psc_summary", "officers_summary", "source_companies", "notes", "ingested_at",
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _match_entities(message: str, universe: List[dict], investors: List[dict], limit: int = 6):
    """Find companies/LPs the question refers to, by name containment."""
    msg = _norm(message)
    matched_companies, matched_investors = [], []
    for row in universe:
        n = _norm(row.get("name", ""))
        if n and len(n) >= 3 and (n in msg or all(w in msg for w in n.split()[:2] if len(w) > 3)):
            matched_companies.append(row)
    for row in investors:
        n = _norm(row.get("name", ""))
        if n and len(n) >= 3 and n in msg:
            matched_investors.append(row)
    return matched_companies[:limit], matched_investors[:limit]


def _slim(row: dict, fields: List[str]) -> dict:
    out = {}
    for f in fields:
        v = row.get(f)
        if v not in (None, "", 0) or f in ("last_smartfill_at",):
            out[f] = str(v) if v is not None else None
    return out


def _index_line_company(c: dict) -> str:
    return (f"{c.get('name')} | status: {c.get('status')} | sector: {c.get('sector') or '?'} | "
            f"fit: {c.get('averroes_fit_score') if c.get('averroes_fit_score') is not None else '-'} | "
            f"band: {c.get('revenue_band') or '-'} | smartfilled: {'yes' if c.get('last_smartfill_at') else 'NO'}")


def _index_line_investor(i: dict) -> str:
    return (f"{i.get('name')} | type: {i.get('investor_type') or '?'} | status: {i.get('status') or '-'} | "
            f"lp_fit: {i.get('lp_fit_score') if i.get('lp_fit_score') is not None else '-'} | "
            f"geo: {i.get('hq_country') or i.get('region') or '-'}")


def build_chat_context(message: str, universe: List[dict], investors: List[dict]) -> Dict:
    """Assemble everything the model may use: matched full records + compact index."""
    mc, mi = _match_entities(message, universe, investors)
    context = {
        "matched_companies": [_slim(c, _COMPANY_FIELDS) for c in mc],
        "matched_investors": [_slim(i, _INVESTOR_FIELDS) for i in mi],
        "company_index": [_index_line_company(c) for c in universe[:600]],
        "investor_index": [_index_line_investor(i) for i in investors[:400]],
    }
    return context


SYSTEM_RULES = """
You are the Averroes Capital deal intelligence assistant. You answer questions about the
companies (deal targets) and investors (LPs) in the Averroes database.

ABSOLUTE RULES:
1. Answer ONLY from the DATA below. Never use outside knowledge about a company or
   investor, even if you recognise the name. Never guess, infer, or fill gaps.
2. If the answer is not in the data, say plainly that the database does not have it,
   and set "needs_web_search": true so the user can choose to run a live web search.
   Do NOT attempt an answer from memory.
3. If a matched company has smartfilled = NO (empty last_smartfill_at), tell the user:
   run SmartFill on it to pull contacts, Companies House financials and the fit score.
4. For aggregate questions (how many, list, top by fit, which are stale) use the
   INDEX lines. Figures like revenue_y1 are raw GBP; revenue_m and *_m fields are in
   millions of GBP.
5. Be concise and specific. Use short paragraphs or compact lists. Plain English.
6. Money formatting: £X.XM. Dates: 12 Jul 2026.

Return ONLY valid JSON: {"reply": "your answer", "needs_web_search": true or false}
"""


def chat_answer(message: str, history: List[Dict], universe: List[dict], investors: List[dict]) -> Dict:
    """Data-only answer. Returns {reply, needs_web_search, matched}."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    context = build_chat_context(message, universe, investors)
    fallback = {"reply": "The AI service is not configured (GEMINI_API_KEY missing).", "needs_web_search": False}
    if not api_key:
        return fallback

    convo = "\n".join(f"{h.get('role', 'user')}: {h.get('content', '')}" for h in history[-10:])
    prompt = f"""{SYSTEM_RULES}

CONVERSATION SO FAR:
{convo or '(none)'}

DATA:
MATCHED COMPANIES (full records for entities named in the question):
{json.dumps(context['matched_companies'], default=str)}

MATCHED INVESTORS:
{json.dumps(context['matched_investors'], default=str)}

COMPANY INDEX ({len(context['company_index'])} companies):
{chr(10).join(context['company_index'])}

INVESTOR INDEX ({len(context['investor_index'])} investors):
{chr(10).join(context['investor_index'])}

USER QUESTION: {message}
"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        result = json.loads(text[start:end + 1])
        return {
            "reply": result.get("reply", "I could not form an answer from the database."),
            "needs_web_search": bool(result.get("needs_web_search", False)),
            "matched": [c.get("name") for c in context["matched_companies"]] +
                       [i.get("name") for i in context["matched_investors"]],
        }
    except Exception as e:
        logger.error(f"[Chat] answer failed: {e}")
        return {"reply": f"Chat failed: {e}", "needs_web_search": False, "matched": []}


def chat_web_search(message: str, history: List[Dict], universe: List[dict], investors: List[dict]) -> Dict:
    """
    Grounded answer, run ONLY when the user explicitly pressed 'Run web search'.
    Caller (main.py) enforces the daily grounding budget before invoking this.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"reply": "The AI service is not configured (GEMINI_API_KEY missing).", "needs_web_search": False}
    context = build_chat_context(message, universe, investors)
    convo = "\n".join(f"{h.get('role', 'user')}: {h.get('content', '')}" for h in history[-10:])
    prompt = f"""You are the Averroes Capital deal intelligence assistant. The user asked a question
the internal database could not answer, and explicitly asked for a LIVE WEB SEARCH.

Use Google Search to answer. Rules:
- Clearly ground every claim in what you find; if the search does not settle it, say so.
- Never invent facts. Prefer primary sources (company site, filings, reputable press).
- Start the reply with "From a live web search:" so the user knows the source.
- Be concise. Plain English. Money as £X.XM where applicable.

INTERNAL DATA FOR CONTEXT (may help disambiguate the entity):
{json.dumps(context['matched_companies'][:2], default=str)}

CONVERSATION SO FAR:
{convo or '(none)'}

USER QUESTION: {message}

Return ONLY valid JSON: {{"reply": "your answer"}}
"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())]),
        )
        text = ""
        if response.candidates:
            for part in (response.candidates[0].content.parts or []):
                if getattr(part, "text", None):
                    text += part.text
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            result = json.loads(text[start:end + 1])
            reply = result.get("reply", text)
        else:
            reply = text or "The web search returned nothing usable."
        return {"reply": reply, "needs_web_search": False,
                "matched": [c.get("name") for c in context["matched_companies"]]}
    except Exception as e:
        logger.error(f"[Chat] web search failed: {e}")
        return {"reply": f"Web search failed: {e}", "needs_web_search": False, "matched": []}
