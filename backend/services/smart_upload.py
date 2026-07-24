"""
Smart Upload: any CSV / Excel / PDF -> companies mapped into the master schema.

Design principle: AI designs the MAPPING, code executes it.
- Tabular files: the model sees ONLY headers + ~15 sample rows and returns a
  column mapping (source column -> target field + transform). Plain Python
  then applies that mapping to every row - one AI call per file regardless
  of size, and no value ever passes through the model (nothing hallucinated).
- PDFs: no columns to map, so the model reads the document natively and
  extracts the companies it can actually see (broker books / conference
  lists AND single-company teasers both handled).
- Columns with no matching field land in extra_data JSON - nothing lost,
  no schema sprawl.
Existing dedicated parsers (PitchBook, Inven, proprietary) remain the
fast paths; this handles everything else. No em dashes in this file.
"""
import io
import os
import json
import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USD_GBP = float(os.getenv("USD_GBP_RATE", "0.74"))
EUR_GBP = float(os.getenv("EUR_GBP_RATE", "0.85"))

# Whitelist of mappable target fields, with guidance the model sees.
# Monetary fields are in MILLIONS OF GBP unless stated otherwise.
TARGET_FIELDS = {
    "name": "Company name (REQUIRED)",
    "website": "Company website URL",
    "sector": "Sector / industry",
    "region": "Country or region",
    "hq_city": "HQ city",
    "hq_country": "HQ country",
    "hq_location": "Full HQ location string",
    "description": "What the company does",
    "employees": "Employee count (integer)",
    "year_founded": "Year founded (integer)",
    "keywords": "Keywords / tags",
    "verticals": "Verticals",
    "ownership": "Ownership type (Founder-led, VC-backed, PE-backed...)",
    "contact_name": "Founder/CEO or main contact person name",
    "contact_email": "Contact email address",
    "contact_title": "Contact job title",
    "linkedin_url": "PERSON LinkedIn profile URL",
    "company_linkedin": "COMPANY LinkedIn page URL",
    "registration_number": "Companies House / registration number",
    "legal_name": "Registered legal name",
    "also_known_as": "Alternative names",
    "revenue_m": "Latest revenue in GBP MILLIONS",
    "net_income_m": "Net income in GBP MILLIONS",
    "total_raised_m": "Total capital raised in GBP MILLIONS",
    "valuation_estimate_m": "Valuation in GBP MILLIONS",
    "enterprise_value_m": "Enterprise value in GBP MILLIONS",
    "revenue_growth_pct": "Revenue growth percent (number)",
    "ebitda_margin_pct": "EBITDA margin percent (number)",
    "revenue_cagr_3yr_pct": "3yr revenue CAGR percent (number)",
    "employee_growth_1yr_pct": "1yr employee growth percent (number)",
    "last_financing_date": "Last financing date",
    "last_financing_type": "Last financing round type",
    "investors_raw": "Investor names list",
    "current_owners": "Current owner names list",
    "competitors": "Competitor names",
    "financing_status": "Financing status",
}

_TRANSFORMS = ("none", "usd_m_to_gbp_m", "eur_m_to_gbp_m", "raw_to_m",
               "usd_raw_to_gbp_m", "k_to_m", "percent", "int", "year")


