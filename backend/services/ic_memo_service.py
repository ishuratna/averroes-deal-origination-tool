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


def build_ic_memo(company: dict, emails: List[dict]) -> dict:
    """Assemble the memo: deterministic facts + one grounded narrative call."""
    narrative = _narrative(company, emails)
    return {
        "v": 1,
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
        "financials": _financial_rows(company),
        "deal_math": _deal_math(company),
        "scorecard": _scorecard(company),
        "cap_table": _cap_table(company),
        "registry_flags": _flags(company),
        "narrative": narrative,
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
    empty = {"opportunity": "", "mandate_fit": [], "deal_hypothesis": "",
             "engagement_status": "", "market_context": "", "risks": [],
             "open_questions": [], "recommendation": ""}
    if not api_key:
        return empty
    dm = _deal_math(company)
    prompt = f"""You are an associate at Averroes Capital writing the narrative sections of a one page IC memo. Be concise, factual and honest. Where information is missing, write "Not yet known" instead of guessing. NEVER invent a number that is not in the record below or in your search results. No em dashes anywhere.

THE MANDATE: {_MANDATE}

COMPANY RECORD (verified data, the only permitted source for company facts):
{_facts_block(company)}

DEAL MATH (already computed, do not recompute): {json.dumps(dm)}

EMAIL THREAD WITH THE FOUNDER (oldest first):
{_email_block(emails)}

You may use Google Search ONCE conceptually: for the market_context section only (market size, obvious competitors, notable recent news about the company or its space). Name sources inline in plain text, e.g. "(per TechCrunch, Jan 2026)".

Write these sections:
1. opportunity: 2-3 sentences. What the company does, that Averroes sourced it proprietarily through direct founder outreach, and the single most compelling reason it is interesting.
2. mandate_fit: exactly 3 items, each {{"check": "...", "verdict": "PASS"|"FAIL"|"UNKNOWN", "evidence": "one sentence citing the record"}}. Checks: revenue envelope (2.5 to 40m GBP), UK or Ireland B2B software thesis, ownership amenable to a 25 percent plus stake.
3. deal_hypothesis: 2-3 sentences using ONLY the deal math above plus any structure signals from the founder emails. Label it as a hypothesis.
4. engagement_status: 2 sentences. When we reached out, what the founder said (use the thread), and the current next step.
5. market_context: 2-3 sentences from search, sources named. If search yields nothing solid, write "No reliable market context found."
6. risks: 3-5 short strings. Start with any registry red flags in the record, then data gaps and business risks. Honest.
7. open_questions: 3-4 short strings. The questions the first meeting must answer.
8. recommendation: one sentence, e.g. "Proceed to a first meeting to validate X and Y."

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
