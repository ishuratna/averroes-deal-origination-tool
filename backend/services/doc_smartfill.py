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
    "revenue_growth_pct":   ("FLOAT64", "Revenue growth (%)",       "fill_or_confirm"),
    "ebitda_margin_pct":    ("FLOAT64", "EBITDA margin (%)",        "fill_or_confirm"),
    "revenue_estimate_m":   ("FLOAT64", "Revenue (GBP m, latest)",  "fill_or_confirm"),
}

# Figures that may legitimately be negative.
_SIGNED = {"revenue_growth_pct", "ebitda_margin_pct"}

# Financial year table: metric -> the column for slot 1/2/3 (None = no column).
YEAR_METRICS: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {
    "revenue":           ("revenue_y1", "revenue_y2", "revenue_y3"),
    "gross_profit":      ("gross_profit_y1", "gross_profit_y2", None),
    "profit_before_tax": ("profit_y1", "profit_y2", "profit_y3"),
    "cash":              ("cash_y1", None, None),
    "net_assets":        ("net_assets_y1", None, None),
    "total_assets":      ("total_assets_y1", None, None),
}
YEAR_DATE_COLS = ("revenue_y1_date", "revenue_y2_date", "revenue_y3_date")
_YEAR_SIGNED = {"profit_before_tax", "net_assets"}

# Every column this module may write, with its BigQuery type (the apply step
# binds parameters by type; an unknown column is refused, never guessed).
COLUMN_TYPES: Dict[str, str] = {k: v[0] for k, v in SCALAR_FIELDS.items()}
COLUMN_TYPES.update({c: "FLOAT64" for cols in YEAR_METRICS.values() for c in cols if c})
COLUMN_TYPES.update({c: "STRING" for c in YEAR_DATE_COLS})
COLUMN_TYPES.update({"revenue_source": "STRING", "profit_y1_date": "STRING"})


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


# ── The extraction prompt ────────────────────────────────────────────────────

def extraction_prompt(company: Dict, filename: str) -> str:
    current = {f: company.get(f) for f in SCALAR_FIELDS}
    years = []
    for i, dcol in enumerate(YEAR_DATE_COLS, 1):
        rev = company.get(f"revenue_y{i}")
        if rev is not None or company.get(dcol):
            years.append({"period_end": company.get(dcol), "revenue": rev})
    return f"""A company we are evaluating, "{company.get('name')}", shared the attached document
("{filename}"). Our current record holds:
{json.dumps(current, default=str)}
Financial years on file (GBP, period end): {json.dumps(years, default=str)}

Extract EVERYTHING the document clearly states about the company. Do not invent
or infer values that are not in the document. Every number needs the exact
quote or figure and where it appears (slide/page/sheet) as evidence.

Return ONLY valid JSON with this shape (omit keys the document does not support):
{{
  "summary": "one or two sentences: what the document is and its key facts",
  "company": {{
    "description": "what the company does, in full (products, customers, model)",
    "sector": "...", "verticals": "comma-separated", "keywords": "comma-separated",
    "hq_city": "...", "hq_country": "...", "website": "...", "company_linkedin": "...",
    "year_founded": 2018, "employees": 34,
    "directors": "Name - Title; Name - Title (founders and senior management)",
    "total_raised_m": 4.5, "last_financing_date": "YYYY-MM", "last_financing_size_m": 2.0,
    "last_financing_type": "Seed | Series A | ...", "active_investors": "comma-separated",
    "competitors": "comma-separated",
    "revenue_growth_pct": 30.0, "ebitda_margin_pct": -12.0
  }},
  "evidence": {{"field_name": "quote + location", "...": "..."}},
  "financial_years": [
    {{"period_end": "YYYY-MM-DD", "basis": "actual | budget | forecast",
      "revenue": 5200000, "gross_profit": 4160000, "ebitda": -250000,
      "profit_before_tax": -300000, "cash": 812345, "net_assets": 1900000,
      "total_assets": 2500000, "employees": 34, "evidence": "slide 9, P&L table"}}
  ]
}}
Rules: all money in GBP as absolute numbers (not thousands) except the *_m
fields, which are GBP millions; convert other currencies and state the rate in
evidence. Use "basis": "actual" only for reported/historic figures; budgets and
forecasts are never "actual". If the period end is only given as a year, use
that year's 31 December unless the document states the year end."""


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
        if raw in (None, "", [], {}):
            continue
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
        ev = _clean_str(evidence.get(col) or "")[:300]
        writes = {col: new}
        if col == "revenue_estimate_m":
            writes["revenue_source"] = "Company document"
        if rule == "longer":
            if old is not None and len(str(new)) <= len(str(old)):
                continue
            kind = "fill"
        else:
            kind = "fill" if old is None else "conflict"
        items.append({"key": col, "label": label, "kind": kind,
                      "old": _fmt(old, col), "new": _fmt(new, col),
                      "evidence": ev, "writes": writes})
    return items


