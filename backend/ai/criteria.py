import os
import json
import logging
from pydantic import BaseModel
from typing import Optional, List, Dict, ClassVar

logger = logging.getLogger(__name__)

# --- Load Gemini SDK if available ---
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-generativeai not installed. Falling back to keyword qualification.")


class AverroesPhilosophy(BaseModel):
    """
    Standardizes the investment thesis for Averroes Capital.
    Two hard filters: UK/Ireland geography + technology-related company.
    Now reads from BQ config at runtime; this class provides legacy fallback.
    """
    THESIS: ClassVar[dict] = {
        "geography": ["UK", "Ireland", "United Kingdom", "Great Britain",
                       "London", "Dublin", "Edinburgh", "Manchester",
                       "Birmingham", "Belfast", "Glasgow", "Bristol",
                       "Leeds", "Cardiff", "Cork", "Galway", "Limerick"],
        "tech_keywords": [
            "software", "saas", "platform", "cloud", "paas", "iaas",
            "tech", "technology", "digital", "ai", "artificial intelligence",
            "machine learning", "data", "analytics", "automation",
            "cyber", "fintech", "healthtech", "edtech", "insurtech",
            "proptech", "regtech", "legaltech", "martech", "adtech",
            "devops", "api", "iot", "blockchain", "robotics",
            "it services", "managed services", "hosting",
            "e-commerce platform", "marketplace platform",
        ],
        "focus": "B2B SaaS / Software / High-Margin Tech-Enabled Services / Industrial Tech",
        "target_ebitda": "£1M - £10M (Revenue approx £2M - £20M)",
    }


# ── Module-level config cache ─────────────────────────────────────────────────
# criteria.py can be called with or without a BQ handler reference.
# When called from main.py, we pass the live criteria. When called standalone,
# we use the hardcoded THESIS as fallback.

_cached_criteria = None

def set_criteria_from_bq(criteria_dict: dict):
    """Called by main.py to inject the latest BQ criteria into this module."""
    global _cached_criteria
    _cached_criteria = criteria_dict

def _get_geo_targets(criteria: dict = None) -> list:
    """Get geography target list from criteria or fallback."""
    if criteria and "geography" in criteria:
        geo = criteria["geography"]
        regions = [r.lower() for r in geo.get("regions", [])]
        codes = [c.lower() for c in geo.get("country_codes", [])]
        return regions + codes
    # Fallback
    thesis = AverroesPhilosophy.THESIS
    return [g.lower() for g in thesis["geography"]] + ["uk", "gb", "ie", "england", "scotland", "wales", "northern ireland"]

def _get_tech_keywords(criteria: dict = None) -> list:
    """Get tech keywords from criteria or fallback."""
    if criteria and "industry" in criteria:
        return criteria["industry"].get("keywords", [])
    return AverroesPhilosophy.THESIS["tech_keywords"]


# ── Size buckets ────────────────────────────────────────────────────────────

SIZE_BUCKETS = {
    "Micro":  {"label": "Micro",  "max_revenue_m": 5,   "qualifies": True},
    "Small":  {"label": "Small",  "max_revenue_m": 15,  "qualifies": True},
    "Mid":    {"label": "Mid",    "max_revenue_m": 50,  "qualifies": True},
    "Large":  {"label": "Large",  "max_revenue_m": None, "qualifies": False},
}

def _get_size_config(criteria: dict = None) -> dict:
    """Get size filter config from criteria or defaults."""
    if criteria and "size" in criteria:
        return criteria["size"]
    return {
        "label": "Company Size (Revenue-based)",
        "description": "Micro (<£5M), Small (£5-15M), Mid (£15-50M) qualify. Large (>£50M) rejected.",
        "buckets": SIZE_BUCKETS,
        "max_revenue_m": 50,
    }


