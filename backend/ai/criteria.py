from pydantic import BaseModel
from typing import Optional, List

class AverroesPhilosophy(BaseModel):
    """
    Standard Averroes Capital Investment Thesis.
    Focus on B2B SaaS, Service-Led Tech, and High-Retention Software.
    """
    sectors: List[str] = ["SaaS", "Vertical Software", "B2B Tech", "Service-Led Tech"]
    min_ebitda: float = 2.0  # $2M EBITDA Floor
    growth_yoy: float = 0.15 # 15% YoY Growth Floor
    rule_of_40_target: float = 0.40 # Target 40% (Growth + EBITDA Margin)
    retention_floor: float = 0.85 # Min 85% Net Revenue Retention
    
    # Geographic Focus
    regions: List[str] = ["UK", "Europe", "English-speaking Global"]

def evaluate_target(metrics: dict, philosophy: AverroesPhilosophy) -> float:
    """
    Calculates a weighted match score based on known financials.
    """
    score = 0
    weight = 0
    
    # 1. Sector Alignment (High Weight)
    if any(s.lower() in metrics.get('sector', '').lower() for s in philosophy.sectors):
        score += 1.0 * 0.4
    weight += 0.4
    
    # 2. EBITDA Check
    if metrics.get('ebitda', 0) >= philosophy.min_ebitda:
        score += 1.0 * 0.2
    elif metrics.get('ebitda', 0) > 0:
        score += 0.5 * 0.2
    weight += 0.2
    
    # 3. Rule of 40 Check
    growth = metrics.get('growth', 0)
    margin = metrics.get('margin', 0)
    if (growth + margin) >= philosophy.rule_of_40_target:
        score += 1.0 * 0.3
    weight += 0.3
    
    # 4. Location Context
    if any(r.lower() in metrics.get('region', '').lower() for r in philosophy.regions):
        score += 1.0 * 0.1
    weight += 0.1
    
    return round(score / weight, 2) if weight > 0 else 0.0

# --- GEMINI PROMPT GENERATOR ---
def generate_analysis_prompt(web_data: str, philosophy: AverroesPhilosophy) -> str:
    return f"""
    You are an expert Private Equity Investment Analyst for Averroes Capital. 
    Analyze the following company data scraped from their website and external sources:
    
    {web_data}
    
    Our Investment Philosophy is as follows:
    - Target Sectors: {', '.join(philosophy.sectors)}
    - Financial Floor: ${philosophy.min_ebitda}M EBITDA
    - Growth Profile: Minimum {philosophy.growth_yoy*100}% YoY
    - Efficiency: Aim for Rule of 40 (Growth + Margin >= 0.4)
    
    TASK:
    1. Extract company name, core business model, and estimated scale.
    2. Estimate financials if possible (or indicate confidence).
    3. Identify key executives and decision-makers.
    4. Calculate a 'Match Score' from 0-100% against our philosophy.
    5. PROVIDE CONTACT DETAILS (Email/Phone) if detected in the data.
    
    FORMAT: JSON
    """
