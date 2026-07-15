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
            - EMAIL RULE: only return an email address you actually FOUND in a source.
              Preferred sources, in order: the company's own website, press releases,
              official filings, conference speaker pages, articles quoting the address.
              Contact-aggregator sites (RocketReach, ContactOut, Lusha, Apollo,
              SignalHire, Hunter, ZoomInfo and similar) ARE acceptable sources, but they
              are unverified: if the email comes from one of them, say so in email_source.
              What you must NEVER do is construct or infer an address yourself from name
              patterns such as firstname@company.com when no source shows it.
              If no source anywhere shows an address, return "" for contact_email.
              A published generic company address (hello@, info@) from their own site is
              acceptable as a last resort.
            - In email_source, state where you found the email (the site/page), or "" if
              no email was returned.
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
            {{"website": "https://company.com", "contact_name": "First Last", "contact_email": "name@company.com", "email_source": "where the email was found, or empty", "linkedin_url": "https://www.linkedin.com/in/...", "description": "1-2 paragraph company summary"}}
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
            out = {
                "website": result.get("website", ""),
                "contact_name": result.get("contact_name", ""),
                "contact_email": result.get("contact_email", ""),
                "email_source": result.get("email_source", ""),
                "linkedin_url": result.get("linkedin_url", ""),
                "description": result.get("description", ""),
            }
            # Retry ladder: first pass found no email — run ONE sharper search
            # (domain-string, GitHub commits, speaker pages, press footers).
            if not out["contact_email"]:
                retry = self._retry_email_search(client, company_name, out.get("website", ""), out.get("contact_name", ""))
                if retry.get("contact_email"):
                    out["contact_email"] = retry["contact_email"]
                    out["email_source"] = retry.get("email_source", "retry search")
            return out
        except Exception as e:
            logger.error(f"Enrichment search failed for {company_name}: {e}")
            return {"contact_name": "", "contact_email": "", "linkedin_url": "", "website": "", "description": ""}

    def _retry_email_search(self, client, company_name: str, website: str, contact_name: str) -> dict:
        """
        Second, sharper grounded pass used ONLY when the first found no email.
        Different tactics: search the literal @domain string (surfaces addresses
        inside PDFs, press releases and newswire footers), GitHub commits and
        profiles (tech founders leak emails in code), and conference speaker
        pages. Same rules: found-in-a-source only, never constructed.
        """
        try:
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            domain = ""
            if website:
                domain = website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            prompt = f"""
            Find a published email address for {contact_name or 'the founder/CEO'} of the company '{company_name}'
            {f'(website domain: {domain})' if domain else ''}.

            SEARCH TACTICS (try these specifically):
            1. Search the literal string "@{domain}" — email addresses appear inside press releases,
               PDF documents, newswire footers and event listings that mention the domain.
            2. Search GitHub for the domain or founder name — developers often expose their work
               email in commits, profiles and package files.
            3. Search conference and event speaker pages featuring {contact_name or 'the founder'}.
            4. Search press releases and media contact sections mentioning the company.

            RULES: return ONLY an address you actually saw in a source. NEVER construct or guess one
            from name patterns. If nothing is published anywhere, return an empty string.

            Return ONLY valid JSON: {{"contact_email": "...", "email_source": "where you saw it"}}
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())]),
            )
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                return {}
            result = json.loads(text[start:end + 1])
            email = (result.get("contact_email") or "").strip()
            if email:
                logger.info(f"[Enrichment] retry search found email for {company_name}: {email}")
            return {"contact_email": email, "email_source": result.get("email_source", "")}
        except Exception as e:
            logger.warning(f"[Enrichment] retry email search failed for {company_name}: {e}")
            return {}
