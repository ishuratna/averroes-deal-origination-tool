"""
Averroes Fit Scoring Model

Scores QUALIFIED companies on 5 metrics (0-1 each):
  1. Employee Growth YoY — growth trajectory signal
  2. Revenue Growth — top-line momentum
  3. Revenue Size — maturity & scale (proxy from assets/EBITDA/valuation if needed)
  4. Business Model Fit — B2B, tech, SaaS alignment with Averroes thesis
  5. Market Sentiment — news exposure, brand strength, market positioning

Rules:
  - Only scored if company is already qualified (UK/Ireland + Tech + Size)
  - Minimum 4 of 5 metrics must be assessable
  - Missing metrics are excluded (NOT scored as 0) — normalize across available
  - Composite = average of available metric scores
"""

import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _compute_revenue_growth(company: dict) -> Optional[Dict]:
    """
    Compute revenue growth from CH filing data (y1 vs y2).
    Returns {score, value, explanation} or None.
    """
    rev_y1 = company.get("revenue_y1")
    rev_y2 = company.get("revenue_y2")

    if rev_y1 is None or rev_y2 is None:
        return None

    try:
        rev_y1 = float(rev_y1)
        rev_y2 = float(rev_y2)
    except (ValueError, TypeError):
        return None

    if rev_y2 <= 0:
        return None

    growth_pct = ((rev_y1 - rev_y2) / abs(rev_y2)) * 100

    # Score: 0-1 scale
    # Negative growth = 0-0.2
    # 0-10% = 0.2-0.5
    # 10-25% = 0.5-0.75
    # 25-50% = 0.75-0.9
    # 50%+ = 0.9-1.0
    if growth_pct < -20:
        score = 0.0
    elif growth_pct < 0:
        score = 0.1 + (growth_pct + 20) / 200  # 0.0 to 0.2
    elif growth_pct < 10:
        score = 0.2 + (growth_pct / 10) * 0.3  # 0.2 to 0.5
    elif growth_pct < 25:
        score = 0.5 + ((growth_pct - 10) / 15) * 0.25  # 0.5 to 0.75
    elif growth_pct < 50:
        score = 0.75 + ((growth_pct - 25) / 25) * 0.15  # 0.75 to 0.9
    else:
        score = min(1.0, 0.9 + ((growth_pct - 50) / 100) * 0.1)  # 0.9 to 1.0

    y1_date = company.get("revenue_y1_date", "latest")
    y2_date = company.get("revenue_y2_date", "prior")

    return {
        "score": round(score, 3),
        "value": round(growth_pct, 1),
        "explanation": f"Revenue grew {growth_pct:+.1f}% ({y2_date} → {y1_date})",
    }


def _compute_revenue_size(company: dict) -> Optional[Dict]:
    """
    Score revenue size — how well it fits the Averroes sweet spot.
    Uses CH revenue, PitchBook revenue, or proxies (assets, EBITDA, valuation, funding).
    Returns {score, value, explanation} or None.
    """
    # Try CH revenue first (raw GBP → millions)
    rev_m = None
    source = ""

    if company.get("revenue_y1") is not None:
        try:
            rev_m = float(company["revenue_y1"]) / 1_000_000
            source = "CH filings"
        except (ValueError, TypeError):
            pass

    # Fallback: PitchBook revenue_m
    if rev_m is None and company.get("revenue_m") is not None:
        try:
            rev_m = float(company["revenue_m"])
            source = "PitchBook"
        except (ValueError, TypeError):
            pass

    # Fallback: estimate from total assets (tech companies: revenue ≈ 2-3x assets)
    if rev_m is None and company.get("total_assets_y1") is not None:
        try:
            assets_m = float(company["total_assets_y1"]) / 1_000_000
            rev_m = assets_m * 2.5  # conservative multiplier for tech
            source = "estimated from total assets"
        except (ValueError, TypeError):
            pass

    # Fallback: estimate from EBITDA (revenue ≈ 5-8x EBITDA for SaaS)
    if rev_m is None and company.get("estimated_ebitda") is not None:
        try:
            ebitda = float(company["estimated_ebitda"])
            rev_m = ebitda * 6.5
            source = "estimated from EBITDA"
        except (ValueError, TypeError):
            pass

    # Fallback: estimate from valuation (revenue ≈ valuation / 5-8x for SaaS)
    if rev_m is None and company.get("valuation_estimate_m") is not None:
        try:
            val = float(company["valuation_estimate_m"])
            rev_m = val / 6
            source = "estimated from valuation"
        except (ValueError, TypeError):
            pass

    # Fallback: estimate from last financing round
    if rev_m is None and company.get("last_financing_size_m") is not None:
        try:
            last_round = float(company["last_financing_size_m"])
            rev_m = last_round * 3  # rough heuristic
            source = "estimated from last funding round"
        except (ValueError, TypeError):
            pass

    if rev_m is None or rev_m <= 0:
        return None

    # Score: Averroes sweet spot is £5-30M
    # Under £2M = 0.1-0.3
    # £2-5M = 0.3-0.5
    # £5-15M = 0.5-0.75 (good)
    # £15-30M = 0.75-1.0 (sweet spot)
    # £30-50M = 0.6-0.75 (acceptable but large end)
    # Over £50M = 0.3 (too big but already qualified somehow)
    if rev_m < 2:
        score = 0.1 + (rev_m / 2) * 0.2
    elif rev_m < 5:
        score = 0.3 + ((rev_m - 2) / 3) * 0.2
    elif rev_m < 15:
        score = 0.5 + ((rev_m - 5) / 10) * 0.25
    elif rev_m <= 30:
        score = 0.75 + ((rev_m - 15) / 15) * 0.25
    elif rev_m <= 50:
        score = 0.75 - ((rev_m - 30) / 20) * 0.15
    else:
        score = 0.3

    return {
        "score": round(score, 3),
        "value": round(rev_m, 2),
        "explanation": f"Revenue ~£{rev_m:.1f}M ({source})",
    }


