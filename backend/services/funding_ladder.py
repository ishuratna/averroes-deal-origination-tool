"""
The funding ladder: every equity round a company has done, from Companies
House, with what the last investor paid.

WHY (Ishu, 25 Sep 2026, after reading Arcus Global on Mark to Market): the
single most useful thing on that page is the round-by-round history with a
price per share and an implied valuation, and every input for it is in the
SH01 filings we already download. We were reading one SH01, for allottee
names only, with an AI call. This module reads them ALL, from the filing's
own text, with no AI unless the text has nothing in it.

THE SH01 FORM states, per class of shares allotted: the number allotted, the
nominal value of each share, and the amount paid (including share premium)
on each share; then a statement of capital with the total shares in issue
after the allotment. So for each filing:

    raised          = sum(shares * paid per share)
    price           = raised / shares                 (weighted across classes)
    post-money      = total shares after * price      (when the statement is present)
    pre-money       = post-money - raised

A NOMINAL ISSUE (paid <= nominal, or paid = 0) is an option exercise, a bonus
issue or a founder subscription, not a fundraising. It is kept in the ledger,
marked, and excluded from "raised" and from any valuation.

Everything derived is labelled as derived on the card and traceable to the
filing (transaction id, date). The ledger is stored once per company
(`ch_funding_rounds`) and extended only for filings not yet read, so the
cost is paid once per filing, ever.
"""
import json
import logging
import re
from datetime import date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LEDGER_VERSION = 1
MAX_PDF_READS_PER_RUN = 8      # text extraction is free; this bounds the downloads
MAX_AI_FALLBACKS_PER_RUN = 3   # a scanned SH01 costs one ungrounded Gemini call


# ── Pure: read one SH01's text ───────────────────────────────────────────────

_NUM = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_DATE = r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})"


