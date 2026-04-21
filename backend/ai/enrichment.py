import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class EnrichmentAgent:
    """
    Secondary agent that performs deep-dive enrichment
    to find founders and contact details for high-conviction targets.
    """
    
    def enrich_founder_details(self, company_name: str) -> Dict[str, Optional[str]]:
        """
        Uses Gemini with Google Search grounding to discover founder details
        and the correct company website URL.
        """
        logger.info(f"Agents Hunting: Enriching founder details for '{company_name}'...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": ""}
            
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            client = genai.Client(api_key=api_key)
            
            prompt = f"""
            Research the tech company '{company_name}' and find:
            1. The company's official website URL
            2. The primary founder, co-founder, or current CEO
            3. Their LinkedIn profile URL (must be a direct individual profile link)
            4. Their most likely professional email address

            QUALITY GUIDELINES:
            - Website must be the company's main domain (e.g., https://company.com), not a LinkedIn or Crunchbase page
            - Focus on identifying REAL human names, not generic roles
            - LinkedIn URL must be a real individual profile link, not a company page
            - Email should follow standard patterns (firstname@company.com)
            - If you cannot find a field with confidence, return an empty string for that field

            PRIORITY FOR CONTACT:
            1. Founder
            2. Co-Founder  
            3. CEO / Managing Director

            Return ONLY valid JSON, no markdown, no explanation:
            {{"website": "https://company.com", "contact_name": "First Last", "contact_email": "name@company.com", "linkedin_url": "https://www.linkedin.com/in/..."}}
            """
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[Tool(google_search=GoogleSearch())]
                )
            )
            
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
            result = json.loads(text)
            return {
                "website": result.get("website", ""),
                "contact_name": result.get("contact_name", ""),
                "contact_email": result.get("contact_email", ""),
                "linkedin_url": result.get("linkedin_url", "")
            }
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": ""}
