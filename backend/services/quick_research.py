"""
Quick Tools — Company Deep Research.

WHAT THIS IS: a front door to the EXISTING SmartFill workflow for a company
that is not in the universe yet. You give it a company name (typed) or a
document (PDF/DOCX/CSV/XLSX), it works out which company that is, seeds one
normal row in `targets`, and then hands over to the untouched
`smartfill_company()` in main.py.

WHY IT IS BUILT THIS WAY (explicit requirement):
  * This module NEVER re-implements any part of SmartFill. It only
    identifies the company and creates the row; main.py then calls the same
    SmartFill function the Universe buttons call. So every future change to
    SmartFill carries through here automatically, and nothing here can
    change SmartFill's behaviour for existing flows.
  * The seeded row is an ordinary universe row with source = 'Quick
    Research', so it shows on the Universe/Pipeline pages and in the normal
    company card, and every existing rule (merge-never-overwrite, watch job,
    analytics ledger) applies to it unchanged.

Identification is ONE ungrounded AI call (cheap) and is source-stated only:
if the document does not name the company, we say so instead of guessing.
"""
import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_IDENTIFY_PROMPT = """You are reading material about ONE company for an investment team.
Extract ONLY what the text actually states. Never invent or infer beyond it.

Return strict JSON:
{{
  "name": "the company's common trading name, no legal suffix",
  "legal_name": "registered/legal name if stated, else empty",
  "registration_number": "company registration number if stated, else empty",
  "website": "primary website if stated, else empty",
  "sector": "short sector/industry label if stated, else empty",
  "region": "country or region of HQ if stated, else empty",
  "hq_city": "city if stated, else empty",
  "description": "2-4 sentences describing what the company does, using only the text",
  "confidence": "high | medium | low",
  "notes": "anything ambiguous, e.g. multiple companies mentioned"
}}

If the material does not clearly identify a single company, set name to "" and
explain in notes.

MATERIAL:
{material}
"""


def _clean_json(text: str) -> Dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def identify_from_text(material: str) -> Dict:
    """Company identity from pasted text / a typed description. One ungrounded call."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=_IDENTIFY_PROMPT.format(material=material[:60000]))
    try:
        return _clean_json(resp.text or "")
    except Exception as e:
        return {"error": f"Could not parse identification: {e}"}


def identify_from_document(data: bytes, filename: str) -> Dict:
    """Company identity from an uploaded document.

    PDFs go to Gemini natively (same technique as Smart Upload). Text-ish and
    tabular files are read locally first, then identified as text.
    """
    lower = (filename or "").lower()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}

    if lower.endswith(".pdf"):
        if len(data) > 18 * 1024 * 1024:
            return {"error": "PDF too large (max 18MB)"}
        import base64
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                gt.Part.from_bytes(data=data, mime_type="application/pdf"),
                _IDENTIFY_PROMPT.format(material="(see the attached PDF)"),
            ])
        try:
            return _clean_json(resp.text or "")
        except Exception as e:
            return {"error": f"Could not parse identification: {e}"}

    text = ""
    if lower.endswith((".txt", ".md", ".csv", ".tsv")):
        text = data.decode("utf-8", errors="replace")
    elif lower.endswith((".xlsx", ".xls")):
        try:
            import pandas as pd
            import io as _io
            df = pd.read_excel(_io.BytesIO(data), nrows=60)
            text = df.to_csv(index=False)
        except Exception as e:
            return {"error": f"Could not read spreadsheet: {e}"}
    elif lower.endswith(".docx"):
        try:
            import io as _io, zipfile
            with zipfile.ZipFile(_io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", xml)
            text = re.sub(r"\s+", " ", text)
        except Exception as e:
            return {"error": f"Could not read .docx: {e}"}
    else:
        return {"error": "Unsupported file type. Use PDF, DOCX, TXT, MD, CSV or XLSX."}

    if not text.strip():
        return {"error": "No readable text found in the document."}
    return identify_from_text(text)


def seed_row(ident: Dict, source_note: str = "") -> Dict:
    """Build the universe row to seed before SmartFill runs.

    Only fields the identification actually stated. Status 'Uploaded' is the
    same starting point every ingested company uses, so downstream logic
    treats this row exactly like any other.
    """
    name = (ident.get("name") or "").strip()
    website = (ident.get("website") or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    row = {
        "name": name,
        "website": website,
        "sector": (ident.get("sector") or "").strip(),
        "region": (ident.get("region") or "").strip(),
        "hq_city": (ident.get("hq_city") or "").strip(),
        "registration_number": (ident.get("registration_number") or "").strip(),
        "legal_name": (ident.get("legal_name") or "").strip(),
        "description": (ident.get("description") or "").strip(),
        "source": "Quick Research",
        "status": "Uploaded",
        "match_score": 0.0,
        "financing_note": source_note[:400],
    }
    return {k: v for k, v in row.items() if v not in (None, "")}
