"""
Document SmartFill: a founder's document becomes a first-class source.

Per Ishu (7 Sep 2026): "find all new numbers and add them if the existing
numbers aren't available; for existing, show all numbers and take
confirmation to replace."  So the planner produces TWO lists from one AI
read:

  fills      the record holds nothing for the field  -> written immediately
  conflicts  the record holds a DIFFERENT value       -> held for review;
             the profile shows current vs document (with evidence) and Ishu
             ticks what to replace. Nothing is overwritten silently.

Three exceptions to "conflict", all deliberate:
  * description follows the doctrine "longer wins": a longer text is a fill,
    a shorter one is dropped (never a conflict - marketing copy is not worth
    a decision).
  * a value equal to what we hold (1% tolerance on numbers) is nothing.
  * a NEWER financial year than any we hold is a fill: it adds a year, the
    older years shift down (y1 -> y2 -> y3). It becomes a conflict only when
    a year we hold would fall off the 3-year window or a shared year disagrees.

No identity guard here, on purpose (Ishu, 7 Sep 2026): a document uploaded
from a company's card, or attached to that company's email thread, IS that
company's document. The AI is told which company it is reading about and only
extracts; it never has to work out whose deck this is.

Everything in here is PURE (no BigQuery, no AI) so the rules are testable;
email_docs_service.py does the I/O. Office files (pptx/xlsx/docx) are turned
into text here so the AI read covers a raw deck or an Excel model, not just
PDFs.
"""
import io
import json
import logging
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── What the AI is asked for, and where it lands ─────────────────────────────
# column -> (BigQuery type, label shown in the review, rule)
#   fill_or_confirm  blank -> fill; different -> conflict
#   longer           description doctrine
SCALAR_FIELDS: Dict[str, Tuple[str, str, str]] = {
    "description":          ("STRING",  "Description",              "longer"),
    "sector":               ("STRING",  "Sector",                   "fill_or_confirm"),
    "verticals":            ("STRING",  "Verticals",                "fill_or_confirm"),
    "keywords":             ("STRING",  "Keywords",                 "fill_or_confirm"),
    "hq_city":              ("STRING",  "HQ city",                  "fill_or_confirm"),
    "hq_country":           ("STRING",  "HQ country",               "fill_or_confirm"),
    "website":              ("STRING",  "Website",                  "fill_or_confirm"),
    "company_linkedin":     ("STRING",  "LinkedIn",                 "fill_or_confirm"),
    "year_founded":         ("INT64",   "Year founded",             "fill_or_confirm"),
    "employees":            ("INT64",   "Employees",                "fill_or_confirm"),
    "directors":            ("STRING",  "Founders / management",    "fill_or_confirm"),
    "total_raised_m":       ("FLOAT64", "Total raised (GBP m)",     "fill_or_confirm"),
    "last_financing_date":  ("STRING",  "Last financing date",      "fill_or_confirm"),
    "last_financing_size_m": ("FLOAT64", "Last financing size (GBP m)", "fill_or_confirm"),
    "last_financing_type":  ("STRING",  "Last financing type",      "fill_or_confirm"),
    "active_investors":     ("STRING",  "Investors",                "fill_or_confirm"),
    "competitors":          ("STRING",  "Competitors",              "fill_or_confirm"),
    "revenue_growth_pct":   ("FLOAT64", "Revenue growth, last year (%)", "fill_or_confirm"),
    "revenue_cagr_3yr_pct": ("FLOAT64", "Revenue CAGR, multi-year (%)", "fill_or_confirm"),
    "ebitda_margin_pct":    ("FLOAT64", "EBITDA margin (%)",        "fill_or_confirm"),
    "revenue_estimate_m":   ("FLOAT64", "Revenue (GBP m, latest)",  "fill_or_confirm"),
}

# Figures that may legitimately be negative.
_SIGNED = {"revenue_growth_pct", "revenue_cagr_3yr_pct", "ebitda_margin_pct"}

