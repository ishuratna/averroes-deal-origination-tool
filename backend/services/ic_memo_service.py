"""
IC Memo one-pager for Engaged companies.

Data honesty is the design principle: every NUMBER on the memo (financial
table, scorecard, cap table, valuation math) is assembled IN CODE from the
stored record with its source labelled; gaps say "Not yet known". The AI
writes only the narrative sections around those facts, plus one market
context paragraph from a single grounded search (weight 1 on the shared
budget). Style rule: no em dashes anywhere in this file.
"""
import os
import json
import logging
from datetime import date, datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

_MANDATE = (
    "Averroes Capital invests 15 to 40 million pounds of equity per deal in UK and "
    "Ireland B2B software companies, taking majority or significant minority (25 percent "
    "or more) stakes. Investable revenue envelope 2.5 to 40 million pounds, core sweet "
    "spot 8 to 20 million. Valuation heuristic 4 to 6 times revenue."
)


def _m(v) -> Optional[float]:
    """Raw GBP -> millions, None-safe."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v / 1e6, 2) if abs(v) > 100000 else round(v, 2)


def _fmt_gbp_m(v) -> str:
    m = _m(v)
    return f"£{m}M" if m is not None else "Not yet known"


def _financial_rows(c: dict) -> list:
    """Financial table rows straight from the record, source-tagged."""
    rows = []
    src_rev = "CH filed" if c.get("revenue_y1_date") and "Inven" not in str(c.get("revenue_y1_date")) else \
              ("Inven" if c.get("revenue_y1") else "")
    for label, key, datekey in [("Revenue (latest)", "revenue_y1", "revenue_y1_date"),
                                ("Revenue (prior)", "revenue_y2", "revenue_y2_date"),
                                ("Revenue (2 yrs prior)", "revenue_y3", "revenue_y3_date")]:
        if c.get(key):
            d = str(c.get(datekey) or "")
            rows.append({"label": label + (f" ({d})" if d else ""),
                         "value": _fmt_gbp_m(c[key]),
                         "source": "Inven" if "Inven" in d else "CH filed"})
    if not rows and c.get("revenue_estimate_m"):
        rows.append({"label": "Revenue (estimated)", "value": f"£{c['revenue_estimate_m']}M",
                     "source": f"estimate, {c.get('revenue_confidence') or 'low'} confidence"})
    gp, rev = c.get("gross_profit_y1"), c.get("revenue_y1")
    if gp and rev:
        rows.append({"label": "Gross margin", "value": f"{round(100 * float(gp) / float(rev))}%", "source": "CH filed"})
    for label, key in [("Profit (latest)", "profit_y1"), ("Cash", "cash_y1"), ("Net assets", "net_assets_y1")]:
        if c.get(key) is not None:
            rows.append({"label": label, "value": _fmt_gbp_m(c[key]), "source": "CH filed"})
    emp = c.get("employees_ch") or c.get("employees")
    if emp:
        g1 = c.get("employee_growth_1yr_pct")
        rows.append({"label": "Employees", "value": f"{int(emp)}" + (f" ({round(g1)}% 1yr)" if g1 is not None else ""),
                     "source": "CH filed" if c.get("employees_ch") else "Inven"})
    if c.get("revenue_cagr_3yr_pct") is not None:
        rows.append({"label": "Revenue CAGR (3yr)", "value": f"{round(float(c['revenue_cagr_3yr_pct']), 1)}%", "source": "Inven"})
    if src_rev:
        pass  # src captured per-row above
    return rows


def _deal_math(c: dict) -> dict:
    """Valuation and stake arithmetic, computed, never narrated by the AI."""
    rev_m = _m(c.get("revenue_y1")) or c.get("revenue_estimate_m")
    if not rev_m:
        return {"available": False, "note": "No revenue figure on record; valuation range not computable."}
    lo, hi = round(4 * float(rev_m), 1), round(6 * float(rev_m), 1)
    mid = round(5 * float(rev_m), 1)
    stake_15 = min(100, round(100 * 15 / mid)) if mid else None
    stake_40 = min(100, round(100 * 40 / mid)) if mid else None
    return {
        "available": True,
        "revenue_m": float(rev_m),
        "estimated": not bool(c.get("revenue_y1")),
        "val_low_m": lo, "val_mid_m": mid, "val_high_m": hi,
        "stake_note": f"At ~£{mid}M (5x revenue), the £15-40M cheque buys approximately {stake_15}%-{stake_40}%."
                      if stake_15 else "",
    }


def _scorecard(c: dict) -> dict:
    def pct(v):
        return round(float(v) * 100) if v is not None else None
    return {
        "fit": pct(c.get("averroes_fit_score")),
        "subscores": [
            {"label": "Revenue size", "value": pct(c.get("score_revenue_size"))},
            {"label": "Revenue growth", "value": pct(c.get("score_revenue_growth"))},
            {"label": "Employee growth", "value": pct(c.get("score_employee_growth"))},
            {"label": "Business fit", "value": pct(c.get("score_business_fit"))},
            {"label": "Market sentiment", "value": pct(c.get("score_market_sentiment"))},
        ],
    }


def _cap_table(c: dict) -> dict:
    holders = []
    raw = c.get("ch_cap_table")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            holders = parsed.get("holders") or parsed.get("shareholders") or []
        except Exception:
            holders = []
    return {
        "founder_pct": c.get("ch_founder_pct"),
        "as_of": c.get("ch_cap_table_date") or "",
        "holders": holders[:8],
        "psc": c.get("ch_psc_summary") or "",
        "ownership_verified": c.get("ch_ownership_verified") or "",
        "charges": c.get("ch_charges_summary") or "",
    }


def _flags(c: dict) -> list:
    """Registry red flags, deterministic."""
    out = []
    if c.get("ch_accounts_overdue"):
        out.append("Accounts OVERDUE at Companies House")
    if c.get("ch_insolvency_summary"):
        out.append(f"Insolvency history: {c['ch_insolvency_summary']}")
    if c.get("ch_charges_count"):
        out.append(f"{c['ch_charges_count']} registered charge(s): {c.get('ch_charges_summary') or ''}".strip())
    if c.get("ch_status") and str(c["ch_status"]).lower() not in ("active",):
        out.append(f"Company status: {c['ch_status']}")
    return out


def _returns_scenarios(c: dict) -> dict:
    """ILLUSTRATIVE pre-DD returns, computed in code, unlevered, 5-year hold.
    Entry at 5x current revenue; scenarios vary exit multiple and growth.
    Labelled illustrative everywhere - never presented as underwritten."""
    dm = _deal_math(c)
    if not dm.get("available"):
        return {"available": False, "note": "No revenue on record; returns not computable."}
    rev = dm["revenue_m"]
    entry_ev = round(5 * rev, 1)
    hold = 5
    rows = []
    for label, growth, exit_mult in (("Downside", 0.05, 4.0), ("Base", 0.15, 5.0), ("Upside", 0.25, 6.0)):
        exit_rev = rev * ((1 + growth) ** hold)
        exit_ev = exit_rev * exit_mult
        moic = exit_ev / entry_ev
        irr = moic ** (1 / hold) - 1
        rows.append({"scenario": label, "revenue_growth_pct": round(growth * 100),
                     "exit_multiple": exit_mult, "moic": round(moic, 1),
                     "irr_pct": round(irr * 100)})
    return {"available": True, "entry_ev_m": entry_ev, "entry_multiple": 5.0,
            "hold_years": hold, "estimated_revenue": dm.get("estimated", False),
            "scenarios": rows,
            "note": ("Illustrative only, pre-diligence: unlevered, entry at 5x current revenue, "
                     "no margin assumptions. Sensitivities to test in DD: exit multiple, revenue growth, "
                     "EBITDA margin, leverage, holding period."
                     + (" Revenue is an ESTIMATE." if dm.get("estimated") else ""))}


def _verified_vs_tbd(c: dict) -> dict:
    """Diligence status at origination stage: what the registry has already
    verified vs the workstreams a real DD must cover. Code-built, honest."""
    verified = []
    if c.get("ch_company_number"):
        verified.append(f"Registry identity confirmed ({c.get('ch_official_name') or ''} #{c['ch_company_number']}, {c.get('ch_match_confidence') or ''} confidence)")
    if c.get("revenue_y1") or c.get("ch_history"):
        verified.append("Filed financials extracted from Companies House accounts (multi-year)")
    if c.get("ch_cap_table"):
        verified.append(f"Cap table built from CS01 ({str(c.get('ch_cap_table_date') or '')[:10]})"
                        + (", PSC cross-checked" if '"psc_check": "consistent' in (c.get("ch_cap_table") or "") else ""))
    if c.get("ch_psc_summary"):
        verified.append("Ownership control verified via PSC register")
    if c.get("ch_charges_count") == 0:
        verified.append("No outstanding registered charges (no secured debt on record)")
    if not c.get("ch_accounts_overdue") and c.get("ch_company_number"):
        verified.append("Accounts filings up to date")
    return {"verified": verified}


def build_ic_memo(company: dict, emails: List[dict]) -> dict:
    """Assemble the memo (v2, classic 10-section IC flow):
    1 Executive Summary  2 Investment Thesis  3 Company Overview  4 Market
    5 Financials  6 Diligence Status  7 Value Creation  8 Risks  9 Returns
    10 Recommendation. All numbers computed in code; AI writes prose only."""
    narrative = _narrative(company, emails)
    dm = _deal_math(company)
    exec_facts = {
        "company": company.get("name"),
        "sector": company.get("sector") or "",
        "indicative_ev": (f"£{dm['val_low_m']}M-£{dm['val_high_m']}M (4-6x revenue"
                          + (", estimated" if dm.get("estimated") else "") + ")") if dm.get("available") else "Not yet known",
        "equity_investment": "£15-40M (mandate)",
        "ownership_targeted": dm.get("stake_note") or "25%+ (mandate minimum)",
        "stage": company.get("status") or "",
        "fit_score": _scorecard(company).get("fit"),
        "recommendation": narrative.get("recommendation_action") or "",
    }
    return {
        "v": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "header": {
            "name": company.get("name"),
            "official_name": company.get("ch_official_name") or "",
            "ch_number": company.get("ch_company_number") or "",
            "sector": company.get("sector") or "",
            "hq": company.get("hq_city") or company.get("hq_location") or company.get("region") or "",
            "founded": company.get("year_founded") or (str(company.get("ch_incorporated_date") or "")[:4]),
            "website": company.get("website") or "",
            "stage": company.get("status") or "",
            "band": company.get("revenue_band") or "",
        },
        "executive_summary": {"facts": exec_facts, "summary": narrative.get("executive_summary") or ""},
        "investment_thesis": narrative.get("investment_thesis") or [],
        "company_overview": narrative.get("company_overview") or {},
        "market": narrative.get("market") or {},
        "financials": _financial_rows(company),
        "deal_math": dm,
        "scorecard": _scorecard(company),
        "cap_table": _cap_table(company),
        "diligence": {**_verified_vs_tbd(company),
                      "open_questions": narrative.get("diligence_open_questions") or {}},
        "value_creation": narrative.get("value_creation") or [],
        "risks": narrative.get("risks") or [],
        "registry_flags": _flags(company),
        "returns": _returns_scenarios(company),
        "recommendation": narrative.get("recommendation") or "",
        "narrative": narrative,  # kept for backward compatibility with v1 renderers
    }


def _email_block(emails: List[dict]) -> str:
    rows = []
    for m in sorted(emails or [], key=lambda x: str(x.get("sent_at") or ""))[-8:]:
        rows.append(f"[{m.get('direction')}] {str(m.get('sent_at') or '')[:10]} "
                    f"{m.get('subject') or ''}: {(m.get('snippet') or '')[:250]}")
    return "\n".join(rows) if rows else "(no logged emails)"


def _facts_block(company: dict) -> str:
    keys = ["name", "sector", "description", "website", "hq_city", "region", "year_founded",
            "status", "revenue_band", "averroes_fit_score", "revenue_estimate_m", "revenue_confidence",
            "employees", "employees_ch", "employee_growth_1yr_pct", "revenue_cagr_3yr_pct",
            "ch_official_name", "ch_status", "ch_founder_pct", "ch_psc_summary", "ch_ownership_verified",
            "ch_charges_summary", "ch_accounts_regime", "ch_last_resolution", "ownership",
            "action_bucket", "action_rationale", "unfit_reason", "keywords", "verticals"]
    lines = []
    for k in keys:
        v = company.get(k)
        if v not in (None, ""):
            lines.append(f"{k}: {str(v)[:400]}")
    for k in ("revenue_y1", "revenue_y2", "revenue_y3", "gross_profit_y1", "profit_y1", "cash_y1", "net_assets_y1"):
        if company.get(k) is not None:
            lines.append(f"{k}: {_fmt_gbp_m(company[k])}")
    return "\n".join(lines)


def _narrative(company: dict, emails: List[dict]) -> dict:
    """One grounded Gemini call for the prose sections. Facts only; the model
    is told that inventing a number that is not in the record or its cited
    search results is a failure."""
    api_key = os.getenv("GEMINI_API_KEY")
    empty = {"executive_summary": "", "recommendation_action": "", "investment_thesis": [],
             "company_overview": {}, "market": {}, "diligence_open_questions": {},
             "value_creation": [], "risks": [], "recommendation": "",
             "engagement_status": ""}
    if not api_key:
        return empty
    dm = _deal_math(company)
    prompt = f"""You are an associate at Averroes Capital writing the prose sections of an IC memo that follows the classic PE structure. Be concise, factual and honest. Where information is missing, write "Not yet known" instead of guessing. NEVER invent a number that is not in the record below or in your search results. This is an ORIGINATION-stage memo (pre-diligence): recommendations are about progressing the deal (first meeting, management session, proceed to DD), never about approving an acquisition. No em dashes anywhere.