def _stored_years(company: Dict) -> Tuple[List[Tuple[str, Dict]], bool]:
    """[(period_end, {metric: value})] for the slots that hold anything, and a
    flag: True when some slot holds a value but no date (cannot be aligned)."""
    years, undated = [], False
    for slot in range(3):
        metrics = {}
        for m, cols in YEAR_METRICS.items():
            col = cols[slot]
            if col and company.get(col) is not None:
                v = _num(company.get(col), signed=True)
                if v is not None:
                    metrics[m] = v
        d = _period(company.get(YEAR_DATE_COLS[slot]))
        if not d and slot == 0 and metrics.get("profit_before_tax") is not None:
            d = _period(company.get("profit_y1_date"))
        if metrics:
            if not d:
                undated = True
            else:
                years.append((d, metrics))
    return years, undated


def _doc_years(extracted: Dict) -> Tuple[Dict[str, Dict], str]:
    """{period_end: {metric: value}} for ACTUAL years only, plus joined evidence."""
    out: Dict[str, Dict] = {}
    evs = []
    for y in extracted.get("financial_years") or []:
        if not isinstance(y, dict):
            continue
        if _clean_str(y.get("basis") or "actual").lower() != "actual":
            continue
        d = _period(y.get("period_end"))
        if not d:
            continue
        metrics = {}
        for m in YEAR_METRICS:
            v = _num(y.get(m), signed=m in _YEAR_SIGNED)
            if v is not None and (m in _YEAR_SIGNED or v > 0):
                metrics[m] = v
        if metrics:
            out.setdefault(d, {}).update(metrics)
            if y.get("evidence"):
                evs.append(f"{d}: {_clean_str(y['evidence'])}")
    return out, "; ".join(evs)[:300]


def _table_writes(years: List[Tuple[str, Dict]]) -> Dict:
    """Column writes for an ordered (newest first) list of up to 3 years."""
    writes: Dict = {}
    for slot in range(3):
        d, metrics = (years[slot] if slot < len(years) else (None, {}))
        writes[YEAR_DATE_COLS[slot]] = d
        for m, cols in YEAR_METRICS.items():
            col = cols[slot]
            if col:
                writes[col] = metrics.get(m)
    if years:
        writes["profit_y1_date"] = years[0][0]
    return writes


def _table_text(years: List[Tuple[str, Dict]]) -> str:
    if not years:
        return "(empty)"
    rows = []
    for d, m in years:
        parts = [f"FY to {d}"]
        for k, lab in (("revenue", "rev"), ("gross_profit", "GP"), ("profit_before_tax", "PBT"),
                       ("cash", "cash"), ("net_assets", "net assets"), ("total_assets", "assets")):
            if m.get(k) is not None:
                parts.append(f"{lab} {_fmt_money(m[k])}")
        rows.append(" · ".join(parts))
    return "\n".join(rows)


