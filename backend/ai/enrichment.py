import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class EnrichmentAgent:
    """
    Simulates a secondary agent that performs deep-dive enrichment
    to find founders and contact details for high-conviction targets.
    """
    
    def enrich_founder_details(self, company_name: str) -> Dict[str, Optional[str]]:
        """
        Uses Gemini to intelligently discover the founder's name, company email,
        and LinkedIn profile URL for the given B2B tech company.
        """
        logger.info(f"Agents Hunting: Enriching founder details for '{company_name}'...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"contact_name": "Pending Activation", "contact_email": "api_key_required@averroes.com", "linkedin_url": ""}
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            prompt = f"""
            Identify the primary founder or current CEO for the B2B tech company '{company_name}'.
            
            TASKS:
            1. Find their full name.
            2. Find their professional LinkedIn profile URL.
            3. Construct their business email (often first.last@company.com or first@company.com).
            
            RETURN FORMAT (STRICT JSON):
            {{
                "contact_name": "First Last",
                "contact_email": "email@domain.com",
                "linkedin_url": "https://www.linkedin.com/in/username/"
            }}
            
            Do not use markdown blocks. Return only the JSON. 
            If exact details are missing, provide your highest-confidence estimate based on public records.
            """
            
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            result = json.loads(response.text)
            return {
                "contact_name": result.get("contact_name", "Unknown Founder"),
                "contact_email": result.get("contact_email", ""),
                "linkedin_url": result.get("linkedin_url", "")
            }
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {"contact_name": "Data Missing", "contact_email": "", "linkedin_url": ""}