def _apply_transform(value, transform: str):
    """Deterministic value conversion. Returns None when not parseable."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("£", "").replace("$", "").replace("€", "").replace("%", "")
    if s == "" or s.lower() in ("nan", "n/a", "na", "-", "none", "null"):
        return None
    try:
        if transform == "none":
            v = str(value).strip()
            return v if v.lower() not in ("nan", "n/a", "none", "null", "-") else None
        x = float(s)
        if math.isnan(x):
            return None
        if transform == "usd_m_to_gbp_m":
            return round(x * USD_GBP, 3)
        if transform == "eur_m_to_gbp_m":
            return round(x * EUR_GBP, 3)
        if transform == "raw_to_m":
            return round(x / 1e6, 3)
        if transform == "usd_raw_to_gbp_m":
            return round(x * USD_GBP / 1e6, 3)
        if transform == "k_to_m":
            return round(x / 1000, 3)
        if transform == "percent":
            return round(x, 2)
        if transform in ("int", "year"):
            return int(x)
        return round(x, 3)
    except (ValueError, TypeError):
        return None


def read_tabular(data: bytes, filename: str) -> Tuple[Optional["object"], str]:
    """File bytes -> pandas DataFrame (largest sheet for Excel). (df, note)."""
    import pandas as pd
    fn = filename.lower()
    try:
        if fn.endswith(".csv") or fn.endswith(".tsv"):
            sep = "\t" if fn.endswith(".tsv") else None
            df = pd.read_csv(io.BytesIO(data), sep=sep, engine="python", dtype=str)
            return df, ""
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str)
        if not sheets:
            return None, "No sheets found"
        best = max(sheets, key=lambda k: sheets[k].shape[0])
        note = f"Used sheet '{best}' ({len(sheets)} sheets in file)" if len(sheets) > 1 else ""
        return sheets[best], note
    except Exception as e:
        return None, f"Could not parse file: {e}"


def analyze_mapping(headers: List[str], sample_rows: List[dict], filename: str) -> Dict:
    """ONE ungrounded call: headers + samples -> column mapping. {} on failure."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}
    fields_doc = "\n".join(f"- {k}: {v}" for k, v in TARGET_FIELDS.items())
    prompt = f"""You are mapping the columns of an uploaded dataset ("{filename}") of companies into a
fixed database schema. You see the headers and a few sample rows. Decide, for EVERY source
column, which target field it maps to, or "extra" if none fits, or "ignore" for junk
(row numbers, empty columns, internal IDs with no meaning).

TARGET FIELDS (monetary fields are GBP MILLIONS):
{fields_doc}

TRANSFORMS you may assign (code applies them, you only choose):
- none: keep as text
- usd_m_to_gbp_m: value is USD millions -> convert
- eur_m_to_gbp_m: value is EUR millions -> convert
- raw_to_m: value is GBP units (e.g. 12500000) -> divide to millions
- usd_raw_to_gbp_m: value is USD units -> convert and divide
- k_to_m: value is thousands -> divide by 1000
- percent: numeric percent
- int / year: integers

RULES:
- Exactly ONE column must map to "name". If no column looks like a company name,
  set "no_name_column": true.
- Read currencies/units from the header text and sample values (e.g. "Revenue ($M)",
  values like 12,500,000). When unsure between millions and units, look at magnitudes
  in the samples.
- Never map two source columns to the same target field.
- dataset_guess: one short line on what this dataset looks like (e.g. "Beauhurst export").

HEADERS: {json.dumps(headers)}

SAMPLE ROWS (first {len(sample_rows)}):
{json.dumps(sample_rows, default=str)[:12000]}

Return ONLY valid JSON:
{{"dataset_guess": "...", "no_name_column": false,
 "mapping": [{{"source": "<header>", "target": "<field>|extra|ignore", "transform": "none", "note": ""}}]}}"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.1, response_mime_type="application/json"))
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        # sanitise
        clean, used_targets = [], set()
        for m in (data.get("mapping") or []):
            if not isinstance(m, dict) or not m.get("source"):
                continue
            target = (m.get("target") or "extra").strip()
            if target not in TARGET_FIELDS and target not in ("extra", "ignore"):
                target = "extra"
            if target in used_targets and target not in ("extra", "ignore"):
                target = "extra"  # never two columns onto one field
            used_targets.add(target)
            transform = m.get("transform") if m.get("transform") in _TRANSFORMS else "none"
            clean.append({"source": str(m["source"])[:120], "target": target,
                          "transform": transform, "note": (m.get("note") or "")[:150]})
        return {"dataset_guess": (data.get("dataset_guess") or "")[:120],
                "no_name_column": bool(data.get("no_name_column")),
                "mapping": clean}
    except Exception as e:
        logger.warning(f"[SmartUpload] mapping analysis failed: {e}")
        return {"error": f"AI mapping failed: {e}"}


def apply_mapping(df, mapping: List[dict]) -> List[dict]:
    """Deterministically apply the AI-designed mapping to every row."""
    rows = []
    by_source = {m["source"]: m for m in mapping}
    for _, r in df.iterrows():
        out, extra = {}, {}
        for col in df.columns:
            m = by_source.get(str(col))
            if not m or m["target"] == "ignore":
                continue
            val = r[col]
            if m["target"] == "extra":
                v = _apply_transform(val, "none")
                if v is not None:
                    extra[str(col)[:80]] = str(v)[:300]
            else:
                v = _apply_transform(val, m["transform"])
                if v is not None:
                    out[m["target"]] = v
        name = str(out.get("name") or "").strip()
        if not name or name.lower() in ("nan", "none"):
            continue
        out["name"] = name[:150]
        if extra:
            out["extra_data"] = json.dumps(extra)[:4000]
        rows.append(out)
    return rows


def extract_pdf_companies(data: bytes, filename: str) -> Dict:
    """PDF -> companies. Detects list PDFs (broker books, brochures) vs a
    single-company document (teaser/CIM) and extracts accordingly."""
    import base64
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}
    if len(data) > 18 * 1024 * 1024:
        return {"error": "PDF too large (max 18MB)"}
    fields_doc = "\n".join(f"- {k}: {v}" for k, v in TARGET_FIELDS.items())
    prompt = f"""This PDF ("{filename}") was uploaded to a private equity deal-sourcing database.