def _financial_items(company: Dict, extracted: Dict) -> List[Dict]:
    doc, evidence = _doc_years(extracted)
    if not doc:
        return []
    stored, undated = _stored_years(company)
    stored_map = dict(stored)

    if undated:
        # Values we cannot align to a period: replacing them is a decision.
        new = sorted(doc.items(), key=lambda kv: kv[0], reverse=True)[:3]
        return [{"key": "financials", "label": "Financials by year", "kind": "conflict",
                 "old": "Stored figures have no period dates:\n" + "; ".join(
                     f"{c}={_fmt_money(_num(company.get(c), signed=True))}"
                     for cols in YEAR_METRICS.values() for c in cols
                     if c and company.get(c) is not None),
                 "new": _table_text(new), "evidence": evidence, "writes": _table_writes(new)}]

    merged: Dict[str, Dict] = {d: dict(m) for d, m in stored}
    disagreement = False
    changed = False
    for d, m in doc.items():
        cur = merged.setdefault(d, {})
        for k, v in m.items():
            if k in cur and cur[k] is not None:
                if not _same(cur[k], v):
                    disagreement = True
                    cur[k] = v            # document value shown as the proposal
                    changed = True
            else:
                cur[k] = v
                changed = True
    if not changed:
        return []
    ordered = sorted(merged.items(), key=lambda kv: kv[0], reverse=True)
    kept = ordered[:3]
    dropped = [d for d, _ in ordered[3:] if d in stored_map]
    kind = "conflict" if (disagreement or dropped) else "fill"
    label = "Financials by year"
    if dropped and not disagreement:
        label += f" (adds a newer year; FY {', '.join(dropped)} would leave the 3-year window)"
    return [{"key": "financials", "label": label, "kind": kind,
             "old": _table_text(stored), "new": _table_text(kept),
             "evidence": evidence, "writes": _table_writes(kept)}]


def _derived_items(company: Dict, fin_item: Optional[Dict], existing_keys: set) -> List[Dict]:
    """From the financial table AS IT WOULD STAND after the document (document
    years merged with stored years): latest revenue -> revenue_estimate_m, the
    two latest years -> revenue_growth_pct. Only when the document did not
    state those directly, and only when the document contributed a year."""
    if not fin_item:
        return []
    w = fin_item["writes"]
    # A figure derived from a table that still awaits confirmation cannot be
    # written before the table is: it inherits the table's status.
    dependent = fin_item["kind"] == "conflict"
    items = []
    r1, d1 = w.get("revenue_y1"), w.get("revenue_y1_date")
    r2, d2 = w.get("revenue_y2"), w.get("revenue_y2_date")
    if "revenue_estimate_m" not in existing_keys and r1:
        new = round(r1 / 1e6, 3)
        old = _num(company.get("revenue_estimate_m"))
        if old is None or not _same(old, new):
            items.append({"key": "revenue_estimate_m", "label": "Revenue (GBP m, latest)",
                          "kind": "fill" if (old is None and not dependent) else "conflict",
                          "old": _fmt(old, "revenue_estimate_m"), "new": _fmt(new, "revenue_estimate_m"),
                          "evidence": f"FY to {d1}: revenue {_fmt_money(r1)}",
                          "writes": {"revenue_estimate_m": new, "revenue_source": "Company document"}})
    if "revenue_growth_pct" not in existing_keys and r1 and r2:
        new = round((r1 / r2 - 1) * 100, 1)
        old = _num(company.get("revenue_growth_pct"), signed=True)
        if old is None or not _same(old, new):
            items.append({"key": "revenue_growth_pct", "label": "Revenue growth (%)",
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
    items += _derived_items(company, fin[0] if fin else None, {i["key"] for i in items})
    for it in items:
        it["writes"] = {c: v for c, v in it["writes"].items() if c in COLUMN_TYPES}
    items = [i for i in items if i["writes"]]
    return {"fills": [i for i in items if i["kind"] == "fill"],
            "conflicts": [i for i in items if i["kind"] == "conflict"]}


def merge_writes(items: List[Dict]) -> Dict:
    """One column map from several items (later items win on overlap)."""
    out: Dict = {}
    for it in items:
        out.update(it.get("writes") or {})
    return out
