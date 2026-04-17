import os
import json
import logging
from pydantic import BaseModel
from typing import Optional, List, ClassVar

logger = logging.getLogger(__name__)

# --- Load Gemini SDK if available ---
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-generativeai not installed. Falling back to keyword scoring.")


class AverroesPhilosophy(BaseModel):
    """
    Standardizes the investment thesis for Averroes Capital.
    Updated for RELAXED sourcing net.
    """
    THESIS: ClassVar[dict] = {
        "geography": ["UK", "Ireland", "United Kingdom", "Great Britain", "London", "Dublin"],
        "ownership": ["Founder-led", "Bootstrapped", "Angel-backed", "Family-owned", "Management-owned"],
        "rejections": ["VC-backed", "PE-backed", "Institutional majorities", "Series B+", "Venture Capital"],
        "focus": "B2B SaaS / Software / High-Margin Tech-Enabled Services / Industrial Tech",
        "target_ebitda": "£1M - £10M (Revenue approx £2M - £20M)",
        "scoring_weights": {
            "b2b_tech_alignment": 0.40,  # Slightly lower weight to allow for broader tech-enabled
            "ownership_fit": 0.35,       # Higher emphasis on proprietary/founder ownership
            "geography_focus": 0.25      # Clean UK/Ireland focus
        }
    }


def _keyword_score(company: dict, philosophy: AverroesPhilosophy) -> float:
    """
    Deterministic keyword-based fallback scorer.
    Used when GEMINI_API_KEY is not set.
    """
    score = 0.0
    thesis = philosophy.THESIS

    # 1. B2B Tech Alignment (+0.45)
    content = (company.get('sector', '') + " " + company.get('description', '')).lower()
    is_b2b = any(kw in content for kw in ["b2b", "enterprise", "industrial", "corporate", "professional services", "business", "logistics"])
    is_saas = any(kw in content for kw in ["software", "saas", "platform", "cloud", "paas", "software-as-a-service"])
    is_tech_enabled = any(kw in content for kw in ["tech-enabled", "automation", "digital", "ai", "data", "it services", "proprietary tech", "analytics"])

    if is_b2b:
        if is_saas:
            score += thesis["scoring_weights"]["b2b_tech_alignment"]
        elif is_tech_enabled:
            score += thesis["scoring_weights"]["b2b_tech_alignment"] * 0.9
        else:
            score += 0.15

    # 2. Ownership Check (+0.3)
    ownership = company.get('ownership', '').lower()
    is_bootstrapped = any(o.lower() in ownership for o in thesis["ownership"])
    is_rejected = any(r.lower() in ownership for r in thesis["rejections"])

    if is_bootstrapped and not is_rejected:
        score += thesis["scoring_weights"]["ownership_fit"]
    elif is_rejected:
        score -= 0.6

    # 3. Geography & Growth Check (+0.25)
    region = company.get('region', 'Unknown').lower()
    geo_keywords = [r.lower() for r in thesis["geography"]] + ["uk", "united kingdom", "ireland", "dublin", "london"]
    is_target_region = any(kw in region or region in kw for kw in geo_keywords)

    if is_target_region:
        score += thesis["scoring_weights"]["geography_focus"]
        # RELAXED MODE: If it's UK/Ireland B2B, ensure it hits at least 0.4 to enter the universe
        if is_b2b:
            score = max(score, 0.45)
            
    growth_signals = company.get('growth_signals', False)
    if growth_signals:
        score += 0.10

    return max(0.0, min(1.0, round(score, 2)))


def _gemini_score(company: dict, philosophy: AverroesPhilosophy, api_key: str) -> float:
    """
    Uses Gemini 1.5 Pro to evaluate a company against the Averroes thesis.
    Returns a float match_score between 0.0 and 1.0.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = generate_analysis_prompt(company.get("name", "Unknown"), json.dumps(company), philosophy)

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )

        result = json.loads(response.text)
        score = float(result.get("match_score", 0.0))
        
        # Also pull in enriched fields from Gemini response if available
        for field in ["sector", "region", "ownership", "description", "status"]:
            if field in result and not company.get(field):
                company[field] = result[field]

        return max(0.0, min(1.0, round(score, 2)))

    except Exception as e:
        logger.warning(f"Gemini scoring failed for {company.get('name')}: {e}. Falling back to keyword score.")
        return _keyword_score(company, philosophy)


def evaluate_target(company: dict, philosophy: AverroesPhilosophy) -> float:
    """
    Unified scoring engine. Uses Gemini if API key is set, else falls back to keyword scorer.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")

    if api_key and GEMINI_AVAILABLE:
        logger.info(f"Scoring '{company.get('name')}' via Gemini 1.5 Pro...")
        return _gemini_score(company, philosophy, api_key)
    else:
        logger.info(f"Scoring '{company.get('name')}' via keyword fallback...")
        return _keyword_score(company, philosophy)


def generate_analysis_prompt(company_name: str, web_data: str, philosophy: AverroesPhilosophy) -> str:
    thesis = philosophy.THESIS
    return f"""
    You are an expert Private Equity Investment Analyst for Averroes Capital.
    Analyze the following company data and return a structured JSON evaluation.

    COMPANY DATA:
    {web_data}

    AVERROES INVESTMENT PHILOSOPHY:
    - Focus: {thesis['focus']}
    - Geography: {', '.join(thesis['geography'])}
    - Ownership: Strictly {', '.join(thesis['ownership'])}. REJECT any company with {', '.join(thesis['rejections'])} status.
    - Target EBITDA: {thesis['target_ebitda']}

    EVALUATION TASKS:
    1. Verify B2B orientation (is the primary customer a business, not a consumer?).
    2. Identify tech-enablement (do they use a platform, proprietary tech, or automation?).
    3. Ascertain geographic HQ (UK or European focus scores higher).
    4. Determine ownership structure (look for 'Self-funded', 'Family-owned', 'Bootstrapped').
    5. Check for growth signals (hiring, awards, fast-growing revenue).

    SCORING GUIDE:
    - 0.9 - 1.0: Perfect thesis fit (UK/EU B2B SaaS, bootstrapped, strong growth).
    - 0.7 - 0.89: Strong fit, minor gaps (e.g., correct sector but continental Europe only).
    - 0.4 - 0.69: Partial fit (B2B but not tech-enabled, or geography uncertain).
    - 0.0 - 0.39: Poor fit or disqualifier present (VC-backed, B2C, outside Europe).

    RETURN FORMAT: A single JSON object with these exact keys:
    {{
        "name": "{company_name}",
        "sector": "string",
        "region": "string",
        "ownership": "string",
        "growth_signals": true or false,
        "match_score": float between 0.0 and 1.0,
        "status": "Qualified" or "Rejected",
        "description": "string — one sentence summary of why this score was given"
    }}
    """
