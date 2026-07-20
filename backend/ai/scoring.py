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


def _growth_pct_to_score(growth_pct: float) -> float:
    """Shared growth-% → 0-1 score curve (used for revenue AND employee growth)."""
    if growth_pct < -20:
        return 0.0
    if growth_pct < 0:
        return round(0.1 + (growth_pct + 20) / 200, 3)
    if growth_pct < 10:
        return round(0.2 + (growth_pct / 10) * 0.3, 3)
    if growth_pct < 25:
        return round(0.5 + ((growth_pct - 10) / 15) * 0.25, 3)
    if growth_pct < 50:
        return round(0.75 + ((growth_pct - 25) / 25) * 0.15, 3)
    return round(min(1.0, 0.9 + ((growth_pct - 50) / 100) * 0.1), 3)


def _compute_employee_growth_local(company: dict) -> Optional[Dict]:
    """
    Employee growth from stored figures (Inven uploads) — replaces the
    web-search judgement whenever real numbers exist. 1yr growth primary,
    3yr CAGR fallback.
    """
    for key, label in (("employee_growth_1yr_pct", "1yr"), ("employee_growth_3yr_pct", "3yr CAGR")):
        val = company.get(key)
        try:
            val = float(val) if val is not None else None
        except (ValueError, TypeError):
            val = None
        if val is not None:
            return {
                "score": _growth_pct_to_score(val),
                "value": round(val, 1),
                "explanation": f"Headcount {label} {val:+.1f}% (LinkedIn via Inven, local data)",
            }
    return None


def _compute_revenue_growth(company: dict) -> Optional[Dict]:
    """
    Compute revenue growth from CH filing data (y1 vs y2).
    Returns {score, value, explanation} or None.
    """
    rev_y1 = company.get("revenue_y1")
    rev_y2 = company.get("revenue_y2")

    if rev_y1 is None or rev_y2 is None:
        # Local fallback: a stored 3yr revenue CAGR (Inven uploads) is a real
        # growth figure — use the same curve instead of returning nothing
        cagr = company.get("revenue_cagr_3yr_pct")
        try:
            cagr = float(cagr) if cagr is not None else None
        except (ValueError, TypeError):
            cagr = None
        if cagr is not None:
            return {
                "score": _growth_pct_to_score(cagr),
                "value": round(cagr, 1),
                "explanation": f"Revenue 3yr CAGR {cagr:+.1f}% (Inven, local data)",
            }
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


