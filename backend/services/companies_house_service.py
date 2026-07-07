"""
Companies House Financial Extraction Service

Robust 4-step pipeline:
1. Search CH API for the company → verify correct match
2. Get filing history → find latest accounts filing
3. Download the actual accounts PDF from Companies House
4. Feed PDF to Gemini → extract structured financial data from the real document

Requires: COMPANIES_HOUSE_API_KEY (free — register at developer.company-information.service.gov.uk)
          GEMINI_API_KEY (for PDF parsing)
"""

import os
import io
import json
import logging
import base64
import re as _re
import requests
from difflib import SequenceMatcher as _SequenceMatcher
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Companies House API base URLs
CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_DOC_API = "https://document-api.company-information.service.gov.uk"


def _ch_auth() -> Tuple[str, str]:
    """Return HTTP Basic auth tuple for CH API (key as username, blank password)."""
    key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    return (key, "")


# ── Step 1: Search for company ──────────────────────────────────────────────

def _search_company(company_name: str) -> List[dict]:
    """Search Companies House for a company by name. Returns top results."""
    auth = _ch_auth()
    if not auth[0]:
        return []

    try:
        resp = requests.get(
            f"{CH_API_BASE}/search/companies",
            params={"q": company_name, "items_per_page": 10},
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except Exception as e:
        logger.error(f"CH search failed for '{company_name}': {e}")
        return []


# Words that carry NO identity information — legal suffixes and generic
# industry terms. Overlap on these must never count as a name match.
# (Fix for false positives like "Vrinsoft Technology Inc" matching
#  "ALL EAT APP NETWORK TECHNOLOGY INCORPORATED LTD" via the word "technology".)
_GENERIC_NAME_TOKENS = {
    "ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "co", "corp",
    "corporation", "company", "group", "holdings", "holding", "uk", "gb",
    "the", "and", "of", "a", "an", "&",
    "technologies", "technology", "tech", "software", "solutions", "solution",
    "services", "service", "systems", "system", "digital", "global",
    "international", "app", "apps", "network", "networks", "labs", "lab",
    "studio", "studios", "consulting", "consultancy", "ventures", "partners",
    "media", "online", "platform", "platforms", "cloud", "it", "europe", "london",
}


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    name = _re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    return _re.sub(r"\s+", " ", name).strip()


def _core_name(name: str) -> str:
    """Normalized name with generic/legal tokens removed (order preserved)."""
    return " ".join(t for t in _normalize_name(name).split() if t not in _GENERIC_NAME_TOKENS)


def _name_gate(company_name: str, title: str) -> Optional[Tuple[str, int]]:
    """
    HARD GATE on name similarity. Returns (gate_level, name_score) if the two
    names plausibly refer to the same company, else None (candidate discarded —
    no amount of status/SIC/address bonus can revive it).
    """
    n1, n2 = _normalize_name(company_name), _normalize_name(title)
    if not n1 or not n2:
        return None
    if n1 == n2:
        return ("exact", 100)

    c1, c2 = _core_name(company_name), _core_name(title)

    # Same distinctive words (e.g. "vrinsoft" == "vrinsoft") ignoring suffixes
    if c1 and c1 == c2:
        return ("exact-core", 90)

    # One distinctive name contains the other (e.g. "monzo" in "monzo bank")
    if c1 and c2 and len(min(c1, c2, key=len)) >= 4 and (c1 in c2 or c2 in c1):
        return ("contains", 75)

    # Fuzzy: near-identical distinctive names (typos, spacing)
    if c1 and c2:
        ratio = _SequenceMatcher(None, c1, c2).ratio()
        if ratio >= 0.85:
            return ("fuzzy", 65)
        # Partial: at least one shared DISTINCTIVE word + strong overall similarity
        shared = set(c1.split()) & set(c2.split())
        if shared and ratio >= 0.6:
            return ("partial", 50)

    return None


def _pick_best_match(
    results: List[dict],
    company_name: str,
    sector: str = "",
    description: str = "",
) -> Optional[dict]:
    """
    Pick the best matching company from CH search results.

    STRINGENT: name similarity is a hard gate — candidates whose names don't
    plausibly refer to the same company are discarded outright. Status/SIC/
    address only rank candidates that already passed the gate.
    """
    if not results:
        return None

    context_lower = f"{sector} {description}".lower()

    scored = []
    for item in results:
        title = item.get("title") or ""

        gate = _name_gate(company_name, title)
        if gate is None:
            continue  # name doesn't match — discard regardless of other signals
        gate_level, score = gate

        # Small tie-breaker bonuses (can never rescue a failed name gate)
        status = (item.get("company_status") or "").lower()
        if status == "active":
            score += 10
        elif status == "dissolved":
            score -= 15

        sic_codes = item.get("sic_codes") or []
        snippet = (item.get("snippet") or "").lower()
        if any(kw in snippet or kw in " ".join(sic_codes) for kw in ["software", "tech", "data", "digital", "saas", "platform", "cloud"]):
            if any(kw in context_lower for kw in ["software", "tech", "data", "digital", "saas", "platform", "cloud"]):
                score += 5

        address = item.get("address", {})
        country = (address.get("country") or "").lower()
        if "united kingdom" in country or "england" in country or "wales" in country or "scotland" in country:
            score += 5

        item["_match_gate"] = gate_level
        scored.append((score, item))

    if not scored:
        logger.warning(f"[CH] No candidate passed the name gate for '{company_name}' — refusing to match.")
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    logger.info(f"[CH] Best match for '{company_name}': '{best.get('title')}' (gate={best.get('_match_gate')}, score={best_score})")
    return best


# ── Step 2: Get company profile + filing history ─────────────────────────────

def _get_company_profile(company_number: str) -> Optional[dict]:
    """Fetch the full company profile from CH API."""
    auth = _ch_auth()
    try:
        resp = requests.get(
            f"{CH_API_BASE}/company/{company_number}",
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"CH profile fetch failed for {company_number}: {e}")
        return None


def _get_accounts_filings(company_number: str, max_items: int = 10) -> List[dict]:
    """Fetch recent accounts filings from CH filing history."""
    auth = _ch_auth()
    try:
        resp = requests.get(
            f"{CH_API_BASE}/company/{company_number}/filing-history",
            params={"category": "accounts", "items_per_page": max_items},
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        # Filter to just accounts
        accounts = [
            item for item in items
            if item.get("category") == "accounts"
        ]
        return accounts
    except Exception as e:
        logger.error(f"CH filing history failed for {company_number}: {e}")
        return []


# ── Step 3: Download the accounts PDF ────────────────────────────────────────

def _download_accounts_pdf(filing: dict) -> Optional[bytes]:
    """
    Download the actual PDF document for an accounts filing.
    The filing dict contains links.document_metadata which gives us the doc ID.
    """
    auth = _ch_auth()

    # Get document metadata link
    links = filing.get("links", {})
    doc_meta_url = links.get("document_metadata")

    if not doc_meta_url:
        # Try constructing from self link
        self_link = links.get("self", "")
        # Filing self link looks like /company/12345678/filing-history/MzM1NTY4...
        # We need the transaction ID which is the last part
        logger.warning(f"No document_metadata link in filing: {filing.get('description', 'unknown')}")
        return None

    try:
        # Ensure we have the full URL
        if doc_meta_url.startswith("/"):
            doc_meta_url = f"{CH_API_BASE}{doc_meta_url}"

        # Step 1: Get document metadata
        meta_resp = requests.get(doc_meta_url, auth=auth, timeout=15)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        # The metadata contains links.document which points to the actual document content
        doc_links = meta.get("links", {})
        doc_url = doc_links.get("document")

        if not doc_url:
            logger.warning(f"No document download link in metadata: {doc_meta_url}")
            return None

        # Ensure full URL
        if doc_url.startswith("/"):
            doc_url = f"{CH_DOC_API}{doc_url}"

        # Step 2: Download the PDF (request PDF content type)
        pdf_resp = requests.get(
            doc_url,
            auth=auth,
            headers={"Accept": "application/pdf"},
            timeout=30,
            allow_redirects=True,
        )
        pdf_resp.raise_for_status()

        content_type = pdf_resp.headers.get("Content-Type", "")
        if "pdf" in content_type or len(pdf_resp.content) > 1000:
            logger.info(f"Downloaded accounts PDF: {len(pdf_resp.content)} bytes")
            return pdf_resp.content
        else:
            logger.warning(f"Document response was not PDF (content-type: {content_type})")
            return None

    except Exception as e:
        logger.error(f"PDF download failed: {e}")
        return None


# ── Step 4: Parse PDF with Gemini ─────────────────────────────────────────────

def _parse_accounts_pdf_with_gemini(
    pdf_bytes: bytes,
    company_name: str,
    company_number: str,
    filing_date: str = "",
) -> Dict:
    """
    Send the actual accounts PDF to Gemini and extract structured financial data.
    This is the key step — we're parsing the REAL document, not asking Gemini to guess.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return {"error": "GEMINI_API_KEY not set"}

    try:
        import google.generativeai as genai

        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Encode PDF as base64 for Gemini
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        prompt = f"""You are a UK chartered accountant. I am giving you the ACTUAL filed accounts PDF
for company "{company_name}" (Companies House #{company_number}, filing date: {filing_date}).

Extract ALL available financial data from this document. This is a real UK statutory filing.

EXTRACT THE FOLLOWING (set to null if not present in the document):

1. REVENUE / TURNOVER — Look for "Turnover", "Revenue", "Total revenue", "Net revenue"
   - Many micro-entity and abbreviated accounts do NOT include turnover — that's OK, set to null
   - If a Profit & Loss / Income Statement exists, turnover is usually the top line

2. PROFIT / LOSS BEFORE TAX — Look for "Profit/(loss) before taxation", "Profit before tax"

3. TOTAL ASSETS — Look in the Balance Sheet for "Total assets"

4. NET ASSETS — "Net assets", "Total net assets", or "Total assets less current liabilities" minus long-term liabilities

5. CASH — "Cash at bank and in hand", "Cash and cash equivalents"

6. EMPLOYEES — "Average number of employees", "Employee information"

7. FILING TYPE — Is this: "full" accounts, "small" company accounts, "micro-entity" accounts,
   "abbreviated" accounts, "medium" accounts, "filleted" accounts, or "dormant" accounts?

8. PERIOD DATES — What financial period does this cover? (period start and end dates)

9. COMPARATIVE FIGURES — Many UK accounts show current year AND prior year side by side.
   Extract BOTH if available. Label clearly which is current vs prior year.

IMPORTANT RULES:
- Only extract numbers that ACTUALLY appear in the document. Do NOT estimate or calculate.
- All monetary values in raw GBP (e.g., 5000000 for £5M). Do NOT format as strings.
- If the accounts are micro-entity or abbreviated with no P&L, just extract the Balance Sheet figures.
- Negative numbers should be negative (e.g., -250000 for a £250K loss).
- Look for notes to accounts that might contain additional financial data.

Return ONLY valid JSON:
{{
    "revenue_current": null or number,
    "revenue_prior": null or number,
    "profit_current": null or number,
    "profit_prior": null or number,
    "total_assets_current": null or number,
    "total_assets_prior": null or number,
    "net_assets_current": null or number,
    "net_assets_prior": null or number,
    "cash_current": null or number,
    "cash_prior": null or number,
    "employees": null or integer,
    "period_end_current": "YYYY-MM-DD or null",
    "period_end_prior": "YYYY-MM-DD or null",
    "filing_type": "full or small or micro-entity or abbreviated or medium or filleted or dormant",
    "currency": "GBP or other",
    "notes": "Brief summary of what's in the accounts, e.g. 'Micro-entity accounts, Balance Sheet only, no P&L'"
}}"""

        # Send PDF as file data to Gemini
        response = model.generate_content(
            [
                {"mime_type": "application/pdf", "data": pdf_b64},
                prompt,
            ],
            generation_config={"response_mime_type": "application/json"},
        )

        text = response.text
        if not text:
            return {"error": "Gemini returned empty response for PDF parsing"}

        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        logger.info(f"Gemini extracted financials from PDF for {company_name}: "
                     f"revenue={result.get('revenue_current')}, assets={result.get('total_assets_current')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Gemini PDF parse JSON error for {company_name}: {e}")
        return {"error": f"Failed to parse Gemini response: {str(e)}"}
    except Exception as e:
        logger.error(f"Gemini PDF parsing failed for {company_name}: {e}")
        return {"error": str(e)}


# ── Main orchestrator ─────────────────────────────────────────────────────────

def extract_ch_financials(
    company_name: str,
    sector: str = "",
    region: str = "",
    description: str = "",
    gcs_handler=None,
) -> Dict:
    """
    Full pipeline: Search CH → Find company → Download accounts PDF → Parse with Gemini.

    Returns structured dict with CH company info + extracted financial data.
    """
    ch_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    if not ch_key:
        return {"error": "COMPANIES_HOUSE_API_KEY not set — register free at developer.company-information.service.gov.uk"}
    if not gemini_key:
        return {"error": "GEMINI_API_KEY not set"}

    # ── Step 1: Search Companies House ──
    logger.info(f"[CH] Step 1: Searching Companies House for '{company_name}'...")
    results = _search_company(company_name)
    if not results:
        return {"error": f"No results found on Companies House for '{company_name}'"}

    # Pick best match
    best = _pick_best_match(results, company_name, sector, description)
    if not best:
        return {"error": f"No confident match found on Companies House for '{company_name}'"}

    company_number = best.get("company_number", "")
    official_name = best.get("title", "")
    logger.info(f"[CH] Step 1 result: '{official_name}' (#{company_number})")

    # Match confidence derives from the name gate the candidate passed
    gate_level = best.get("_match_gate", "")
    if gate_level in ("exact", "exact-core"):
        match_confidence = "high"
    elif gate_level in ("contains", "fuzzy"):
        match_confidence = "medium"
    else:  # "partial"
        match_confidence = "low"

    # ── Step 2: Get company profile ──
    logger.info(f"[CH] Step 2: Fetching profile for #{company_number}...")
    profile = _get_company_profile(company_number)

    ch_status = "Unknown"
    ch_incorporated = None
    ch_sic_codes = None

    if profile:
        ch_status = profile.get("company_status", "unknown").replace("_", " ").title()
        date_of_creation = profile.get("date_of_creation")
        ch_incorporated = date_of_creation
        sic = profile.get("sic_codes", [])
        ch_sic_codes = ", ".join(sic) if sic else None

    # ── Step 3: Get filing history (accounts only) ──
    logger.info(f"[CH] Step 3: Fetching accounts filing history for #{company_number}...")
    filings = _get_accounts_filings(company_number, max_items=5)

    if not filings:
        logger.warning(f"[CH] No accounts filings found for #{company_number}")
        return {
            "ch_company_number": company_number,
            "ch_official_name": official_name,
            "ch_status": ch_status,
            "ch_incorporated_date": ch_incorporated,
            "ch_sic_codes": ch_sic_codes,
            "ch_match_confidence": match_confidence,
            "filing_type": None,
            "notes": "No accounts filings found on Companies House",
            "error": None,
        }

    # Try to download PDFs for up to the latest 3 filings
    logger.info(f"[CH] Found {len(filings)} accounts filings. Attempting PDF downloads...")

    all_financials = []
    filing_type = None

    saved_pdf_path = None

    for i, filing in enumerate(filings[:3]):
        filing_date = filing.get("date", "")
        filing_desc = filing.get("description", "")
        logger.info(f"[CH] Step 4: Downloading PDF for filing {i+1}: {filing_desc} ({filing_date})...")

        # Detect filing type from description
        desc_lower = filing_desc.lower()
        if "micro" in desc_lower:
            filing_type = filing_type or "micro-entity"
        elif "small" in desc_lower:
            filing_type = filing_type or "small"
        elif "medium" in desc_lower:
            filing_type = filing_type or "medium"
        elif "abbreviated" in desc_lower:
            filing_type = filing_type or "abbreviated"
        elif "dormant" in desc_lower:
            filing_type = filing_type or "dormant"
        elif "full" in desc_lower or "group" in desc_lower:
            filing_type = filing_type or "full"
        elif "filleted" in desc_lower:
            filing_type = filing_type or "filleted"

        pdf_bytes = _download_accounts_pdf(filing)
        if not pdf_bytes:
            logger.warning(f"[CH] Could not download PDF for filing {i+1}")
            continue

        # Save first PDF to GCS for later review
        if gcs_handler and pdf_bytes and not saved_pdf_path:
            try:
                safe_name = company_name.replace(" ", "_").replace("/", "_")[:50]
                gcs_path = f"ch-filings/{company_number}/{safe_name}_{filing_date}.pdf"
                if gcs_handler.storage_client:
                    bucket = gcs_handler.storage_client.bucket(gcs_handler.bucket_name)
                    blob = bucket.blob(gcs_path)
                    blob.upload_from_string(data=pdf_bytes, content_type="application/pdf")
                    saved_pdf_path = gcs_path
                    logger.info(f"[CH] Saved accounts PDF to GCS: {gcs_path}")
            except Exception as e:
                logger.warning(f"[CH] Could not save PDF to GCS: {e}")

        # Parse the PDF with Gemini
        parsed = _parse_accounts_pdf_with_gemini(
            pdf_bytes, company_name, company_number, filing_date
        )

        if parsed.get("error"):
            logger.warning(f"[CH] Gemini parse failed for filing {i+1}: {parsed['error']}")
            continue

        parsed["_filing_date"] = filing_date
        all_financials.append(parsed)
        logger.info(f"[CH] Successfully parsed filing {i+1} ({filing_date})")

    # ── Step 5: Assemble final result ──
    result = {
        "ch_company_number": company_number,
        "ch_official_name": official_name,
        "ch_status": ch_status,
        "ch_incorporated_date": ch_incorporated,
        "ch_sic_codes": ch_sic_codes,
        "ch_match_confidence": match_confidence,
        "filing_type": filing_type,
        "error": None,
        "ch_pdf_path": saved_pdf_path,
    }

    if not all_financials:
        result["notes"] = "Accounts filings exist but could not download/parse PDFs"
        return result

    # Map parsed financials into y1 (most recent), y2, y3
    # Each parsed filing may have current + prior year data
    year_data = []  # List of (period_end_date, {revenue, profit, assets, ...})

    for parsed in all_financials:
        # Current year from this filing
        period_end = parsed.get("period_end_current")
        if period_end:
            year_data.append((period_end, {
                "revenue": parsed.get("revenue_current"),
                "profit": parsed.get("profit_current"),
                "total_assets": parsed.get("total_assets_current"),
                "net_assets": parsed.get("net_assets_current"),
                "cash": parsed.get("cash_current"),
            }))

        # Prior year from this filing (comparative figures)
        period_end_prior = parsed.get("period_end_prior")
        if period_end_prior:
            year_data.append((period_end_prior, {
                "revenue": parsed.get("revenue_prior"),
                "profit": parsed.get("profit_prior"),
                "total_assets": parsed.get("total_assets_prior"),
                "net_assets": parsed.get("net_assets_prior"),
                "cash": parsed.get("cash_prior"),
            }))

    # Deduplicate by period end date (prefer the current-year extraction)
    seen_dates = {}
    for date, data in year_data:
        if date and date not in seen_dates:
            seen_dates[date] = data

    # Sort by date descending (most recent first)
    sorted_years = sorted(seen_dates.items(), key=lambda x: x[0], reverse=True)

    # Map to y1 (most recent), y2, y3
    for i, (date, data) in enumerate(sorted_years[:3]):
        suffix = f"y{i+1}"
        result[f"revenue_{suffix}"] = data.get("revenue")
        result[f"revenue_{suffix}_date"] = date
        result[f"profit_{suffix}"] = data.get("profit")
        if i == 0:
            result["profit_y1_date"] = date
            result["total_assets_y1"] = data.get("total_assets")
            result["net_assets_y1"] = data.get("net_assets")
            result["cash_y1"] = data.get("cash")

    # Employees from most recent filing
    if all_financials:
        result["employees_ch"] = all_financials[0].get("employees")

    # Filing type from most recent
    if all_financials and all_financials[0].get("filing_type"):
        result["filing_type"] = all_financials[0]["filing_type"]

    # Notes
    notes_parts = []
    if all_financials[0].get("notes"):
        notes_parts.append(all_financials[0]["notes"])
    notes_parts.append(f"Extracted from {len(all_financials)} filing PDF(s), covering {len(sorted_years)} financial periods")
    result["notes"] = ". ".join(notes_parts)

    logger.info(f"[CH] Complete for '{company_name}': {len(sorted_years)} years of data extracted from actual filings")
    return result
