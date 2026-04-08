from pydantic import BaseModel
from typing import Optional, List

class AverroesPhilosophy(BaseModel):
    """
    Standard Averroes Capital Investment Thesis - Founder-Led B2B SaaS.
    """
    sectors: List[str] = ["B2B SaaS", "Software as a Service"]
    
    # Geographic Focus
    targeted_regions: List[str] = ["UK", "Netherlands", "Germany", "France", "Nordics", "Ireland"]
    
    # Ownership Focus
    required_ownership: List[str] = ["Founder-Led", "Bootstrapped", "Angel-backed"]
    rejected_ownership: List[str] = ["PE-backed", "VC Sponsor", "Series A", "Series B"]

def evaluate_target(metrics: dict, philosophy: AverroesPhilosophy) -> float:
    """
    Calculates a weighted match score based on known financials and ownership signals.
    Metric logic:
    +0.4 strictly B2B SaaS in UK/EU
    +0.3 Confirmed "No VC" (Bootstrapped/Angel)
    +0.3 Growth signals (hiring or traffic)
    """
    score = 0.0
    
    # 1. Geography & Sector Alignment (+0.4)
    sector = metrics.get('sector', '').lower()
    region = metrics.get('region', '').lower()
    
    is_b2b_saas = 'b2b' in sector or 'saas' in sector
    is_target_region = any(r.lower() in region for r in philosophy.targeted_regions)
    
    if is_b2b_saas and (is_target_region or region == ''): # Treat empty region as possible match until confirmed
        score += 0.4
        
    # 2. Ownership Check (+0.3)
    ownership = metrics.get('ownership', '').lower()
    is_vc_backed = any(r.lower() in ownership for r in philosophy.rejected_ownership)
    is_bootstrapped = any(r.lower() in ownership for r in philosophy.required_ownership)
    
    if is_bootstrapped and not is_vc_backed:
        score += 0.3
        
    # 3. Growth Signals Check (+0.3)
    growth_signals = metrics.get('growth_signals', False)
    if growth_signals:
        score += 0.3
        
    return round(score, 2)

# --- GEMINI PROMPT GENERATOR ---
def generate_analysis_prompt(company_name: str, web_data: str, philosophy: AverroesPhilosophy) -> str:
    return f"""
    You are an expert Private Equity Investment Analyst for Averroes Capital. 
    Analyze the following company data scraped from {company_name}'s website, Acquire.com, or LinkedIn:
    
    {web_data}
    
    Our Strict Investment Philosophy is as follows:
    - Target Sectors: B2B SaaS ONLY.
    - Geography: {', '.join(philosophy.targeted_regions)}
    - Ownership: Strictly Founder-led, Bootstrapped, or Angel-backed. REJECT any company with a PE/VC sponsor or Series A/B funding.
    
    TASK:
    1. Verify the company is strictly B2B SaaS.
    2. Ascertain their geographic HQ.
    3. Determine the ownership structure. Look for keywords like "Self-funded", "Founder-owned", or absence of institutional VC news.
    4. Check for growth signals (Hiring, growing web traffic, expanding feature sets).
    5. Calculate a 'Match Score' from 0.0 to 1.0 based on our criteria.
    
    FORMAT: JSON containing keys: "name", "sector", "region", "ownership", "growth_signals" (boolean), "match_score" (float), "status" (Qualified or Rejected), "description".
    """