def size_company_rule_based(company: dict) -> Dict[str, any]:
    """
    Determine company size bucket from available financial data.
    Returns {bucket, confidence, reason} or None if insufficient data.
    """
    revenue = company.get("revenue_m")
    # Try parsing string revenue values
    if revenue is not None:
        try:
            revenue = float(revenue)
        except (ValueError, TypeError):
            revenue = None

    if revenue is not None and revenue > 0:
        if revenue < 5:
            return {"size_bucket": "Micro", "size_confidence": "high", "size_reason": f"Revenue £{revenue:.1f}M < £5M"}
        elif revenue < 15:
            return {"size_bucket": "Small", "size_confidence": "high", "size_reason": f"Revenue £{revenue:.1f}M (£5-15M range)"}
        elif revenue <= 50:
            return {"size_bucket": "Mid", "size_confidence": "high", "size_reason": f"Revenue £{revenue:.1f}M (£15-50M range)"}
        else:
            return {"size_bucket": "Large", "size_confidence": "high", "size_reason": f"Revenue £{revenue:.1f}M exceeds £50M threshold"}

    # No revenue data — return None so Gemini can assess
    return None


def is_size_qualified(size_bucket: str, criteria: dict = None) -> bool:
    """Check if a size bucket qualifies (Micro, Small, Mid = yes; Large = no)."""
    cfg = _get_size_config(criteria)
    buckets = cfg.get("buckets", SIZE_BUCKETS)
    bucket_info = buckets.get(size_bucket, {})
    return bucket_info.get("qualifies", False)


# ── Hard filter: Geography ──────────────────────────────────────────────────

def _is_uk_ireland(company: dict, criteria: dict = None) -> bool:
    """Check if the company is based in the target geography."""
    geo_targets = _get_geo_targets(criteria)

    fields_to_check = [
        company.get("region", ""),
        company.get("hq_country", ""),
        company.get("hq_city", ""),
        company.get("hq_location", ""),
    ]
    combined = " ".join(str(f) for f in fields_to_check).lower()

    return any(kw in combined for kw in geo_targets)


# ── Hard filter: Technology ──────────────────────────────────────────────────

def _is_tech_related(company: dict, criteria: dict = None) -> bool:
    """Check if the company is technology or technology-related."""
    tech_kw = _get_tech_keywords(criteria)

    fields_to_check = [
        company.get("sector", ""),
        company.get("description", ""),
        company.get("keywords", ""),
        company.get("verticals", ""),
        company.get("industry_group", ""),
        company.get("emerging_spaces", ""),
    ]
    combined = " ".join(str(f) for f in fields_to_check).lower()

    return any(kw in combined for kw in tech_kw)


# ── Main qualification function ──────────────────────────────────────────────

def qualify_company(company: dict, criteria: dict = None) -> Dict[str, any]:
    """
    Applies qualification filters from BQ config (or defaults):
      1. Geography filter
      2. Industry/tech filter
      3. Size filter (when size_bucket is available on the company)

    Returns dict with:
      - qualified: bool
      - status: 'Qualified' or 'Not a Fit'
      - is_uk_ireland: bool
      - is_tech: bool
      - size_bucket: str or None
      - size_qualified: bool or None
      - reason: str (human-readable explanation)
    """
    c = criteria or _cached_criteria
    uk_ire = _is_uk_ireland(company, c)
    tech = _is_tech_related(company, c)

    # Size check — use stored bucket if available, else try rule-based
    size_bucket = company.get("size_bucket")
    size_qualified = None
    if size_bucket:
        size_qualified = is_size_qualified(size_bucket, c)
    else:
        rule_result = size_company_rule_based(company)
        if rule_result:
            size_bucket = rule_result["size_bucket"]
            size_qualified = is_size_qualified(size_bucket, c)

    # Qualified = passes all available filters
    qualified = uk_ire and tech
    if size_qualified is not None:
        qualified = qualified and size_qualified

    geo_label = "target geography"
    if c and "geography" in c:
        geo_label = c["geography"].get("label", "target geography")
    industry_label = "technology-related"
    if c and "industry" in c:
        industry_label = c["industry"].get("label", "technology-related")

    # Build reason
    failures = []
    if not uk_ire:
        failures.append(f"not in {geo_label}")
    if not tech:
        failures.append(f"not {industry_label}")
    if size_qualified is False:
        failures.append(f"too large (size: {size_bucket})")

    if qualified:
        parts = [geo_label, industry_label]
        if size_qualified is True:
            parts.append(f"size OK ({size_bucket})")
        reason = f"Meets all filters: {', '.join(parts)}."
    else:
        reason = f"Failed: {', '.join(failures)}."

    return {
        "qualified": qualified,
        "status": "Qualified" if qualified else "Not a Fit",
        "is_uk_ireland": uk_ire,
        "is_tech": tech,
        "size_bucket": size_bucket,
        "size_qualified": size_qualified,
        "reason": reason,
    }