THE MANDATE: {_MANDATE}

COMPANY RECORD (verified data, the only permitted source for company facts):
{_facts_block(company)}

DEAL MATH (already computed, do not recompute): {json.dumps(dm)}

EMAIL THREAD WITH THE FOUNDER (oldest first):
{_email_block(emails)}

You may use Google Search ONCE conceptually: for the market section only (market size, growth, competitors, demand drivers, regulation). Name sources inline in plain text, e.g. "(per Gartner, 2026)".

Write these sections:
1. executive_summary: 3-4 sentences. What the company does, how we sourced it (proprietary founder outreach), the size/fit picture from the record, and where the deal stands.
2. recommendation_action: 2-5 words, one of the spirit of: "Advance to first meeting" / "Progress to DD" / "Hold - revisit [when]" / "Pass". Choose from the record and thread.
3. investment_thesis: 4-6 short bullet strings. Only claims supported by the record or search (growth rates, recurring revenue nature, founder strength, buy-and-build angle). Where a classic thesis point (e.g. net revenue retention) is unknown, DO NOT list it.
4. company_overview: {{"history": "1-2 sentences", "products": "...", "geography": "...", "customers": "...", "revenue_mix": "...", "team": "..."}} - each from the record or "Not yet known".
5. market: {{"size": "...", "growth": "...", "competitors": "...", "demand_drivers": "...", "regulation": "..."}} - 1-2 sentences each FROM SEARCH with sources named; "Not yet known" where search gives nothing solid.
6. diligence_open_questions: {{"commercial": ["..."], "financial": ["..."], "legal": ["..."], "technology": ["..."]}} - 1-3 questions per workstream that a real DD must answer for THIS company.
7. value_creation: 3-5 short bullet strings, labelled as hypotheses, grounded in what the company actually does (pricing, geographic expansion, enterprise motion, bolt-ons, product extensions, operational efficiency).
8. risks: 3-5 items, each {{"risk": "...", "mitigation": "..."}}. Start with any registry red flags in the record; mitigations must be realistic (diversify, verify in DD, structure protections).
9. engagement_status: 2 sentences from the thread: when we reached out, what the founder said, current next step.
10. recommendation: one closing paragraph (3-4 sentences): the action, why the record supports it, what the illustrative economics suggest, and the key risks with how they are manageable. Never claim diligence findings that have not happened.

Return ONLY valid JSON with exactly those keys."""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, Tool, GoogleSearch

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], temperature=0.2),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        result = json.loads(text[start:end + 1])
        for k in empty:
            result.setdefault(k, empty[k])
        # Containment: strip em dashes from every string the model produced
        def _clean(v):
            if isinstance(v, str):
                return v.replace("\u2014", "-").replace("\u2013", "-")
            if isinstance(v, list):
                return [_clean(x) for x in v]
            if isinstance(v, dict):
                return {kk: _clean(vv) for kk, vv in v.items()}
            return v
        return _clean(result)
    except Exception as e:
        logger.warning(f"[ICMemo] Narrative generation failed for {company.get('name')}: {e}")
        return empty
