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
        Uses Gemini with Google Search grounding to discover the founder's name,
        company email, and LinkedIn profile URL.
        """
        logger.info(f"Agents Hunting: Enriching founder details for '{company_name}'...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"contact_name": "", "contact_email": "", "linkedin_url": ""}
            
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            client = genai.Client(api_key=api_key)
            
            prompt = f"""
            Identify the primary founder or current CEO for the tech company '{company_name}'.
            
            INVESTIGATIVE STEPS:
            1. Search for '{company_name}' on LinkedIn and corporate websites.
            2. Identify the lead professional (Founder, Co-Founder, or Managing Director).
            3. Find their LinkedIn URL (MUST be a direct individual profile link).
            4. Construct the most likely professional email (e.g., firstname@company.com).
            
            QUALITY GUIDELINES:
            - Focus on identifying REAL human names, not generic roles.
            - If you cannot identify a real person, return empty strings.
            - LinkedIn URL must be a real individual profile link, not a company page.
            
            PRIORITY:
            1. Founder
            2. Co-Founder
            3. CEO / Managing Director
            
            Return ONLY valid JSON, no markdown, no explanation:
            {{"contact_name": "First Last", "contact_email": "name@company.com", "linkedin_url": "https://www.linkedin.com/in/..."}}
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
                "contact_name": result.get("contact_name", ""),
                "contact_email": result.get("contact_email", ""),
                "linkedin_url": result.get("linkedin_url", "")
            }
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {"contact_name": "", "contact_email": "", "linkedin_url": ""}