# ── Gemini-powered qualification (richer data = better filter accuracy) ──────

def qualify_company_with_gemini(company: dict, criteria: dict = None) -> Dict[str, any]:
    """
    Uses Gemini to determine geography, tech-relatedness, AND company size
    when local data is sparse. Falls back to keyword qualification if
    Gemini unavailable.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    c = criteria or _cached_criteria

    if not api_key or not GEMINI_AVAILABLE:
        logger.info(f"Qualifying '{company.get('name')}' via keyword filters...")
        return qualify_company(company, c)

    logger.info(f"Qualifying '{company.get('name')}' via Gemini...")

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = _build_qualification_prompt(company, c)

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )

        result = json.loads(response.text)

        # Extract Gemini's assessment
        is_uk_ire = bool(result.get("is_uk_ireland", False))
        is_tech = bool(result.get("is_tech_related", False))

        # Size assessment — prefer rule-based if revenue exists, else use Gemini's judgment
        rule_size = size_company_rule_based(company)
        if rule_size:
            size_bucket = rule_size["size_bucket"]
            size_confidence = rule_size["size_confidence"]
            size_reason = rule_size["size_reason"]
        else:
            # Use Gemini's AI judgment
            size_bucket = result.get("size_bucket", "Small")  # default conservative
            size_confidence = result.get("size_confidence", "low")
            size_reason = result.get("size_reason", "AI estimate based on available signals")

        size_ok = is_size_qualified(size_bucket, c)
        qualified = is_uk_ire and is_tech and size_ok

        # Pull in any enriched fields Gemini found
        for field in ["sector", "region", "ownership", "description"]:
            if field in result and result[field] and not company.get(field):
                company[field] = result[field]

        # Store size data on the company dict so it gets saved to BQ
        company["size_bucket"] = size_bucket

        # Build reason
        failures = []
        if not is_uk_ire:
            failures.append("not in target geography")
        if not is_tech:
            failures.append("not in target industry")
        if not size_ok:
            failures.append(f"too large ({size_bucket}, {size_reason})")

        reason = result.get("reason", "")
        if qualified:
            reason = f"Qualified: geography OK, tech OK, size {size_bucket} ({size_reason})."
        elif failures:
            reason = f"Not a Fit: {', '.join(failures)}."

        return {
            "qualified": qualified,
            "status": "Qualified" if qualified else "Not a Fit",
            "is_uk_ireland": is_uk_ire,
            "is_tech": is_tech,
            "size_bucket": size_bucket,
            "size_qualified": size_ok,
            "size_confidence": size_confidence,
            "size_reason": size_reason,
            "reason": reason,
        }

    except Exception as e:
        logger.warning(f"Gemini qualification failed for {company.get('name')}: {e}. Falling back to keywords.")
        return qualify_company(company, c)


def _build_qualification_prompt(company: dict, criteria: dict = None) -> str:
    """Build a Gemini prompt that checks the qualification filters."""
    c = criteria or _cached_criteria
    company_json = json.dumps(company, default=str)

    # Build dynamic geo list from criteria
    geo_regions = []
    if c and "geography" in c:
        geo_regions = c["geography"].get("regions", [])
    else:
        geo_regions = AverroesPhilosophy.THESIS["geography"]

    # Build dynamic tech list from criteria
    tech_kw = []
    if c and "industry" in c:
        tech_kw = c["industry"].get("keywords", [])
    else:
        tech_kw = AverroesPhilosophy.THESIS["tech_keywords"]

    geo_str = ", ".join(geo_regions)
    tech_str = ", ".join(tech_kw[:20])

    return f"""
    You are an expert Private Equity analyst for Averroes Capital.
    Determine THREE things about this company:

    COMPANY DATA:
    {company_json}

    QUESTION 1 — GEOGRAPHY:
    Is this company headquartered in one of these target regions?
    Target regions: {geo_str}
    Look at region, hq_country, hq_city, hq_location, or infer from any available data.

    QUESTION 2 — INDUSTRY:
    Is this a company in the target industry?
    Target keywords: {tech_str}
    Check sector, description, keywords, verticals, industry group.
    Traditional industries without tech as a core product do NOT count.

    QUESTION 3 — COMPANY SIZE:
    Estimate the company's size bucket based on ALL available signals.
    We are a lower-mid-market PE fund and only want companies with revenue under £50M.

    Size buckets:
    - "Micro": Revenue under £5M (typically <30 employees, early-stage, seed/angel funded)
    - "Small": Revenue £5M-£15M (typically 30-100 employees, Series A/B or bootstrapped profitable)
    - "Mid": Revenue £15M-£50M (typically 100-500 employees, growth stage, established product)
    - "Large": Revenue over £50M (typically 500+ employees, late-stage or enterprise — TOO BIG for us)

    Use these proxy signals to estimate when revenue is not available:
    - Employee count (strongest signal)
    - Total funding raised (high funding = likely larger)
    - Valuation estimates
    - Years since founding + growth trajectory
    - Company description / market positioning
    - Enterprise value if available

    Be conservative: if signals are mixed, lean toward a smaller bucket rather than larger.
    Only classify as "Large" if there are strong indicators the company exceeds £50M revenue.

    RETURN FORMAT — JSON only:
    {{
        "is_uk_ireland": true or false,
        "is_tech_related": true or false,
        "size_bucket": "Micro" or "Small" or "Mid" or "Large",
        "size_confidence": "high" or "medium" or "low",
        "size_reason": "Brief explanation of how you estimated the size, citing the signals you used",
        "reason": "One sentence overall assessment",
        "sector": "string — the company's sector if you can determine it",
        "region": "string — the company's HQ region/country if you can determine it",
        "ownership": "string — ownership structure if you can determine it",
        "description": "string — one sentence company summary if the existing one is empty"
    }}
    """


# ── Preview: count how many companies would qualify under given criteria ──────

def preview_criteria(universe: list, criteria: dict) -> dict:
    """
    Given the full universe and proposed criteria, returns counts of
    how many would qualify vs not, without changing anything.
    """
    qualified = 0
    rejected = 0
    sample_qualified = []
    sample_rejected = []

    for company in universe:
        result = qualify_company(company, criteria)
        if result["qualified"]:
            qualified += 1
            if len(sample_qualified) < 5:
                sample_qualified.append(company.get("name", "Unknown"))
        else:
            rejected += 1
            if len(sample_rejected) < 5:
                sample_rejected.append({"name": company.get("name", "Unknown"), "reason": result["reason"]})

    return {
        "qualified": qualified,
        "rejected": rejected,
        "total": qualified + rejected,
        "sample_qualified": sample_qualified,
        "sample_rejected": sample_rejected,
    }


# ── Legacy compatibility ─────────────────────────────────────────────────────

def evaluate_target(company: dict, philosophy: AverroesPhilosophy) -> float:
    """Legacy wrapper. Returns 1.0 for Qualified, 0.0 for Not a Fit."""
    result = qualify_company(company)
    return 1.0 if result["qualified"] else 0.0


def generate_analysis_prompt(company_name: str, web_data: str, philosophy: AverroesPhilosophy) -> str:
    """Legacy — kept for backward compatibility."""
    return _build_qualification_prompt({"name": company_name, "raw_data": web_data})