def score_company(company: dict) -> Dict:
    """
    Score a qualified company on the 5 Averroes fit metrics.
    Uses existing data from the company dict (CH financials, PitchBook data, etc.)
    + Gemini with Google Search for qualitative assessments.

    Returns:
    {
        "averroes_fit_score": 0-1 composite,
        "score_employee_growth": 0-1 or None,
        "score_revenue_growth": 0-1 or None,
        "score_revenue_size": 0-1 or None,
        "score_business_fit": 0-1 or None,
        "score_market_sentiment": 0-1 or None,
        "score_details": JSON string with per-metric explanations,
        "metrics_available": int (how many of 5 were scored),
        "error": str or None,
    }
    """
    company_name = company.get("name", "Unknown")
    logger.info(f"[Scoring] Scoring '{company_name}'...")

    details = {}
    scores = {}

    # ── Metric 1: Employee Growth YoY ──
    # We'll get this from Gemini along with metrics 4 & 5

    # ── Metric 2: Revenue Growth ──
    rev_growth = _compute_revenue_growth(company)
    if rev_growth:
        scores["revenue_growth"] = rev_growth["score"]
        details["revenue_growth"] = rev_growth

    # ── Metric 3: Revenue Size ──
    rev_size = _compute_revenue_size(company)
    if rev_size:
        scores["revenue_size"] = rev_size["score"]
        details["revenue_size"] = rev_size

    # ── Metrics 1, 4, 5: Gemini assessment (employee growth, business fit, market sentiment) ──
    gemini_scores = _gemini_qualitative_scoring(company)
    if gemini_scores and not gemini_scores.get("error"):
        for metric in ["employee_growth", "business_fit", "market_sentiment"]:
            if gemini_scores.get(metric) is not None:
                scores[metric] = gemini_scores[metric]["score"]
                details[metric] = gemini_scores[metric]

    # ── Compute composite ──
    available = len(scores)
    if available < 4:
        logger.warning(f"[Scoring] Only {available}/5 metrics for '{company_name}' — insufficient (need 4+)")
        return {
            "averroes_fit_score": None,
            "score_employee_growth": scores.get("employee_growth"),
            "score_revenue_growth": scores.get("revenue_growth"),
            "score_revenue_size": scores.get("revenue_size"),
            "score_business_fit": scores.get("business_fit"),
            "score_market_sentiment": scores.get("market_sentiment"),
            "score_details": json.dumps(details),
            "metrics_available": available,
            "error": f"Insufficient data: only {available}/5 metrics available (need 4+)",
        }

    composite = sum(scores.values()) / available
    logger.info(f"[Scoring] '{company_name}' scored {composite:.3f} ({available}/5 metrics)")

    return {
        "averroes_fit_score": round(composite, 3),
        "score_employee_growth": scores.get("employee_growth"),
        "score_revenue_growth": scores.get("revenue_growth"),
        "score_revenue_size": scores.get("revenue_size"),
        "score_business_fit": scores.get("business_fit"),
        "score_market_sentiment": scores.get("market_sentiment"),
        "score_details": json.dumps(details),
        "metrics_available": available,
        "error": None,
    }


def _gemini_qualitative_scoring(company: dict) -> Optional[Dict]:
    """
    Use Gemini + Google Search to assess:
    1. Employee growth trend (YoY)
    4. Business model fit for Averroes (B2B, tech, SaaS)
    5. Market sentiment (news, exposure, brand)

    Returns dict with scored metrics or None on failure.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[Scoring] No GEMINI_API_KEY — skipping qualitative scoring")
        return None

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)

        # Build context from company data
        name = company.get("name", "Unknown")
        sector = company.get("sector", "")
        desc = company.get("description", "")
        region = company.get("region", "")
        website = company.get("website", "")
        employees = company.get("employees") or company.get("employees_ch")
        year_founded = company.get("year_founded")
        ch_name = company.get("ch_official_name", "")
        ch_sic = company.get("ch_sic_codes", "")
        keywords = company.get("keywords", "")
        verticals = company.get("verticals", "")
        revenue_y1 = company.get("revenue_y1")
        total_assets = company.get("total_assets_y1")

        prompt = f"""You are a Private Equity analyst at Averroes Capital, a UK-based lower-mid-market PE fund
