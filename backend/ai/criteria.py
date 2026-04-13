from pydantic import BaseModel
from typing import Optional, List

class AverroesPhilosophy(BaseModel):
    """
    Standardizes the investment thesis for Averroes Capital.
    """
    THESIS = {
        "geography": ["United Kingdom", "Ireland", "Netherlands", "Germany", "France", "Nordics"],
        "ownership": ["Founder-led", "Bootstrapped", "Angel-backed"],
        "rejections": ["VC-backed", "PE-backed", "Institutional majorities"],
        "focus": "B2B Tech-Enabled / Software / Digital Services",
        "target_ebitda": "Low-Mid Market (Profitable or Breakeven)",
        "scoring_weights": {
            "b2b_tech_alignment": 0.4,   # Is it B2B? Is it Tech-enabled?
            "ownership_fit": 0.3,        # No VC/PE backing
            "growth_signal": 0.3         # Hiring, traffic, or ranking list
        }
    }

def evaluate_target(company: dict, philosophy: AverroesPhilosophy) -> float:
    """
    A unified scoring engine using the philosophy tokens.
    """
    score = 0.0
    thesis = philosophy.THESIS
    
    # 1. B2B Tech Alignment (+0.4)
    content = (company.get('sector', '') + " " + company.get('description', '')).lower()
    is_b2b = "b2b" in content
    is_tech = any(kw in content for kw in ["software", "saas", "tech", "platform", "digital", "ai", "automation", "it services"])
    
    if is_b2b and is_tech:
        score += thesis["scoring_weights"]["b2b_tech_alignment"]
    elif is_b2b or is_tech:
        score += 0.2
        
    # 2. Ownership Check (+0.3)
    ownership = company.get('ownership', '').lower()
    is_bootstrapped = any(o.lower() in ownership for o in thesis["ownership"])
    is_rejected = any(r.lower() in ownership for r in thesis["rejections"])
    
    if is_bootstrapped and not is_rejected:
        score += thesis["scoring_weights"]["ownership_fit"]
    elif is_rejected:
        score -= 0.5 # Penalty for institutional backing
        
    # 3. Geography & Growth Check (+0.3)
    region = company.get('region', 'Unknown').lower()
    is_target_region = any(r.lower() in region for r in thesis["geography"])
    has_growth = company.get('growth_signals', False)
    
    if is_target_region:
        score += 0.15
    if has_growth:
        score += 0.15
             
    return max(0.0, min(1.0, round(score, 2)))

# --- GEMINI PROMPT GENERATOR ---
def generate_analysis_prompt(company_name: str, web_data: str, philosophy: AverroesPhilosophy) -> str:
    thesis = philosophy.THESIS
    return f"""
    You are an expert Private Equity Investment Analyst for Averroes Capital. 
    Analyze the following company data:
    
    {web_data}
    
    Our Investment Philosophy is as follows:
    - Focus: {thesis['focus']}
    - Geography: {', '.join(thesis['geography'])}
    - Ownership: Strictly {', '.join(thesis['ownership'])}. 
    - REJECT: Any company with {', '.join(thesis['rejections'])} status.
    
    TASK:
    1. Verify B2B orientation. 
    2. Identify 'Tech-enablement' (Do they use a platform, proprietary tech, or automation to deliver service?).
    3. Ascertain geographic HQ.
    4. Determine ownership structure (Look for 'Self-funded', 'Family-owned', etc.).
    5. Check for growth signals (Hiring, traffic, industry awards).
    
    FORMAT: JSON containing keys: "name", "sector", "region", "ownership", "growth_signals" (boolean), "match_score" (float), "status" (Qualified or Rejected), "description".
    """