First decide its shape:
- "list": it presents MANY companies (broker book, conference brochure, award list, directory)
- "single": it is about ONE company (teaser, CIM, one-pager)
Then extract every company you can actually see, with whatever attributes the document states.

TARGET ATTRIBUTES (monetary values: convert to GBP MILLIONS using approx rates
USD x{USD_GBP}, EUR x{EUR_GBP}; if the document gives raw units, divide to millions):
{fields_doc}

STRICT RULES:
- Only companies and values that appear in the document. NEVER invent, estimate or
  complete from memory. Omit attributes the document does not state.
- For "single" shape, return that one company with maximum faithful detail.
- Any stated attribute that fits no target field goes into "extra" as key/value text.

Return ONLY valid JSON:
{{"shape": "list" or "single", "dataset_guess": "one line",
 "companies": [{{"name": "...", "<target_field>": "...", "extra": {{"label": "value"}}}}]}}"""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        resp = model.generate_content(
            [{"mime_type": "application/pdf", "data": base64.b64encode(data).decode()}, prompt],
            generation_config={"response_mime_type": "application/json"})
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text[text.find("{"):text.rfind("}") + 1])
        rows = []
        for c in (parsed.get("companies") or []):
            if not isinstance(c, dict) or not (c.get("name") or "").strip():
                continue
            out = {}
            for k, v in c.items():
                if k == "extra" and isinstance(v, dict) and v:
                    out["extra_data"] = json.dumps({str(kk)[:80]: str(vv)[:300] for kk, vv in v.items()})[:4000]
                elif k in TARGET_FIELDS and v not in (None, ""):
                    out[k] = v
            out["name"] = str(c["name"]).strip()[:150]
            rows.append(out)
        return {"shape": parsed.get("shape") or "list",
                "dataset_guess": (parsed.get("dataset_guess") or "")[:120],
                "companies": rows}
    except Exception as e:
        logger.warning(f"[SmartUpload] PDF extraction failed for {filename}: {e}")
        return {"error": f"PDF extraction failed: {e}"}


def smart_parse(data: bytes, filename: str) -> Dict:
    """Full pipeline for one file. Returns
    {kind, dataset_guess, mapping?, companies, total, sample, warnings}."""
    fn = filename.lower()
    warnings = []
    if fn.endswith(".pdf"):
        res = extract_pdf_companies(data, filename)
        if res.get("error"):
            return {"kind": "pdf", "companies": [], "total": 0, "sample": [],
                    "warnings": [res["error"]], "dataset_guess": "", "mapping": []}
        rows = res["companies"]
        return {"kind": "pdf", "dataset_guess": res.get("dataset_guess", ""),
                "shape": res.get("shape"), "mapping": [],
                "companies": rows, "total": len(rows), "sample": rows[:8], "warnings": warnings}

    df, note = read_tabular(data, filename)
    if df is None:
        return {"kind": "tabular", "companies": [], "total": 0, "sample": [],
                "warnings": [note or "Unreadable file"], "dataset_guess": "", "mapping": []}
    if note:
        warnings.append(note)
    df = df.dropna(how="all")
    headers = [str(c) for c in df.columns]
    sample = [
        {str(k): (str(v)[:120] if v is not None else None) for k, v in row.items()}
        for row in df.head(15).to_dict(orient="records")
    ]
    analysis = analyze_mapping(headers, sample, filename)
    if analysis.get("error"):
        return {"kind": "tabular", "companies": [], "total": 0, "sample": [],
                "warnings": [analysis["error"]], "dataset_guess": "", "mapping": []}
    if analysis.get("no_name_column"):
        return {"kind": "tabular", "companies": [], "total": 0, "sample": [],
                "warnings": ["The AI could not find a company-name column in this file."],
                "dataset_guess": analysis.get("dataset_guess", ""), "mapping": analysis.get("mapping", [])}
    rows = apply_mapping(df, analysis["mapping"])
    return {"kind": "tabular", "dataset_guess": analysis.get("dataset_guess", ""),
            "mapping": analysis["mapping"], "companies": rows, "total": len(rows),
            "sample": rows[:8], "warnings": warnings}