def _f(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _iso(d: str, m: str, y: str) -> str:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def parse_sh01_text(text: str) -> Dict:
    """The allotment(s) stated in an SH01, from its extracted text. PURE.

    Returns {"allotment_date", "allotments": [{"share_class", "currency",
    "shares", "nominal", "paid"}], "total_shares_after"} or {} when the text
    carries no allotment block (a scan, or a different form).
    """
    t = re.sub(r"[ \t]+", " ", text or "")
    if not t.strip():
        return {}
    out: Dict = {"allotment_date": "", "allotments": [], "total_shares_after": None}

    # Section 2: "From Date dd/mm/yyyy" (a range when shares were allotted
    # over several days; the FROM date is the round's date).
    m = re.search(r"From\s*(?:Date)?\s*:?\s*" + _DATE, t, re.I)
    if not m:
        m = re.search(r"allot(?:ment|ted)\s+(?:date|on)\s*:?\s*" + _DATE, t, re.I)
    if m:
        out["allotment_date"] = _iso(m.group(1), m.group(2), m.group(3))

    # Section 3: one block per class. Split on "Class of shares" and read the
    # three labelled figures inside each block.
    blocks = re.split(r"Class\s+of\s+shares?", t, flags=re.I)
    for blk in blocks[1:]:
        head = blk[:400]
        cls = re.match(r"\s*(?:allotted)?\s*:?\s*([A-Za-z0-9 \-'&/]+?)(?=\s+(?:Currency|Number|Nominal|$))", head, re.I)
        share_class = (cls.group(1).strip().title() if cls else "Ordinary")
        cur = re.search(r"Currency\s*:?\s*([A-Z]{3})", blk)
        n = re.search(r"Number\s+(?:of\s+shares\s+)?allotted\s*:?\s*" + _NUM, blk, re.I)
        nom = re.search(r"Nominal\s+value\s+(?:of\s+)?(?:each\s+)?share\s*:?\s*(?:[A-Z]{3}\s*)?" + _NUM, blk, re.I)
        paid = re.search(r"Amount\s+paid\s*\(including\s+(?:any\s+)?share\s+premium\)\s*(?:on\s+each\s+share)?\s*:?\s*(?:[A-Z]{3}\s*)?" + _NUM, blk, re.I)
        if not paid:
            paid = re.search(r"Amount\s+paid\s+(?:on\s+each\s+share)?\s*:?\s*(?:[A-Z]{3}\s*)?" + _NUM, blk, re.I)
        shares = _f(n.group(1)) if n else None
        if not shares:
            continue
        out["allotments"].append({
            "share_class": share_class[:40],
            "currency": (cur.group(1) if cur else "GBP"),
            "shares": int(shares),
            "nominal": _f(nom.group(1)) if nom else None,
            "paid": _f(paid.group(1)) if paid else None,
        })

    # Section 4: the statement of capital's total after the allotment.
    m = re.search(r"Total\s+number\s+of\s+shares\s*:?\s*" + _NUM, t, re.I)
    if m:
        out["total_shares_after"] = int(_f(m.group(1)) or 0) or None

    return out if out["allotments"] else {}


# ── Pure: the ladder from a list of read filings ─────────────────────────────

def _round_from(reading: Dict, filing: Dict) -> Dict:
    """One filing's reading -> one round row. Nominal issues are marked. The
    reading itself travels with the row, so the ladder can always be rebuilt
    from what the filing said rather than from a derived number."""
    allots = reading.get("allotments") or []
    shares = sum(int(a.get("shares") or 0) for a in allots)
    priced = [a for a in allots if a.get("paid") is not None]
    raised = sum(int(a["shares"]) * float(a["paid"]) for a in priced)
    price = (raised / sum(int(a["shares"]) for a in priced)) if priced and shares else None
    nominal_issue = False
    if price is not None:
        noms = [a.get("nominal") for a in priced if a.get("nominal") is not None]
        top_nominal = max(noms) if noms else None
        if price <= 0 or (top_nominal is not None and price <= top_nominal * 1.01):
            nominal_issue = True
    total_after = reading.get("total_shares_after")
    rd = {
        "date": reading.get("allotment_date") or filing.get("date") or "",
        "filed": filing.get("date") or "",
        "transaction_id": filing.get("transaction_id") or "",
        "shares": shares,
        "classes": sorted({a.get("share_class") or "Ordinary" for a in allots}),
        "price": round(price, 4) if price is not None else None,
        "raised": round(raised, 2) if priced else None,
        "total_shares_after": total_after,
        "kind": "nominal issue" if nominal_issue else ("equity round" if price is not None else "allotment (price not stated)"),
        "source": "ai" if reading.get("_source") == "ai" else "text",
        "reading": {"allotment_date": reading.get("allotment_date") or "", "allotments": allots,
                    "total_shares_after": total_after, "_source": reading.get("_source") or "text"},
    }
    if not nominal_issue and price and total_after:
        rd["post_money"] = round(total_after * price, 2)
        rd["pre_money"] = round(total_after * price - raised, 2)
    return rd


def build_ladder(readings: List[Dict], filings: List[Dict]) -> Dict:
    """readings[i] is parse_sh01_text (or the AI fallback) for filings[i]."""
    rounds = [_round_from(r, f) for r, f in zip(readings, filings) if r]
    rounds.sort(key=lambda r: (r["date"], r["filed"]))
    cum, prev_price, n = 0.0, None, 0
    for r in rounds:
        if r["kind"] == "equity round" and r.get("raised"):
            n += 1
            r["round_no"] = n
            cum += r["raised"]
            if prev_price:
                step = (r["price"] - prev_price) / prev_price
                r["vs_prior"] = "up" if step > 0.05 else ("down" if step < -0.05 else "flat")
                r["vs_prior_pct"] = round(step * 100, 1)
            prev_price = r["price"]
        r["cumulative_raised"] = round(cum, 2)
    equity = [r for r in rounds if r["kind"] == "equity round"]
    last = equity[-1] if equity else None
    return {
        "v": LEDGER_VERSION,
        "rounds": rounds,
        "equity_rounds": len(equity),
        "total_raised": round(cum, 2),
        "last_round": last,
        "filings_seen": sorted({r["transaction_id"] for r in rounds if r.get("transaction_id")}),
    }


def merge_ledger(stored: Optional[Dict], new_readings: List[Dict], new_filings: List[Dict]) -> Dict:
    """Extend a stored ledger with newly read filings. A filing already in
    the ledger is never re-read or duplicated; the whole ladder (numbering,
    cumulative totals, up/down steps) is rebuilt from every filing's reading."""
    stored = stored or {}
    new_ids = {f.get("transaction_id") for f in new_filings}
    kept = [r for r in (stored.get("rounds") or []) if r.get("reading") and r.get("transaction_id") not in new_ids]
    readings = [r["reading"] for r in kept] + list(new_readings)
    filings = [{"date": r.get("filed") or "", "transaction_id": r.get("transaction_id") or ""} for r in kept] + list(new_filings)
    return build_ladder(readings, filings)


def column_fills(ledger: Dict, row: Dict) -> Dict:
    """Fill-only suggestions for the legacy financing columns: a number the
    row already holds (Gain, PitchBook, a founder's document) is never
    replaced by a derivation. Values in GBP millions where the column says so."""
    fills: Dict = {}
    if not ledger or not ledger.get("equity_rounds"):
        return fills
    last = ledger.get("last_round") or {}
    if row.get("total_raised_m") in (None, 0, 0.0) and ledger.get("total_raised"):
        fills["total_raised_m"] = round(ledger["total_raised"] / 1e6, 2)
    if not row.get("last_financing_date") and last.get("date"):
        fills["last_financing_date"] = last["date"]
    if row.get("last_financing_size_m") in (None, 0, 0.0) and last.get("raised"):
        fills["last_financing_size_m"] = round(last["raised"] / 1e6, 2)
    if not row.get("last_financing_type") and last:
        fills["last_financing_type"] = "Equity (SH01 allotment)"
    if row.get("last_financing_valuation_m") in (None, 0, 0.0) and last.get("post_money"):
        fills["last_financing_valuation_m"] = round(last["post_money"] / 1e6, 2)
    if not row.get("last_valuation_date") and last.get("post_money"):
        fills["last_valuation_date"] = last["date"]
    return fills


# ── I/O: read the filings not yet in the ledger ──────────────────────────────

def _pdf_text(pdf: bytes) -> str:
    try:
        import fitz  # pymupdf
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception as e:
        logger.warning(f"[Ladder] PDF text extraction failed: {e}")
        return ""


def _ai_read(pdf: bytes, company_name: str) -> Dict:
    """Fallback for a scanned SH01: one ungrounded Gemini call, structured."""
    import base64
    import os
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return {}
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""This is a UK Companies House SH01 (return of allotment of shares) for {company_name}.
Read the form's own figures. NEVER guess. Return ONLY JSON:
{{"allotment_date": "YYYY-MM-DD or null",
  "allotments": [{{"share_class": "...", "currency": "GBP", "shares": number, "nominal": number or null, "paid": number or null}}],
  "total_shares_after": number or null}}
"paid" is the amount paid (including share premium) on EACH share; "total_shares_after" is the
total number of shares in the statement of capital after the allotment."""
        resp = model.generate_content(
            [{"mime_type": "application/pdf", "data": base64.b64encode(pdf).decode()}, prompt],
            generation_config={"response_mime_type": "application/json"})
        text = (resp.text or "").strip()
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        allots = [a for a in (data.get("allotments") or []) if isinstance(a, dict) and (a.get("shares") or 0) > 0]
        if not allots:
            return {}
        return {"allotment_date": data.get("allotment_date") or "", "allotments": allots,
                "total_shares_after": data.get("total_shares_after"), "_source": "ai"}
    except Exception as e:
        logger.warning(f"[Ladder] AI read failed for {company_name}: {e}")
        return {}


def get_funding_ladder(company_number: str, company_name: str, stored_json: str = "") -> Dict:
    """Read every SH01 not yet in the stored ledger and return the merged
    ledger, plus {"read": n, "ai_reads": n, "skipped": bool}."""
    from services.companies_house_service import _fetch_filing_history, _download_accounts_pdf
    try:
        stored = json.loads(stored_json) if stored_json else {}
    except Exception:
        stored = {}
    seen = set(stored.get("filings_seen") or [])
    filings = [f for f in _fetch_filing_history(company_number, category="capital", items=60)
               if ("allotment" in (f.get("description") or "").lower()
                   or (f.get("type") or "").upper().startswith("SH01"))]
    todo = [f for f in filings if f.get("transaction_id") and f["transaction_id"] not in seen]
    if not todo:
        return {"ledger": stored, "read": 0, "ai_reads": 0, "skipped": True}
    todo.sort(key=lambda f: f.get("date") or "", reverse=True)   # newest first: the recent rounds matter most
    readings, done_filings, reads, ai_reads = [], [], 0, 0
    for f in todo[:MAX_PDF_READS_PER_RUN]:
        pdf = _download_accounts_pdf(f)
        if not pdf:
            continue
        reads += 1
        r = parse_sh01_text(_pdf_text(pdf))
        if not r and ai_reads < MAX_AI_FALLBACKS_PER_RUN:
            ai_reads += 1
            r = _ai_read(pdf, company_name)
        if not r:
            continue
        readings.append(r)
        done_filings.append({"date": f.get("date") or "", "transaction_id": f["transaction_id"]})
    ledger = merge_ledger(stored, readings, done_filings)
    ledger["parsed_at"] = date.today().isoformat()
    ledger["pending"] = max(0, len(todo) - len(done_filings))
    logger.info(f"[Ladder] {company_name}: {len(done_filings)} filing(s) read ({ai_reads} via AI), "
                f"{ledger.get('equity_rounds', 0)} equity rounds, total raised {ledger.get('total_raised')}")
    return {"ledger": ledger, "read": len(done_filings), "ai_reads": ai_reads, "skipped": False}
