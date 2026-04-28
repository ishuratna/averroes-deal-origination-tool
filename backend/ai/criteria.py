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


# ── Hard filter: Geography ──────────────────────────────────────────────────

def _is_uk_ireland(company: dict) -> bool:
    """Check if the company is based in UK or Ireland using all available location fields."""
    thesis = AverroesPhilosophy.THESIS
    geo_targets = [g.lower() for g in thesis["geography"]]
    # Also accept common country codes / abbreviations
    geo_targets += ["uk", "gb", "ie", "england", "scotland", "wales", "northern ireland"]

    # Check every location-related field
    fields_to_check = [
        company.get("region", ""),
        company.get("hq_country", ""),
        company.get("hq_city", ""),
        company.get("hq_location", ""),
    ]
    combined = " ".join(str(f) for f in fields_to_check).lower()

    return any(kw in combined for kw in geo_targets)


# ── Hard filter: Technology ──────────────────────────────────────────────────

def _is_tech_related(company: dict) -> bool:
    """Check if the company is technology or technology-related."""
    thesis = AverroesPhilosophy.THESIS
    tech_kw = thesis["tech_keywords"]

    # Check sector, description, keywords, verticals, industry group
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

def qualify_company(company: dict) -> Dict[str, any]:
    """
    Applies two hard filters to a company:
      1. Must be UK or Ireland based
      2. Must be technology or tech-related

    Returns dict with:
      - qualified: bool
      - status: 'Qualified' or 'Not a Fit'
      - is_uk_ireland: bool
      - is_tech: bool
      - reason: str (human-readable explanation)
    """
    uk_ire = _is_uk_ireland(company)
    tech = _is_tech_related(company)
    qualified = uk_ire and tech

    if qualified:
        reason = "UK/Ireland technology company — meets both hard filters."
    elif not uk_ire and not tech:
        reason = "Failed both filters: not UK/Ireland, not tech-related."
    elif not uk_ire:
        reason = "Not UK/Ireland based."
    else:
        reason = "Not a technology-related company."

    return {
        "qualified": qualified,
        "status": "Qualified" if qualified else "Not a Fit",
        "is_uk_ireland": uk_ire,
        "is_tech": tech,
        "reason": reason,
    }


# ── Gemini-powered qualification (richer data = better filter accuracy) ──────

def qualify_company_with_gemini(company: dict) -> Dict[str, any]:
    """
    Uses Gemini to determine geography and tech-relatedness when local data
    is sparse. Falls back to keyword qualification if Gemini unavailable.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")

    if not api_key or not GEMINI_AVAILABLE:
        logger.info(f"Qualifying '{company.get('name')}' via keyword filters...")
        return qualify_company(company)

    logger.info(f"Qualifying '{company.get('name')}' via Gemini...")

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = _build_qualification_prompt(company)

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )

        result = json.loads(response.text)

        # Extract Gemini's assessment
        is_uk_ire = bool(result.get("is_uk_ireland", False))
        is_tech = bool(result.get("is_tech_related", False))
        qualified = is_uk_ire and is_tech

        # Pull in any enriched fields Gemini found
        for field in ["sector", "region", "ownership", "description"]:
            if field in result and result[field] and not company.get(field):
                company[field] = result[field]

        reason = result.get("reason", "")
        if not reason:
            if qualified:
                reason = "UK/Ireland technology company — meets both hard filters."
            elif not is_uk_ire and not is_tech:
                reason = "Failed both filters: not UK/Ireland, not tech-related."
            elif not is_uk_ire:
                reason = "Not UK/Ireland based."
            else:
                reason = "Not a technology-related company."

        return {
            "qualified": qualified,
            "status": "Qualified" if qualified else "Not a Fit",
            "is_uk_ireland": is_uk_ire,
            "is_tech": is_tech,
            "reason": reason,
        }

    except Exception as e:
        logger.warning(f"Gemini qualification failed for {company.get('name')}: {e}. Falling back to keywords.")
        return qualify_company(company)


def _build_qualification_prompt(company: dict) -> str:
    """Build a Gemini prompt that checks the two hard filters."""
    company_json = json.dumps(company, default=str)
    return f"""
    You are an expert Private Equity analyst for Averroes Capital.
    Determine TWO things about this company:

    COMPANY DATA:
    {company_json}

    QUESTION 1 — GEOGRAPHY:
    Is this company headquartered in the UK or Ireland?
    Look at region, hq_country, hq_city, hq_location, or infer from any available data.
    UK includes: England, Scotland, Wales, Northern Ireland, and all UK cities.
    Ireland includes: Republic of Ireland and all Irish cities.

    QUESTION 2 — TECHNOLOGY:
    Is this a technology or technology-related company?
    This includes: software, SaaS, platforms, AI/ML, data/analytics, cloud,
    fintech, healthtech, edtech, cybersecurity, IT services, digital services,
    tech-enabled services, industrial tech, IoT, robotics, etc.
    Traditional industries (pure manufacturing, retail, hospitality, construction)
    that don't use tech as a core product do NOT count.

    RETURN FORMAT — JSON only:
    {{
        "is_uk_ireland": true or false,
        "is_tech_related": true or false,
        "reason": "One sentence explaining your assessment",
        "sector": "string — the company's sector if you can determine it",
        "region": "string — the company's HQ region/country if you can determine it",
        "ownership": "string — ownership structure if you can determine it",
        "description": "string — one sentence company summary if the existing one is empty"
    }}
    """


# ── Legacy compatibility ─────────────────────────────────────────────────────
# These are kept so existing code that imports them doesn't break.

def evaluate_target(company: dict, philosophy: AverroesPhilosophy) -> float:
    """Legacy wrapper. Returns 1.0 for Qualified, 0.0 for Not a Fit."""
    result = qualify_company(company)
    return 1.0 if result["qualified"] else 0.0


def generate_analysis_prompt(company_name: str, web_data: str, philosophy: AverroesPhilosophy) -> str:
    """Legacy — kept for backward compatibility."""
    return _build_qualification_prompt({"name": company_name, "raw_data": web_data})