focused on B2B SaaS, software, and tech-enabled services.

COMPANY TO ASSESS:
- Name: {name}
- Official CH Name: {ch_name or 'N/A'}
- Sector: {sector or 'Unknown'}
- Description: {desc[:300] if desc else 'N/A'}
- Region: {region or 'Unknown'}
- Website: {website or 'N/A'}
- Employees: {employees or 'Unknown'}
- Founded: {year_founded or 'Unknown'}
- SIC Codes: {ch_sic or 'N/A'}
- Keywords: {keywords or 'N/A'}
- Verticals: {verticals or 'N/A'}
- Latest Revenue: {'£' + str(round(revenue_y1/1e6, 2)) + 'M' if revenue_y1 else 'Not disclosed'}
- Total Assets: {'£' + str(round(total_assets/1e6, 2)) + 'M' if total_assets else 'Not disclosed'}

Search the web for this company and assess THREE things:

METRIC 1 — EMPLOYEE GROWTH (score 0.0 to 1.0):
Look for signals of employee growth or decline over the past 1-2 years.
Check LinkedIn company page, job postings, news about hiring/layoffs, Glassdoor reviews.
- 0.0-0.2: Shrinking workforce, layoffs
- 0.2-0.4: Flat or slight decline
- 0.4-0.6: Stable, modest growth
- 0.6-0.8: Healthy growth (10-30% YoY)
- 0.8-1.0: Rapid expansion (30%+ YoY, lots of open roles)
If you truly cannot find ANY employee data, set score to null.

METRIC 2 — BUSINESS MODEL FIT (score 0.0 to 1.0):
How well does this company fit the Averroes investment thesis?
- Is it B2B? (B2C scores much lower)
- Is it SaaS / subscription / recurring revenue? (one-time sales score lower)
- Is it tech-enabled or pure software? (non-tech scores low)
- Does it serve mid-market or enterprise clients?
- Is it in a growing market segment?
Score:
- 0.0-0.2: Not B2B, not tech
- 0.2-0.4: Some tech involvement but primarily services or B2C
- 0.4-0.6: Tech-enabled services, some recurring revenue
- 0.6-0.8: B2B software with subscription model
- 0.8-1.0: Pure B2B SaaS, strong recurring revenue, clear market fit

METRIC 3 — MARKET SENTIMENT (score 0.0 to 1.0):
Search for recent news, press coverage, awards, industry recognition, customer reviews.
- 0.0-0.2: Negative coverage, complaints, regulatory issues
- 0.2-0.4: Very little online presence, minimal coverage
- 0.4-0.6: Some coverage, neutral sentiment, basic online presence
- 0.6-0.8: Positive coverage, industry awards, good reviews, growing brand
- 0.8-1.0: Strong brand, frequent positive press, thought leadership, high customer satisfaction

IMPORTANT:
- Be rigorous — base scores on actual evidence found via search, not assumptions.
- If you cannot find sufficient evidence for a metric, set its score to null.
- Provide a brief explanation for each score citing what you found.

Return ONLY valid JSON:
{{
    "employee_growth": {{
        "score": 0.0-1.0 or null,
        "value": "Brief data point, e.g. 'LinkedIn shows ~50 employees, up from ~35 last year'",
        "explanation": "One sentence justification"
    }},
    "business_fit": {{
        "score": 0.0-1.0 or null,
        "value": "e.g. 'B2B SaaS, subscription model, serves mid-market'",
        "explanation": "One sentence justification"
    }},
    "market_sentiment": {{
        "score": 0.0-1.0 or null,
        "value": "e.g. 'Won Best SaaS 2025, featured in TechCrunch'",
        "explanation": "One sentence justification"
    }}
}}"""

        logger.info(f"[Scoring] Calling Gemini for qualitative metrics on '{name}'...")

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())]
            ),
        )

        text = response.text
        if not text:
            logger.warning("[Scoring] Gemini returned empty response")
            return None

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)

        # Validate and clean scores
        output = {}
        for key, gemini_key in [
            ("employee_growth", "employee_growth"),
            ("business_fit", "business_fit"),
            ("market_sentiment", "market_sentiment"),
        ]:
            metric = result.get(gemini_key, {})
            if metric and metric.get("score") is not None:
                try:
                    score = float(metric["score"])
                    score = max(0.0, min(1.0, score))
                    output[key] = {
                        "score": round(score, 3),
                        "value": metric.get("value", ""),
                        "explanation": metric.get("explanation", ""),
                    }
                except (ValueError, TypeError):
                    pass

        logger.info(f"[Scoring] Gemini assessed {len(output)} qualitative metrics for '{name}'")
        return output

    except json.JSONDecodeError as e:
        logger.error(f"[Scoring] Gemini JSON parse failed: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"[Scoring] Gemini qualitative scoring failed: {e}")
        return {"error": str(e)}
