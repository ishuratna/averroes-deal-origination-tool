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
            return {"contact_name": "", "contact_email": "", "linkedin_url": ""}
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            
            # Google Search grounding for SDK 0.8.x
            from google.generativeai.types import Tool as GenAITool
            search_tool = GenAITool(google_search=genai.types.GoogleSearch())
            
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                tools=[search_tool]
            )
            
            prompt = f"""
            Identify the primary founder or current CEO for the tech company '{company_name}'.
            
            INVESTIGATIVE STEPS:
            1. Scan recent LinkedIn activity and corporate websites for '{company_name}'.
            2. Identify the lead professional (Founder, Co-Founder, or Managing Director).
            3. Find their LinkedIn URL (MUST be a direct individual link).
            4. Verify or construct the most likely professional email (e.g., name@company.com).
            
            QUALITY GUIDELINES:
            - If you are unsure, provide the best lead found but mark reasonably.
            - Focus on identifying REAL human names, not generic roles.
            
            WHICH TO TARGET:
            - Priority 1: Founder
            - Priority 2: Co-Founder
            - Priority 3: CEO / Managing Director
            
            RETURN FORMAT (STRICT JSON):
            {{
                "contact_name": "First Last",
                "contact_email": "name@company.com",
                "linkedin_url": "https://www.linkedin.com/in/..."
            }}
            """
            
            response = model.generate_content(prompt)
            
            # Parse JSON from response, handling markdown code blocks
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