def _safe_float(val) -> Optional[float]:
    """Parse a value to float, returning None on failure or non-positive."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


# Sector classification for margin/multiplier rules (SIC codes + text signals)
_SOFTWARE_SICS = ("6201", "6202", "6209", "6311", "6312", "5829")   # software dev, IT consultancy, data, publishing
_ASSET_HEAVY_SICS = ("64", "68")                                    # financial holding, property
_SOFTWARE_WORDS = ("saas", "software", "platform", "cloud", "app", "api", "data", "analytics", "ai", "cyber")
_SERVICES_WORDS = ("consult", "agency", "services", "outsourc", "recruit", "managed")


def _classify_sector(company: dict) -> str:
    """'software' | 'services' | 'asset_heavy' | 'unknown' — from SIC codes, then text."""
    sics = str(company.get("ch_sic_codes") or "")
    for code in [s.strip() for s in sics.split(",") if s.strip()]:
        if code.startswith(_ASSET_HEAVY_SICS):
            return "asset_heavy"
        if code.startswith(_SOFTWARE_SICS):
            return "software"
    text = f"{company.get('sector', '')} {company.get('keywords', '')} {company.get('description', '')}".lower()
    if any(w in text for w in _SOFTWARE_WORDS):
        return "software"
    if any(w in text for w in _SERVICES_WORDS):
        return "services"
    return "unknown"


# Business rules (agreed 2026-07): gross margins by sector for the GP→revenue rule
_GROSS_MARGINS = {"software": 0.80, "services": 0.50, "unknown": 0.70, "asset_heavy": 0.70}
# Asset-turnover multipliers by sector; asset_heavy = assets say nothing about revenue
_ASSET_MULTIPLIERS = {"software": 3.0, "services": 2.5, "unknown": 2.0}


def estimate_revenue_m(company: dict, allow_gemini: bool = True) -> Optional[Dict]:
    """
    Estimate annual revenue in £M. Business rules (agreed with IC, 2026-07):

      1. Filed/reported revenue wins outright (CH turnover, then PitchBook) → HIGH.
      2. Filed GROSS PROFIT overrides all inference: revenue = GP ÷ sector gross
         margin (software 80%, services 50%, unknown 70%) → MEDIUM (filed data +
         one assumption). Proxy median still computed as a sanity check and any
         >2x disagreement is noted in the provenance.
      3. Otherwise MEDIAN of local proxies:
           employees × £100K/head · assets × sector multiplier (software 3.0,
           services 2.5, unknown 2.0; skipped for holding/property companies) ·
           EBITDA × 6.5 · valuation ÷ 6 · last round × 3
         Cash rule: if cash > 60% of total assets (post-fundraise signature),
         assets minus cash is used instead.
         Confidence: MEDIUM if 2+ proxies within 2x, else LOW.
      4. Gemini + web search joins the median whenever local confidence is LOW
         (not only when nothing exists). Alone, it stays LOW.

    Returns {"rev_m", "source", "confidence", "is_estimate"} or None.
    """
    # ── Rule 1: actual revenue ──
    rev = _safe_float(company.get("revenue_y1"))
    if rev is not None:
        return {"rev_m": rev / 1_000_000, "source": "CH filings", "confidence": "high", "is_estimate": False}

    rev = _safe_float(company.get("revenue_m"))
    if rev is not None:
        return {"rev_m": rev, "source": "PitchBook", "confidence": "high", "is_estimate": False}

    sector = _classify_sector(company)

    # ── Rule 3 proxies (computed early so the GP rule can sanity-check against them) ──
    proxies = []  # (estimate_m, label)

    employees = _safe_float(company.get("employees")) or _safe_float(company.get("employees_ch"))
    if employees is not None:
        proxies.append((employees * 0.1, f"employees ({int(employees)} × £100K)"))

    assets = _safe_float(company.get("total_assets_y1"))
    if assets is not None and sector != "asset_heavy":
        assets_m = assets / 1_000_000
        cash = _safe_float(company.get("cash_y1"))
        label_extra = ""
        if cash is not None and cash > 0.6 * assets:
            assets_m = max(0.0, (assets - cash) / 1_000_000)  # post-fundraise cash pile
            label_extra = ", cash-adjusted"
        if assets_m > 0:
            mult = _ASSET_MULTIPLIERS.get(sector, 2.0)
            proxies.append((assets_m * mult, f"assets × {mult} ({sector}{label_extra})"))

    ebitda = _safe_float(company.get("estimated_ebitda"))
    if ebitda is not None:
        proxies.append((ebitda * 6.5, "EBITDA × 6.5"))

    valuation = _safe_float(company.get("valuation_estimate_m"))
    if valuation is not None:
        proxies.append((valuation / 6, "valuation ÷ 6"))

    last_round = _safe_float(company.get("last_financing_size_m"))
    if last_round is not None:
        proxies.append((last_round * 3, "last round × 3"))

    def _median(vals):
        vals = sorted(vals)
        n = len(vals)
        return vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2

    # ── Rule 2: filed gross profit overrides inference ──
    gp = _safe_float(company.get("gross_profit_y1"))
    if gp is not None:
        margin = _GROSS_MARGINS.get(sector, 0.70)
        gp_rev = (gp / 1_000_000) / margin
        source = f"gross profit ÷ {margin:.0%} margin ({sector}, filed accounts)"
        if proxies:
            proxy_median = _median([p[0] for p in proxies])
            if proxy_median > 0 and (gp_rev / proxy_median > 2 or proxy_median / gp_rev > 2):
                source += f"; note: proxy median disagrees (£{proxy_median:.1f}M)"
        logger.info(f"[Revenue] '{company.get('name')}' £{gp_rev:.1f}M from filed gross profit ({sector})")
        return {"rev_m": gp_rev, "source": source, "confidence": "medium", "is_estimate": True}

    # ── Rule 3: median of proxies ──
    if proxies:
        values = [p[0] for p in proxies]
        median = _median(values)
        svals = sorted(values)
        confidence = "medium" if (len(values) >= 2 and svals[-1] <= svals[0] * 2) else "low"

        # ── Rule 4: weak jury → Gemini joins the median ──
        if confidence == "low" and allow_gemini:
            g = _gemini_revenue_estimate(company)
            if g is not None:
                values.append(g)
                proxies.append((g, "Gemini + web search"))
                median = _median(values)
                # Re-check agreement including the web estimate
                close = sorted(values)
                if len(values) >= 2 and any(
                    close[i + 1] <= close[i] * 2 for i in range(len(close) - 1)
                ):
                    confidence = "medium"

        labels = ", ".join(p[1] for p in proxies)
        source = f"median of {len(proxies)} prox{'ies' if len(proxies) > 1 else 'y'} ({labels})"
        logger.info(f"[Revenue] '{company.get('name')}' estimated £{median:.1f}M ({confidence}) from: {labels}")
        return {"rev_m": median, "source": source, "confidence": confidence, "is_estimate": True}

    # ── Rule 4 fallback: nothing local at all ──
    if allow_gemini:
        gemini_estimate = _gemini_revenue_estimate(company)
        if gemini_estimate is not None:
            return {"rev_m": gemini_estimate, "source": "Gemini + web search", "confidence": "low", "is_estimate": True}

    return None


def _compute_revenue_size(company: dict) -> Optional[Dict]:
    """
    Score revenue size — how well it fits the Averroes sweet spot.
    Revenue comes from estimate_revenue_m (actual filed revenue, or
    median-of-proxies estimate, or Gemini as last resort).
    Returns {score, value, revenue_band, explanation, ...} or None.
    """
    est = estimate_revenue_m(company)
    if est is None:
        return None

    rev_m = est["rev_m"]
    source = est["source"]

    if rev_m is None or rev_m <= 0:
        return None

    # Score v3 — calibrated to the mandate: £15-40M equity cheques for
    # majority or significant-minority (25%+) stakes implies investable
    # equity values of ~£15-160M, i.e. revenue ~£5-40M at 4-6x. Core sweet
    # spot £8-20M (EV ~£40-90M where both deal structures work comfortably).
    # Under £2M  = 0.1-0.25 (far too early)
    # £2-5M     = 0.25-0.7 (approaching, rising)
    # £5-8M     = 0.7-1.0 (entering the band)
    # £8-20M    = 1.0 (core sweet spot — all equal)
    # £20-40M   = 1.0 declining to 0.5 (investable, needs minority structure)
    # Over £40M = 0.2 (beyond the cheque even at 25%)
    if rev_m < 2:
        score = 0.1 + (rev_m / 2) * 0.15
    elif rev_m < 5:
        score = 0.25 + ((rev_m - 2) / 3) * 0.45
    elif rev_m < 8:
        score = 0.7 + ((rev_m - 5) / 3) * 0.3
    elif rev_m <= 20:
        score = 1.0
    elif rev_m <= 40:
        score = 1.0 - ((rev_m - 20) / 20) * 0.5
    else:
        score = 0.2

    return {
        "score": round(score, 3),
        "value": round(rev_m, 2),
        "revenue_band": compute_revenue_band(rev_m),
        "is_estimate": est["is_estimate"],
        "confidence": est["confidence"],
        "source": source,
        "explanation": f"Revenue {'~' if est['is_estimate'] else ''}£{rev_m:.1f}M ({source}, {est['confidence']} confidence)",
    }


def compute_revenue_band(rev_m: Optional[float]) -> Optional[str]:
    """
    Classify revenue (£M) into the Averroes deal band.
    Target Band = £2.5-40M for £15-40M equity cheques at 25-100% stakes
    (core sweet spot £8-20M). Hard qualification cap: £40M.
    """
    if rev_m is None or rev_m <= 0:
        return None
    if rev_m < 2.5:
        return "Too Early"
    if rev_m <= 40:
        return "Target Band"
    return "Too Large"


def score_company(company: dict, skip_qualitative_if_too_large: bool = False) -> Dict:
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

    # ── Cost gate (bulk mode): Too Large band → skip the 3 web-search metrics.
    # The company stays Qualified but unscored; score on demand via the
    # individual SmartFill button. Saves ~3 grounded calls per Too Large company.
    band_now = details.get("revenue_size", {}).get("revenue_band")
    if skip_qualitative_if_too_large and band_now == "Too Large":
        logger.info(f"[Scoring] '{company_name}' is Too Large (bulk mode) — skipping web-search metrics")
        return {
            "averroes_fit_score": None,
            "score_employee_growth": None,
            "score_revenue_growth": scores.get("revenue_growth"),
            "score_revenue_size": scores.get("revenue_size"),
            "score_business_fit": None,
            "score_market_sentiment": None,
            "score_details": json.dumps(details),
            "revenue_band": band_now,
            "revenue_estimate_m": details.get("revenue_size", {}).get("value") if details.get("revenue_size", {}).get("is_estimate") else None,
            "revenue_source": details.get("revenue_size", {}).get("source"),
            "revenue_confidence": details.get("revenue_size", {}).get("confidence"),
            "metrics_available": len(scores),
            "error": "Skipped web-search scoring: Too Large band (bulk cost gate). Run SmartFill individually to score.",
        }

    # ── Metrics 1, 4, 5: Gemini assessment (employee growth, business fit, market sentiment) ──
    gemini_scores = _gemini_qualitative_scoring(company)
    if gemini_scores and not gemini_scores.get("error"):
        for metric in ["employee_growth", "business_fit", "market_sentiment"]:
            if gemini_scores.get(metric) is not None:
                scores[metric] = gemini_scores[metric]["score"]
                details[metric] = gemini_scores[metric]

    # Local data beats web judgement: stored employee-growth figures (Inven)
    # override the search-based score whenever they exist
    local_emp = _compute_employee_growth_local(company)
    if local_emp:
        scores["employee_growth"] = local_emp["score"]
        details["employee_growth"] = local_emp

    # Revenue band + estimate details (informational — from whatever revenue was found)
    rev_details = details.get("revenue_size", {})
    revenue_band = rev_details.get("revenue_band")
    revenue_estimate_m = rev_details.get("value") if rev_details.get("is_estimate") else None
    revenue_source = rev_details.get("source")
    revenue_confidence = rev_details.get("confidence")

    # ── Compute composite ──
    available = len(scores)
    logger.info(f"[Scoring] '{company_name}' has {available}/5 metrics: {list(scores.keys())}")
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
            "revenue_band": revenue_band,
            "revenue_estimate_m": revenue_estimate_m,
            "revenue_source": revenue_source,
            "revenue_confidence": revenue_confidence,
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
        "revenue_band": revenue_band,
        "revenue_estimate_m": revenue_estimate_m,
        "revenue_source": revenue_source,
        "revenue_confidence": revenue_confidence,
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

        from ai.investor_fill import _response_text, _extract_json
        text = _response_text(response)
        if not text:
            logger.warning("[Scoring] Gemini returned empty response")
            return None

        result = _extract_json(text)

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


def _gemini_revenue_estimate(company: dict) -> Optional[float]:
    """
    Last-resort revenue estimation using Gemini + Google Search.
    Returns estimated revenue in £ millions, or None.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return None

    name = company.get("name", "")
    if not name:
        return None

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)

        website = company.get("website", "")
        sector = company.get("sector", "")
        employees = company.get("employees") or company.get("employees_ch")
        ch_name = company.get("ch_official_name", "")
        description = company.get("description", "")[:200]
        total_assets = company.get("total_assets_y1")

        prompt = f"""Search the web for the company "{name}" (also known as "{ch_name or name}").
Website: {website or 'N/A'}
Sector: {sector or 'Unknown'}
Description: {description or 'N/A'}
Employees: {employees or 'Unknown'}
Total Assets: {'£' + str(round(total_assets/1e6, 2)) + 'M' if total_assets else 'Unknown'}

Estimate this company's ANNUAL REVENUE in GBP millions (£M).

Use any available information: company filings, press releases, industry reports,
employee count heuristics (e.g., £100-200K revenue per employee for SaaS),
asset-based estimates, or comparable companies.

Return ONLY a JSON object:
{{"revenue_estimate_m": <number or null>, "confidence": "high" | "medium" | "low", "reasoning": "<brief explanation>"}}

If you truly cannot estimate even a rough range, set revenue_estimate_m to null."""

        # NOTE: JSON mode (response_mime_type) cannot be combined with tools —
        # the API rejects it (400 INVALID_ARGUMENT) and this call silently
        # failed for every company. Grounded search + plain text, then parse.
        google_search_tool = Tool(google_search=GoogleSearch())
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(
                tools=[google_search_tool],
                temperature=0.2,
            ),
        )

        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"No JSON object in response: {text[:120]}")

        import json
        result = json.loads(text[start:end + 1])
        estimate = result.get("revenue_estimate_m")
        confidence = result.get("confidence", "low")
        reasoning = result.get("reasoning", "")

        if estimate is not None and estimate > 0:
            logger.info(f"[Scoring] Gemini revenue estimate for '{name}': £{estimate}M ({confidence}) — {reasoning}")
            return float(estimate)
        else:
            logger.info(f"[Scoring] Gemini could not estimate revenue for '{name}'")
            return None

    except Exception as e:
        logger.warning(f"[Scoring] Gemini revenue estimation failed for '{name}': {e}")
        return None
