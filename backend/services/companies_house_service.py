"""
Companies House Financial Extraction Service

Uses Gemini with Google Search grounding to:
1. Find the company's official Companies House record
2. Verify it's the correct company
3. Extract latest 3 years of financial data from public filings

No API key needed — all data is public and found via Google Search.
"""

import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def extract_ch_financials(company_name: str, sector: str = "", region: str = "", description: str = "") -> Dict:
    """
    Search Companies House for a UK/Ireland company and extract financial data.

    Returns dict with:
      - ch_company_number: str (Companies House number)
      - ch_official_name: str (official registered name)
      - ch_status: str (Active, Dissolved, etc.)
      - ch_incorporated_date: str
      - ch_sic_codes: str (SIC codes description)
      - revenue_y1, revenue_y2, revenue_y3: float or None (most recent 3 years, £)
      - revenue_y1_date, revenue_y2_date, revenue_y3_date: str (filing period end dates)
      - profit_y1, profit_y2, profit_y3: float or None
      - total_assets_y1: float or None (most recent year)
      - net_assets_y1: float or None
      - cash_y1: float or None
      - employees_ch: int or None (from CH filings)
      - filing_type: str (e.g. "full", "micro-entity", "small", "medium", "dormant")
      - ch_match_confidence: str ("high", "medium", "low")
      - error: str or None
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)

        # Step 1: Find the company on Companies House and extract financials
        context_parts = [f"Company name: {company_name}"]
        if sector:
            context_parts.append(f"Sector: {sector}")
        if region:
            context_parts.append(f"Region: {region}")
        if description:
            context_parts.append(f"Description: {description[:200]}")
        context = "\n".join(context_parts)

        prompt = f"""
You are a UK financial research analyst. I need you to find a specific company's
official Companies House record and extract their latest filed financial data.

COMPANY TO RESEARCH:
{context}

STEP 1 — FIND THE COMPANY ON COMPANIES HOUSE:
- Search for this company on Companies House (find.and" update.company-information.service.gov.uk)
- Find the correct company — match by name, sector, and any available description
- Get the company number, official registered name, incorporation date, SIC codes, and status
- If multiple matches exist, pick the one that best matches the sector/description

STEP 2 — EXTRACT FINANCIAL DATA FROM LATEST FILINGS:
- Look up the company's latest filed accounts on Companies House
- Extract revenue/turnover, profit/loss before tax, total assets, net assets, and cash for up to 3 years
- Revenue = "Turnover" or "Revenue" in UK accounts terminology
- If the company files micro-entity or abbreviated accounts, they may not include turnover — note this
- Financial figures should be in GBP (£). Convert if shown in other currencies.
- For each year, note the financial period end date (e.g., "2024-03-31")

STEP 3 — VERIFY:
- Make sure the Companies House record matches the company we're looking for
- Rate your confidence: "high" if name and sector clearly match, "medium" if plausible, "low" if uncertain

IMPORTANT:
- Many small UK companies file abbreviated or micro-entity accounts that do NOT include revenue/turnover.
  In that case, still return whatever financial data IS available (total assets, net assets, etc.) and set
  revenue fields to null.
- If the company is Irish, search the CRO (Companies Registration Office Ireland) instead.
- All monetary values should be in raw numbers (e.g., 5000000 for £5M), NOT formatted strings.
- Return null for any field you cannot find — do NOT make up numbers.

Return ONLY valid JSON, no markdown, no explanation:
{{
    "ch_company_number": "string or null",
    "ch_official_name": "string or null",
    "ch_status": "Active or Dissolved or null",
    "ch_incorporated_date": "YYYY-MM-DD or null",
    "ch_sic_codes": "string description of SIC codes or null",
    "revenue_y1": null or number in GBP,
    "revenue_y1_date": "YYYY-MM-DD period end or null",
    "revenue_y2": null or number in GBP,
    "revenue_y2_date": "YYYY-MM-DD period end or null",
    "revenue_y3": null or number in GBP,
    "revenue_y3_date": "YYYY-MM-DD period end or null",
    "profit_y1": null or number in GBP,
    "profit_y1_date": "YYYY-MM-DD or null",
    "profit_y2": null or number in GBP,
    "profit_y3": null or number in GBP,
    "total_assets_y1": null or number in GBP,
    "net_assets_y1": null or number in GBP,
    "cash_y1": null or number in GBP,
    "employees_ch": null or integer,
    "filing_type": "full or small or micro-entity or medium or abbreviated or dormant or null",
    "ch_match_confidence": "high or medium or low",
    "notes": "Any important context about the filings, e.g. 'Files micro-entity accounts, turnover not disclosed'"
}}
"""

        logger.info(f"Searching Companies House for '{company_name}'...")

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())]
            )
        )

        text = response.text
        if not text:
            logger.warning(f"Gemini returned empty response for CH lookup: {company_name}")
            return {"error": "Empty response from AI"}

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        result["error"] = None
        logger.info(f"CH lookup for '{company_name}': found '{result.get('ch_official_name')}' "
                     f"(#{result.get('ch_company_number')}, confidence={result.get('ch_match_confidence')})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"CH financials JSON parse failed for {company_name}: {e}")
        return {"error": f"Failed to parse AI response: {str(e)}"}
    except Exception as e:
        logger.error(f"CH financials extraction failed for {company_name}: {e}")
        return {"error": str(e)}
