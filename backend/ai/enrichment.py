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
        Uses Gemini to intelligently predict the founder's name and contact email 
        for the given B2B tech company.
        """
        logger.info(f"Agents Hunting: Enriching founder details for '{company_name}'...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("No GEMINI_API_KEY found, returning placeholder contact data.")
            return {"contact_name": "Pending Activation", "contact_email": "api_key_required@averroes.com"}
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            prompt = f"""
            You are a private equity researcher. Find the primary founder or CEO for a software company named '{company_name}'.
            Return only a strict JSON object with exactly these two keys:
            "contact_name": "First Last"
            "contact_email": "first.last@companydomain.com"
            
            If you don't know the exact person, make your absolute best guess of the CEO's name and business email based on your training data. Do not use markdown blocks.
            """
            
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            result = json.loads(response.text)
            return {
                "contact_name": result.get("contact_name", "Unknown Founder"),
                "contact_email": result.get("contact_email", "founders@company.com")
            }
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {
                "contact_name": "System Override Required", 
                "contact_email": "research@averroescapital.com"
            }
