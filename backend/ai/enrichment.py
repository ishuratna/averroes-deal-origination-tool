import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class EnrichmentAgent:
    """
    Secondary agent that performs deep-dive enrichment
    to find founders, contact details, and a company summary for targets.
    """

    def enrich_founder_details(self, company_name: str) -> Dict[str, Optional[str]]:
        """
        Uses Gemini with Google Search grounding to discover founder details,
        the correct company website URL, and a 1-2 paragraph company summary.
        """
        logger.info(f"Agents Hunting: Enriching founder details for '{company_name}'...")

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": "", "description": ""}

        try:
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

            client = genai.Client(api_key=api_key)

            prompt = f"""
            Research the tech company '{company_name}' and find:
            1. The company's official website URL
            2. The primary founder, co-founder, or current CEO
            3. Their LinkedIn profile URL (must be a direct individual profile link)
            4. Their professional email address, ONLY if it is actually published somewhere
            5. A detailed company summary (1-2 paragraphs)

            QUALITY GUIDELINES:
            - Website must be the company's main domain (e.g., https://company.com), not a LinkedIn or Crunchbase page
            - Focus on identifying REAL human names, not generic roles
            - LinkedIn URL must be a real individual profile link, not a company page
            - EMAIL RULE (strict, non-negotiable): only return an email address you actually
              FOUND in a source: the company website, press coverage, official filings,
              conference or directory listings. NEVER construct, infer, or guess an address
              from name patterns such as firstname@company.com or f.lastname@company.com.
              A guessed email is worse than no email: it bounces or reaches the wrong
              person. If no published address exists, return "" for contact_email.
              A published generic company address (hello@, info@) is acceptable as a last
              resort ONLY if it appears on their site.
            - If you cannot find a field with confidence, return an empty string for that field

            COMPANY SUMMARY GUIDELINES:
            - Write 1-2 tight paragraphs about what the company does
            - Include: what they sell/build, who their customers are, key differentiators
            - If available, mention: founding year, HQ location, notable clients, funding stage, team size
            - Be factual and specific — no filler phrases like "innovative" or "cutting-edge"
            - This summary will be used for investment screening, so focus on business model and market position

            PRIORITY FOR CONTACT:
            1. Founder
            2. Co-Founder
            3. CEO / Managing Director

            Return ONLY valid JSON, no markdown, no explanation:
            {{"website": "https://company.com", "contact_name": "First Last", "contact_email": "name@company.com", "linkedin_url": "https://www.linkedin.com/in/...", "description": "1-2 paragraph company summary"}}
            """

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=GenerateContentConfig(
                    tools=[Tool(google_search=GoogleSearch())]
                )
            )

            text = response.text
            if not text:
                logger.warning(f"Gemini returned empty response for {company_name}")
                return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": "", "description": ""}

            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            return {
                "website": result.get("website", ""),
                "contact_name": result.get("contact_name", ""),
                "contact_email": result.get("contact_email", ""),
                "linkedin_url": result.get("linkedin_url", ""),
                "description": result.get("description", ""),
            }
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": "", "description": ""}