# Comma-separated SET fields. A document naming investors/verticals/keywords we
# already hold adds nothing; naming NEW ones is new information and is merged
# in as a fill (nothing stored is removed, so no confirmation is needed - the
# first live review asked to replace six investors with 'Innovate UK', 8 Sep 2026).
LIST_FIELDS = {"verticals", "keywords", "active_investors", "competitors"}


def _split_list(v) -> List[str]:
    return [x.strip() for x in re.split(r"[,;\n]+", str(v or "")) if x.strip()]


def _norm_url(v) -> str:
    v = (v or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = re.sub(r"^www\d?\.", "", v)
    return v.rstrip("/")

# ── Multi-year financials (per Ishu, 8 Sep 2026) ────────────────────────────
# The record used to have THREE positional revenue slots and a few single-year
# columns, so a deck's FY2021-2025 chart, gross margin by year or a product
# split had nowhere to go. The store is now `company_financials`: one row per
# (company, period_end, metric, segment) with unit, basis, source and evidence,
# unbounded in years and metrics. The legacy revenue_y1..y3 / gross_profit /
# profit / cash / net_assets / total_assets columns are a PROJECTION of the
# latest three ACTUAL years, kept so scoring, the IC deck and old views work.
#
# metric -> (unit, label). Units: GBP (absolute), pct, count.
METRICS: Dict[str, Tuple[str, str]] = {
    "revenue":           ("GBP",   "Revenue"),
    "arr":               ("GBP",   "ARR"),
    "gross_profit":      ("GBP",   "Gross profit"),
    "gross_margin_pct":  ("pct",   "Gross margin"),
    "ebitda":            ("GBP",   "EBITDA"),
    "ebitda_margin_pct": ("pct",   "EBITDA margin"),
    "profit_before_tax": ("GBP",   "Profit before tax"),
    "net_income":        ("GBP",   "Net income"),
    "cash":              ("GBP",   "Cash"),
    "net_assets":        ("GBP",   "Net assets"),
    "total_assets":      ("GBP",   "Total assets"),
    "employees":         ("count", "Employees"),
    "customers":         ("count", "Customers"),
}
_METRIC_SIGNED = {"gross_profit", "gross_margin_pct", "ebitda", "ebitda_margin_pct",
                  "profit_before_tax", "net_income", "net_assets"}
BASES = ("actual", "budget", "forecast")

# Legacy projection: metric -> the column for slot 1/2/3 (None = no column).
YEAR_METRICS: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {
    "revenue":           ("revenue_y1", "revenue_y2", "revenue_y3"),
    "gross_profit":      ("gross_profit_y1", "gross_profit_y2", None),
    "profit_before_tax": ("profit_y1", "profit_y2", "profit_y3"),
    "cash":              ("cash_y1", None, None),
    "net_assets":        ("net_assets_y1", None, None),
    "total_assets":      ("total_assets_y1", None, None),
}
YEAR_DATE_COLS = ("revenue_y1_date", "revenue_y2_date", "revenue_y3_date")
_YEAR_SIGNED = _METRIC_SIGNED


def cells_from_columns(row: Dict, source: str) -> List[Dict]:
    """Seed cells from the legacy y1..y3 columns (Companies House / imports),
    so a company's existing figures appear in the store before a document
    adds to them. Only slots with a period date can be placed."""
    cells: List[Dict] = []
    for slot in range(3):
        d = _period(row.get(YEAR_DATE_COLS[slot])) or (
            _period(row.get("profit_y1_date")) if slot == 0 else None)
        if not d:
            continue
        for m, cols in YEAR_METRICS.items():
            col = cols[slot]
            if col and row.get(col) is not None:
                v = _num(row.get(col), signed=True)
                if v is not None and (v != 0 or m in ("profit_before_tax", "net_assets")):
                    cells.append({"period_end": d, "metric": m, "segment": "", "value": v,
                                  "unit": "GBP", "basis": "actual", "source": source, "evidence": ""})
        if slot == 0 and row.get("employees_ch") is not None:
            v = _num(row.get("employees_ch"))
            if v:
                cells.append({"period_end": d, "metric": "employees", "segment": "", "value": v,
                              "unit": "count", "basis": "actual", "source": source, "evidence": ""})
    return cells


def project_to_columns(cells: List[Dict]) -> Dict:
    """The legacy columns from the store: latest three ACTUAL, whole-company
    periods that carry a revenue or balance-sheet figure. Returns the column
    map to UPDATE on targets (None clears a slot that no longer exists)."""
    by_period: Dict[str, Dict[str, float]] = {}
    for c in cells:
        if c.get("basis", "actual") != "actual" or c.get("segment"):
            continue
        if c["metric"] in YEAR_METRICS or c["metric"] == "ebitda" or c["metric"] == "revenue":
            by_period.setdefault(c["period_end"], {})[c["metric"]] = float(c["value"])
    periods = sorted((d for d, m in by_period.items() if any(k in YEAR_METRICS for k in m)), reverse=True)[:3]
    writes: Dict = {}
    for slot in range(3):
        d = periods[slot] if slot < len(periods) else None
        metrics = by_period.get(d, {}) if d else {}
        writes[YEAR_DATE_COLS[slot]] = d
        for m, cols in YEAR_METRICS.items():
            col = cols[slot]
            if col:
                writes[col] = metrics.get(m)
    writes["profit_y1_date"] = periods[0] if periods else None
    if periods:
        m0 = by_period[periods[0]]
        if m0.get("revenue") and m0.get("ebitda") is not None:
            writes["ebitda_margin_pct"] = round(m0["ebitda"] / m0["revenue"] * 100, 1)
    return writes


# Every column this module may write, with its BigQuery type (the apply step
# binds parameters by type; an unknown column is refused, never guessed).
COLUMN_TYPES: Dict[str, str] = {k: v[0] for k, v in SCALAR_FIELDS.items()}
COLUMN_TYPES.update({c: "FLOAT64" for cols in YEAR_METRICS.values() for c in cols if c})
COLUMN_TYPES.update({c: "STRING" for c in YEAR_DATE_COLS})
COLUMN_TYPES.update({"revenue_source": "STRING", "profit_y1_date": "STRING"})
# "financials" is a virtual write: a list of cells for company_financials, applied
# by the I/O layer (upsert + projection), never a targets column itself.


# ── Office files -> text (so the AI can read a raw deck or model) ────────────

OFFICE_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
_EXT_TYPES = {".pptx": "pptx", ".xlsx": "xlsx", ".docx": "docx"}
MAX_TEXT_CHARS = 120_000       # ~30k tokens; more than any deck needs


def office_kind(content_type: str, filename: str) -> Optional[str]:
    """pptx | xlsx | docx | None. Browsers often send octet-stream for Office
    files, so the extension is the fallback."""
    k = OFFICE_TYPES.get((content_type or "").lower())
    if k:
        return k
    ext = "." + (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    return _EXT_TYPES.get(ext)


def office_to_text(kind: str, data: bytes) -> str:
    """Plain text from an Office file. Slide/sheet/paragraph order preserved
    and labelled, so the AI can cite 'slide 7' or 'sheet P&L' as evidence."""
    out: List[str] = []
    try:
        if kind == "pptx":
            from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            for i, slide in enumerate(prs.slides, 1):
                out.append(f"\n=== Slide {i} ===")
                for sh in slide.shapes:
                    if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip():
                        out.append(sh.text_frame.text.strip())
                    if getattr(sh, "has_table", False):
                        for r in sh.table.rows:
                            out.append(" | ".join(c.text.strip() for c in r.cells))
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                    n = slide.notes_slide.notes_text_frame.text.strip()
                    if n:
                        out.append(f"[notes] {n}")
        elif kind == "xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            for ws in wb.worksheets:
                out.append(f"\n=== Sheet {ws.title} ===")
                n = 0
                for row in ws.iter_rows(values_only=True):
                    cells = ["" if v is None else str(v) for v in row]
                    if any(c.strip() for c in cells):
                        out.append(" | ".join(cells).rstrip(" |"))
                        n += 1
                    if n >= 400:          # a model's first 400 populated rows per sheet
                        out.append("[...]")
                        break
        elif kind == "docx":
            from docx import Document
            doc = Document(io.BytesIO(data))
            for p in doc.paragraphs:
                if p.text.strip():
                    out.append(p.text.strip())
            for t in doc.tables:
                for r in t.rows:
                    out.append(" | ".join(c.text.strip() for c in r.cells))
    except Exception as e:
        logger.warning(f"[DocSmartFill] {kind} text extraction failed: {e}")
        return ""
    text = "\n".join(out).strip()
    return text[:MAX_TEXT_CHARS]


MIN_PDF_TEXT_CHARS = 1500     # below this the PDF is a scan/picture deck: use vision


def pdf_to_text(data: bytes) -> str:
    """The PDF's own text layer, page-labelled ('=== Page 7 ===') so evidence
    can cite pages. Empty for scans. This is the first route for every PDF:
    a 60MB shareholder update is mostly images by weight but its facts are in
    the text layer, which is a few hundred KB - no upload to the model, no
    size limit, a fraction of the cost. Vision is the fallback, not the default."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        out: List[str] = []
        total = 0
        for i, page in enumerate(reader.pages, 1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                t = ""
            if t:
                out.append(f"\n=== Page {i} ===\n{t}")
                total += len(t)
            if total >= MAX_TEXT_CHARS:
                out.append("[...]")
                break
        return "\n".join(out).strip()[:MAX_TEXT_CHARS]
    except Exception as e:
        logger.warning(f"[DocSmartFill] pdf text extraction failed: {e}")
        return ""


MAX_VISION_PAGES = 80
VISION_BUDGET_BYTES = 16 * 1024 * 1024   # all page images together, inline


def pdf_pages_to_images(data: bytes, max_pages: int = MAX_VISION_PAGES,
                        budget: int = VISION_BUDGET_BYTES) -> List[bytes]:
    """Render a scanned/picture PDF's pages to JPEGs small enough to send
    inline. A 60MB exported deck becomes ~40 images of ~150KB: the model sees
    every page, nothing is uploaded to a file store, no size-limit 400s.
    Renders at 110 dpi and steps down if the budget is exceeded."""
    try:
        import pymupdf
    except ImportError:
        return []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        logger.warning(f"[DocSmartFill] pdf render open failed: {e}")
        return []
    for dpi, quality in ((110, 70), (85, 60), (65, 50)):
        out: List[bytes] = []
        total = 0
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                jpg = page.get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=quality)
                out.append(jpg)
                total += len(jpg)
                if total > budget:
                    break
        except Exception as e:
            logger.warning(f"[DocSmartFill] pdf render failed: {e}")
            return []
        if total <= budget:
            return out
    return out[: max(1, len(out) // 2)]


# ── The extraction prompt ────────────────────────────────────────────────────

def extraction_prompt(company: Dict, filename: str) -> str:
    current = {f: company.get(f) for f in SCALAR_FIELDS}
    held = {}
    for c in (company.get("_financials") or cells_from_columns(company, "record")):
        if c.get("basis", "actual") == "actual" and not c.get("segment"):
            held.setdefault(c["period_end"], {})[c["metric"]] = c["value"]
    years = [{"period_end": d, **m} for d, m in sorted(held.items(), reverse=True)][:8]
    return f"""A company we are evaluating, "{company.get('name')}", shared the attached document
("{filename}"). Our current record holds:
{json.dumps(current, default=str)}
Financial figures on file (GBP, by period end): {json.dumps(years, default=str)}

Extract EVERYTHING the document clearly states about the company. Do not invent
or infer values that are not in the document. Every number needs the exact
quote or figure and where it appears (slide/page/sheet) as evidence.

Return ONLY valid JSON with this shape (omit fields the document does not support).
EVERY company field is an object {{"value": ..., "evidence": "quote + where (page/slide/sheet)"}}:
{{
  "summary": "one or two sentences: what the document is and its key facts",
  "company": {{
    "description": {{"value": "what the company does, in full (products, customers, model)", "evidence": "..."}},
    "sector": {{"value": "...", "evidence": "..."}},
    "verticals": {{"value": "comma-separated", "evidence": "..."}},
    "keywords": {{"value": "comma-separated", "evidence": "..."}},
    "hq_city": {{"value": "...", "evidence": "..."}}, "hq_country": {{"value": "...", "evidence": "..."}},
    "website": {{"value": "...", "evidence": "..."}}, "company_linkedin": {{"value": "...", "evidence": "..."}},
    "year_founded": {{"value": 2018, "evidence": "..."}}, "employees": {{"value": 34, "evidence": "..."}},
    "directors": {{"value": "Name - Title; Name - Title (founders and senior management)", "evidence": "..."}},
    "total_raised_m": {{"value": 4.5, "evidence": "..."}},
    "last_financing_date": {{"value": "YYYY-MM", "evidence": "..."}},
    "last_financing_size_m": {{"value": 2.0, "evidence": "..."}},
    "last_financing_type": {{"value": "Seed | Series A | ...", "evidence": "..."}},
    "active_investors": {{"value": "comma-separated", "evidence": "..."}},
    "competitors": {{"value": "comma-separated", "evidence": "..."}},
    "revenue_growth_pct": {{"value": 30.0, "evidence": "latest year vs the year before ONLY"}},
    "revenue_cagr_3yr_pct": {{"value": 92.0, "evidence": "a multi-year average/CAGR goes HERE, never in revenue_growth_pct"}},
    "ebitda_margin_pct": {{"value": -12.0, "evidence": "..."}}
  }},
  "financial_years": [
    {{"period_end": "YYYY-MM-DD", "basis": "actual | budget | forecast",
      "revenue": 5200000, "arr": 4800000, "gross_profit": 4160000, "gross_margin_pct": 80.0,
      "ebitda": -250000, "ebitda_margin_pct": -4.8, "profit_before_tax": -300000, "net_income": -310000,
      "cash": 812345, "net_assets": 1900000, "total_assets": 2500000,
      "employees": 34, "customers": 120,
      "segments": [{{"name": "Instruments", "revenue": 3100000}}, {{"name": "Software", "revenue": 2100000}}],
      "evidence": "slide 9, P&L table; revenue chart p6"}}
  ]
}}
Rules: all money in GBP as absolute numbers (not thousands) except the *_m
fields, which are GBP millions; convert other currencies and state the rate in
evidence. Use "basis": "actual" only for reported/historic figures; budgets and
forecasts are never "actual". If the period end is only given as a year, use
that year's 31 December unless the document states the year end.

FINANCIAL YEARS: return ONE ENTRY PER YEAR the document shows, historic and
current alike - not just the latest. Revenue-by-year CHARTS count: read every
bar/point, use the data labels where printed, otherwise your best reading of
the axis, and say "read from chart, approx." in that year's evidence. A deck
that says "92% growth 2021-2025" has revenue for 2021, 2022, 2023, 2024 and
2025 somewhere in it; find them. Prior years of a table (e.g. FY24 column
beside FY25) are separate entries with their own period_end. Include every
metric the year shows (margins as percentages, counts as integers); include a
revenue split by product/segment/geography under "segments" when the document
gives one. Omit metrics a year does not state - never fill a gap by guessing."""


# ── Normalisation helpers ────────────────────────────────────────────────────

def _num(v, signed: bool = False) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = re.sub(r"[£$€,\s]", "", v)
        if v.endswith("%"):
            v = v[:-1]
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if f != f:      # NaN
        return None
    if not signed and f < 0:
        return None
    return f


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return abs(a - b) <= 0.01 * max(abs(a), abs(b), 1e-9)
    return str(a).strip().lower() == str(b).strip().lower()


def _clean_str(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _period(s) -> Optional[str]:
    """'YYYY-MM-DD' or None."""
    s = _clean_str(s)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        # last day of that month
        nxt = date(y + (mo == 12), (mo % 12) + 1, 1)
        return (nxt - __import__("datetime").timedelta(days=1)).isoformat()
    m = re.match(r"^(\d{4})$", s)
    if m:
        return f"{m.group(1)}-12-31"
    return None


def _fmt(v, col: str = "") -> str:
    if v is None or v == "":
        return "(empty)"
    if isinstance(v, float) and col.endswith("_m"):
        return f"£{v:,.2f}m"
    if isinstance(v, float) and col.endswith("_pct"):
        return f"{v:+.1f}%"
    if isinstance(v, float):
        return f"£{v:,.0f}" if abs(v) >= 1000 else f"{v:g}"
    s = str(v)
    return s if len(s) <= 160 else s[:157] + "..."


def _fmt_money(v) -> str:
    if v is None:
        return "-"
    a = abs(v)
    s = f"£{a/1e6:.2f}m" if a >= 1e6 else f"£{a/1e3:.0f}k" if a >= 1e3 else f"£{a:.0f}"
    return "-" + s if v < 0 else s


# ── The planner ──────────────────────────────────────────────────────────────

def _scalar_items(company: Dict, extracted: Dict, evidence: Dict) -> List[Dict]:
    items = []
    src = extracted.get("company") or {}
    for col, (btype, label, rule) in SCALAR_FIELDS.items():
        raw = src.get(col)
        ev_inline = ""
        if isinstance(raw, dict):          # {"value": ..., "evidence": ...} shape
            ev_inline = _clean_str(raw.get("evidence") or "")
            raw = raw.get("value")
        if raw in (None, "", [], {}):
            continue
        if isinstance(raw, list):
            raw = ", ".join(_clean_str(x) for x in raw if _clean_str(x))
        if btype == "STRING":
            new = _clean_str(raw)
            if not new:
                continue
        elif btype == "INT64":
            f = _num(raw)
            if f is None or f <= 0:
                continue
            new = int(round(f))
            if col == "year_founded" and not (1800 <= new <= date.today().year):
                continue
        else:
            new = _num(raw, signed=col in _SIGNED)
            if new is None or (col not in _SIGNED and new <= 0):
                continue
        old = company.get(col)
        if old in ("", None):
            old = None
        elif btype != "STRING":
            try:
                old = float(old) if btype == "FLOAT64" else int(old)
            except (ValueError, TypeError):
                old = None
        if old is not None and _same(old, new):
            continue
        if col in ("website", "company_linkedin") and old is not None and _norm_url(old) == _norm_url(new):
            continue                                   # same address, different spelling
        ev = (ev_inline or _clean_str(evidence.get(col) or ""))[:300]
        writes = {col: new}
        if col == "revenue_estimate_m":
            writes["revenue_source"] = "Company document"
        if rule == "longer":
            if old is not None and len(str(new)) <= len(str(old)):
                continue
            kind = "fill"
        elif col in LIST_FIELDS and old is not None:
            have = _split_list(old)
            seen = {h.lower() for h in have}
            added = [x for x in _split_list(new) if x.lower() not in seen]
            if not added:
                continue                               # nothing we did not already hold
            merged = ", ".join(have + added)
            writes = {col: merged}
            items.append({"key": col, "label": f"{label} (added: {', '.join(added)})", "kind": "fill",
                          "old": _fmt(old, col), "new": _fmt(merged, col),
                          "evidence": ev, "writes": writes})
            continue
        else:
            kind = "fill" if old is None else "conflict"
        items.append({"key": col, "label": label, "kind": kind,
                      "old": _fmt(old, col), "new": _fmt(new, col),
                      "evidence": ev, "writes": writes})
    return items


def _doc_cells(extracted: Dict) -> List[Dict]:
    """Cells the document states: [{period_end, metric, segment, value, unit,
    basis, evidence}]. Forecast/budget years are kept (flagged), segment
    revenue rides as metric 'revenue' with a segment name."""
    cells: List[Dict] = []
    seen = set()
    for y in extracted.get("financial_years") or []:
        if not isinstance(y, dict):
            continue
        basis = _clean_str(y.get("basis") or "actual").lower()
        if basis not in BASES:
            basis = "actual"
        d = _period(y.get("period_end"))
        if not d:
            continue
        ev = _clean_str(y.get("evidence") or "")[:300]
        for m, (unit, _label) in METRICS.items():
            v = _num(y.get(m), signed=m in _METRIC_SIGNED)
            if v is None or (unit == "count" and v <= 0) or (m not in _METRIC_SIGNED and v <= 0):
                continue
            key = (d, m, "")
            if key in seen:
                continue
            seen.add(key)
            cells.append({"period_end": d, "metric": m, "segment": "", "value": v,
                          "unit": unit, "basis": basis, "evidence": ev})
        for seg in y.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            name = _clean_str(seg.get("name") or "")[:80]
            v = _num(seg.get("revenue"))
            if not name or not v:
                continue
            key = (d, "revenue", name.lower())
            if key in seen:
                continue
            seen.add(key)
            cells.append({"period_end": d, "metric": "revenue", "segment": name, "value": v,
                          "unit": "GBP", "basis": basis, "evidence": ev})
    return cells


def _fmt_cell(c: Dict) -> str:
    v = c["value"]
    if c.get("unit") == "pct":
        s = f"{v:+.1f}%"
    elif c.get("unit") == "count":
        s = f"{int(round(v)):,}"
    else:
        s = _fmt_money(v)
    label = METRICS.get(c["metric"], ("", c["metric"]))[1]
    if c.get("segment"):
        label += f" · {c['segment']}"
    return f"{label} {s}"


def _financial_items(company: Dict, extracted: Dict) -> List[Dict]:
    """Per CELL fill-or-confirm, grouped per period for the review.

    existing cells come from company['_financials'] (the store) or, before a
    company is seeded, from its legacy columns. A cell we do not hold is a
    fill; one we hold with a different value is a conflict. Each period yields
    at most one fill item and one conflict item, so the review reads as a
    year-by-year table rather than fifty rows."""
    doc = _doc_cells(extracted)
    if not doc:
        return []
    existing = {(c["period_end"], c["metric"], (c.get("segment") or "").lower()): c
                for c in (company.get("_financials") or cells_from_columns(company, "record"))}
    fills: Dict[str, List[Dict]] = {}
    conflicts: Dict[str, List[Tuple[Dict, Dict]]] = {}
    for c in doc:
        key = (c["period_end"], c["metric"], c["segment"].lower())
        old = existing.get(key)
        if old is None:
            fills.setdefault(c["period_end"], []).append(c)
        elif not _same(float(old["value"]), c["value"]):
            conflicts.setdefault(c["period_end"], []).append((old, c))
    items: List[Dict] = []
    for d in sorted(fills, reverse=True):
        cs = fills[d]
        basis = cs[0].get("basis", "actual")
        items.append({"key": f"financials:{d}:new", "kind": "fill",
                      "label": f"FY to {d}" + (f" ({basis})" if basis != "actual" else "") + f": {len(cs)} new figure(s)",
                      "old": "(not held)", "new": "\n".join(_fmt_cell(c) for c in cs),
                      "evidence": "; ".join(sorted({c["evidence"] for c in cs if c["evidence"]}))[:300],
                      "writes": {"financials": cs}})
    for d in sorted(conflicts, reverse=True):
        pairs = conflicts[d]
        items.append({"key": f"financials:{d}:changed", "kind": "conflict",
                      "label": f"FY to {d}: {len(pairs)} figure(s) differ",
                      "old": "\n".join(_fmt_cell(o) + (f"  [{o.get('source')}]" if o.get("source") else "") for o, _ in pairs),
                      "new": "\n".join(_fmt_cell(n) for _, n in pairs),
                      "evidence": "; ".join(sorted({n["evidence"] for _, n in pairs if n["evidence"]}))[:300],
                      "writes": {"financials": [n for _, n in pairs]}})
    return items


def _derived_items(company: Dict, fin_items: List[Dict], existing_keys: set) -> List[Dict]:
    """From the whole-company ACTUAL revenue series AS IT WOULD STAND after the
    document (store + fills; conflicts only if none pending): latest revenue ->
    revenue_estimate_m, the two latest years -> revenue_growth_pct. Only when
    the document did not state those directly."""
    if not fin_items:
        return []
    dependent = any(i["kind"] == "conflict" for i in fin_items)
    series: Dict[str, float] = {}
    for c in (company.get("_financials") or cells_from_columns(company, "record")):
        if c["metric"] == "revenue" and not c.get("segment") and c.get("basis", "actual") == "actual":
            series[c["period_end"]] = float(c["value"])
    for it in fin_items:
        for c in it["writes"]["financials"]:
            if c["metric"] == "revenue" and not c["segment"] and c["basis"] == "actual":
                series[c["period_end"]] = c["value"]
    if not series:
        return []
    ordered = sorted(series.items(), reverse=True)
    (d1, r1) = ordered[0]
    items = []
    if "revenue_estimate_m" not in existing_keys and r1:
        new = round(r1 / 1e6, 3)
        old = _num(company.get("revenue_estimate_m"))
        if old is None or not _same(old, new):
            items.append({"key": "revenue_estimate_m", "label": "Revenue (GBP m, latest)",
                          "kind": "fill" if (old is None and not dependent) else "conflict",
                          "old": _fmt(old, "revenue_estimate_m"), "new": _fmt(new, "revenue_estimate_m"),
                          "evidence": f"FY to {d1}: revenue {_fmt_money(r1)}",
                          "writes": {"revenue_estimate_m": new, "revenue_source": "Company document"}})
    if "revenue_growth_pct" not in existing_keys and len(ordered) >= 2:
        (d2, r2) = ordered[1]
        if r1 and r2:
            new = round((r1 / r2 - 1) * 100, 1)
            old = _num(company.get("revenue_growth_pct"), signed=True)
            if old is None or not _same(old, new):
                items.append({"key": "revenue_growth_pct", "label": "Revenue growth, last year (%)",
                              "kind": "fill" if (old is None and not dependent) else "conflict",
                              "old": _fmt(old, "revenue_growth_pct"), "new": _fmt(new, "revenue_growth_pct"),
                              "evidence": f"{_fmt_money(r2)} (FY {d2}) -> {_fmt_money(r1)} (FY {d1})",
                              "writes": {"revenue_growth_pct": new}})
    return items


def plan_updates(company: Dict, extracted: Dict) -> Dict[str, List[Dict]]:
    """The decision. Pure.

    Returns {"fills": [...], "conflicts": [...]}; each item is
      {key, label, kind, old, new, evidence, writes: {column: value}}
    and every column in writes is in COLUMN_TYPES.
    """
    company = company or {}
    extracted = extracted or {}
    evidence = extracted.get("evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    items = _scalar_items(company, extracted, evidence)
    fin = _financial_items(company, extracted)
    items += fin
    items += _derived_items(company, fin, {i["key"] for i in items})
    for it in items:
        it["writes"] = {c: v for c, v in it["writes"].items() if c in COLUMN_TYPES or c == "financials"}
    items = [i for i in items if i["writes"]]
    return {"fills": [i for i in items if i["kind"] == "fill"],
            "conflicts": [i for i in items if i["kind"] == "conflict"]}


def merge_writes(items: List[Dict]) -> Dict:
    """One write map from several items (later items win on overlap). The
    virtual 'financials' key concatenates its cell lists."""
    out: Dict = {}
    for it in items:
        for k, v in (it.get("writes") or {}).items():
            if k == "financials":
                out.setdefault("financials", []).extend(v)
            else:
                out[k] = v
    return out
