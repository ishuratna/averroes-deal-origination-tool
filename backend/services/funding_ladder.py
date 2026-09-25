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

THREE MORE RULES, learnt on Arcus Global's real filings (25 Sep 2026):
  * A SMALL ISSUE. 9,000 shares at GBP 1 (Nov 2019) and 333,083 shares at
    GBP 0.10 (Mar 2026, against a last round at GBP 3.23) are option
    exercises priced above nominal. Neither is a fundraising, and the first
    had filled `last_financing_valuation_m` with 2.25. So an allotment that
    raises under `SMALL_ISSUE_GBP`, or is priced under `OPTION_PRICE_RATIO`
    of the last equity round's price, is a "small issue": listed, never a
    round, never a valuation.
  * A DUPLICATE FILING. The May 2019 round was filed twice (29 May and
    5 Jun, the same allotment re-submitted); read once each, it doubled the
    money raised. Readings with the same date, shares and price are one
    allotment; the later filing is kept as a note on the first.
  * THE E-FILED LAYOUT. "Number allotted" is also the label the statement
    of capital uses for each class's total in issue, so section 3 must stop
    at "Statement of Capital" or every class in issue reads as an allotment
    with no price (2.3m shares "allotted" in 2026, 333,083 really were).

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

LEDGER_VERSION = 4             # 2: small-issue rule, duplicate fold, e-filed layout; 3: total consistency, price rule only for small money; 4: capital-after from the filing list, reductions
MAX_PDF_READS_PER_RUN = 8      # text extraction is free; this bounds the downloads
MAX_AI_FALLBACKS_PER_RUN = 3   # a scanned SH01 costs one ungrounded Gemini call
SMALL_ISSUE_GBP = 50_000       # under this, an allotment is not a fundraising
OPTION_PRICE_RATIO = 0.25      # priced under a quarter of the last round: an option exercise ...
OPTION_EXERCISE_MAX_GBP = 250_000   # ... but only for small money; more than this is a down round


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
    # The e-filed rendition (SH01(ef)) puts every label and its value on
    # separate lines; the paper form runs them along a line. Folding all
    # whitespace to one space reads both the same way.
    t = re.sub(r"\s+", " ", text or "")
    if not t.strip():
        return {}
    out: Dict = {"allotment_date": "", "allotments": [], "total_shares_after": None}

    # Section 2: "From Date dd/mm/yyyy" (a range when shares were allotted
    # over several days; the FROM date is the round's date). The e-filed
    # layout prints the labels "From" and "To" before either date.
    m = re.search(r"From\s*(?:Date)?\s*:?\s*(?:To\s*(?:Date)?\s*:?\s*)?" + _DATE, t, re.I)
    if not m:
        m = re.search(r"allot(?:ment|ted)\s+(?:date|on)\s*:?\s*" + _DATE, t, re.I)
    if m:
        out["allotment_date"] = _iso(m.group(1), m.group(2), m.group(3))

    # Section 3 ends where the statement of capital begins. The statement
    # lists every class IN ISSUE under the same "Number allotted" label the
    # e-filed form uses for the allotment, so it must be cut off first.
    cut = re.search(r"Statement\s+of\s+Capital", t, re.I)
    section3, section4 = (t[:cut.start()], t[cut.start():]) if cut else (t, t)

    # One block per class. Split on "Class of shares" and read the three
    # labelled figures inside each block.
    blocks = re.split(r"Class\s+of\s+shares?", section3, flags=re.I)
    for blk in blocks[1:]:
        head = blk[:400]
        cls = re.match(r"\s*(?:allotted)?\s*:?\s*([A-Za-z0-9 \-'&/]+?)(?=\s+(?:Currency|Number|Nominal|$))", head, re.I)
        share_class = (cls.group(1).strip().title() if cls else "Ordinary")
        cur = re.search(r"Currency\s*:?\s*([A-Z]{3})", blk)
        n = re.search(r"Number\s+(?:of\s+shares\s+)?allotted\s*:?\s*" + _NUM, blk, re.I)
        nom = re.search(r"Nominal\s+value\s+(?:of\s+)?(?:each\s+)?share\s*:?\s*(?:[A-Z]{3}\s*)?" + _NUM, blk, re.I)
        paid = re.search(r"Amount\s+paid\s*(?:\(including\s+(?:any\s+)?share\s+premium\))?\s*(?:on\s+each\s+share)?\s*:?\s*(?:[A-Z]{3}\s*)?" + _NUM, blk, re.I)
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
    m = re.search(r"Total\s+number\s+of\s+shares\s*:?\s*" + _NUM, section4, re.I)
    if m:
        out["total_shares_after"] = int(_f(m.group(1)) or 0) or None

    return out if out["allotments"] else {}


# ── Pure: the ladder from a list of read filings ─────────────────────────────

def capital_from_description(description: str) -> Optional[float]:
    """Companies House prints the aggregate NOMINAL capital after the event
    in the filing description: "Statement of capital following an allotment
    of shares on 11 May 2018  GBP 739.9635". Free, on the filing list, and
    the statement of capital in one number: at a nominal of 0.0005 that is
    1,479,927 shares. Two SH01s with the same figure are the same statement
    re-filed (Arcus, 14 May and 10 Jul 2018; 29 May and 5 Jun 2019)."""
    m = re.search(r"\bGBP\s*([0-9][0-9,]*(?:\.[0-9]+)?)", description or "")
    return _f(m.group(1)) if m else None


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
    # The filing list's "GBP x" is the aggregate nominal capital after the
    # allotment. With the nominal from the form it gives the total in issue
    # without reading the statement of capital, and it corrects a misread one.
    cap = filing.get("capital_after")
    noms_all = [a.get("nominal") for a in allots if a.get("nominal")]
    implied = None
    if cap and noms_all and len(set(noms_all)) == 1:
        implied = int(round(cap / noms_all[0]))
        if not total_after or abs(total_after - implied) > max(2, 0.005 * implied):
            total_after = implied
    rd = {
        "date": reading.get("allotment_date") or filing.get("date") or "",
        "filed": filing.get("date") or "",
        "transaction_id": filing.get("transaction_id") or "",
        "capital_after": cap,
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
    # A total in issue smaller than the shares just allotted is a misread
    # (or a statement for one class only): no valuation can rest on it.
    if total_after and shares and total_after < shares:
        rd["total_shares_after"] = None
        rd["note"] = f"statement of capital ({total_after:,}) below shares allotted; valuation not derived"
        total_after = None
    if implied and reading.get("total_shares_after") and total_after == implied and implied != reading.get("total_shares_after"):
        rd["note"] = f"statement of capital read as {reading['total_shares_after']:,}; the filing list's capital says {implied:,}, used"
    if not nominal_issue and price and total_after:
        rd["post_money"] = round(total_after * price, 2)
        rd["pre_money"] = round(total_after * price - raised, 2)
    return rd


def _fold_duplicates(rounds: List[Dict]) -> List[Dict]:
    """The same allotment filed twice is one allotment. Same capital after
    (the filing list's figure), or same date, shares and price: keep the
    first filing, note the second on it."""
    out: List[Dict] = []
    for r in rounds:
        key = (r["date"], r["shares"], r.get("price"))
        twin = next((o for o in out if (o.get("capital_after") and o.get("capital_after") == r.get("capital_after"))
                     or (o["date"], o["shares"], o.get("price")) == key), None)
        if twin is not None and r.get("shares"):
            twin.setdefault("duplicate_filings", []).append({"filed": r.get("filed"), "transaction_id": r.get("transaction_id"),
                                                             "capital_after": r.get("capital_after")})
            twin["note"] = "filed twice (" + ", ".join(d["filed"] for d in twin["duplicate_filings"] if d.get("filed")) + "); counted once"
            continue
        out.append(r)
    return out


def build_ladder(readings: List[Dict], filings: List[Dict], reductions: Optional[List[Dict]] = None) -> Dict:
    """readings[i] is parse_sh01_text (or the AI fallback) for filings[i].
    `reductions` are capital reductions from the filing list ({"date",
    "capital"} from an SH19 / RES13): after one, the total in issue may
    lawfully fall (Arcus, Jan 2024: 2,251,604 -> 1,662,682)."""
    rounds = [_round_from(r, f) for r, f in zip(readings, filings) if r]
    # Same-day allotments order by the capital after each (the filing list's
    # figure), so two SH01s dated 11 May 2018 read in the order they happened.
    rounds.sort(key=lambda r: (r["date"], r.get("capital_after") or 0, r["filed"]))
    all_ids = sorted({r["transaction_id"] for r in rounds if r.get("transaction_id")})
    rounds = _fold_duplicates(rounds)
    # A statement of capital that shows FEWER shares in issue than an earlier
    # filing did is a misread, unless a capital reduction sits between: the
    # money still counts, the valuation does not.
    cuts = sorted([x for x in (reductions or []) if x.get("date")], key=lambda x: x["date"])
    high, hi_date = 0, ""
    for r in rounds:
        t = r.get("total_shares_after")
        if t:
            if any(hi_date < c["date"] <= r["date"] for c in cuts):
                high = 0   # a reduction between the last filing and this one resets the floor
            if t < high:
                r["note"] = f"statement of capital ({t:,}) below an earlier filing's ({high:,}); valuation not derived"
                r.pop("post_money", None)
                r.pop("pre_money", None)
                r["total_inconsistent"] = True
            else:
                high, hi_date = t, r["date"]
    cum, prev_price, n = 0.0, None, 0
    for r in rounds:
        if r["kind"] == "equity round" and r.get("raised"):
            # The small-issue rule: an option exercise is priced above nominal
            # but far below the last round, and raises very little. Neither
            # is a fundraising and neither may set a valuation. The price
            # test applies only to small money: GBP 2.5m at a fifth of the
            # last price is a down round (or a misread of the last price),
            # never an option exercise.
            if r["raised"] <= SMALL_ISSUE_GBP:
                r["kind"] = "small issue"
                r["note"] = f"raised GBP {SMALL_ISSUE_GBP:,} or less; not counted as a round"
            elif prev_price and r["price"] < prev_price * OPTION_PRICE_RATIO and r["raised"] < OPTION_EXERCISE_MAX_GBP:
                r["kind"] = "small issue"
                r["note"] = f"priced at {r['price']:.2f} against a last round at {prev_price:.2f}; option exercise, not a round"
            if r["kind"] == "small issue":
                r.pop("post_money", None)
                r.pop("pre_money", None)
                r["cumulative_raised"] = round(cum, 2)
                continue
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
        "filings_seen": all_ids,
        "reductions": cuts,
    }


def merge_ledger(stored: Optional[Dict], new_readings: List[Dict], new_filings: List[Dict],
                 reductions: Optional[List[Dict]] = None, capital_by_id: Optional[Dict[str, float]] = None) -> Dict:
    """Extend a stored ledger with newly read filings. A filing already in
    the ledger is never re-read or duplicated; the whole ladder (numbering,
    cumulative totals, up/down steps) is rebuilt from every filing's reading.
    A filing folded into another as a duplicate keeps its own reading, so
    the fold is re-decided on every rebuild rather than stored.
    `capital_by_id` (transaction id -> capital after, from the filing list)
    lets a stored round that predates the figure pick it up."""
    stored = stored or {}
    caps = capital_by_id or {}
    new_ids = {f.get("transaction_id") for f in new_filings}
    readings, filings = [], []
    for r in (stored.get("rounds") or []):
        if not r.get("reading") or r.get("transaction_id") in new_ids:
            continue
        tid = r.get("transaction_id") or ""
        readings.append(r["reading"])
        filings.append({"date": r.get("filed") or "", "transaction_id": tid,
                        "capital_after": r.get("capital_after") or caps.get(tid)})
        for d in r.get("duplicate_filings") or []:
            if d.get("transaction_id") and d["transaction_id"] not in new_ids:
                readings.append(r["reading"])
                filings.append({"date": d.get("filed") or "", "transaction_id": d["transaction_id"],
                                "capital_after": d.get("capital_after") or caps.get(d["transaction_id"])})
    out = build_ladder(readings + list(new_readings), filings + list(new_filings),
                       reductions=reductions if reductions is not None else stored.get("reductions"))
    if stored.get("fills"):
        out["fills"] = stored["fills"]
    return out


def column_fills(ledger: Dict, row: Dict) -> Dict:
    """Fill-only suggestions for the legacy financing columns: a number the
    row already holds (Gain, PitchBook, a founder's document) is never
    replaced by a derivation. Values in GBP millions where the column says so.

    OUR OWN EARLIER DERIVATION IS NOT SUCH A NUMBER. `ledger["fills"]`
    records what the ladder itself wrote last time; a column still holding
    exactly that value is treated as empty, so a corrected ladder can
    correct its own fill (Arcus: a GBP 9k option exercise had set
    `last_financing_valuation_m = 2.25`). A value anyone else wrote is left."""
    fills: Dict = {}
    if not ledger or not ledger.get("equity_rounds"):
        return fills
    ours = ledger.get("fills") or {}

    def empty(col):
        v = row.get(col)
        if v in (None, 0, 0.0, ""):
            return True
        if col in ours:
            try:
                return v == ours[col] or float(v) == float(ours[col])
            except (TypeError, ValueError):
                return v == ours[col]
        return False

    last = ledger.get("last_round") or {}
    if empty("total_raised_m") and ledger.get("total_raised"):
        fills["total_raised_m"] = round(ledger["total_raised"] / 1e6, 2)
    if empty("last_financing_date") and last.get("date"):
        fills["last_financing_date"] = last["date"]
    if empty("last_financing_size_m") and last.get("raised"):
        fills["last_financing_size_m"] = round(last["raised"] / 1e6, 2)
    if empty("last_financing_type") and last:
        fills["last_financing_type"] = "Equity (SH01 allotment)"
    if empty("last_financing_valuation_m") and last.get("post_money"):
        fills["last_financing_valuation_m"] = round(last["post_money"] / 1e6, 2)
    if empty("last_valuation_date") and last.get("post_money"):
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
  "allotments": [{{"share_class": "...", "currency": "GBP", "shares": number, "nominal": number or null, "paid": number or null,
                   "aggregate_paid": number or null}}],
  "total_shares_after": number or null}}
"paid" is the amount paid (including share premium) on EACH share, exactly as printed (it is often a
small decimal such as 1.6258, never the total). "aggregate_paid" is the TOTAL consideration for the
class if the form states one, else null. "total_shares_after" is the total number of shares in the
statement of capital after the allotment (the Totals line, all classes)."""
        resp = model.generate_content(
            [{"mime_type": "application/pdf", "data": base64.b64encode(pdf).decode()}, prompt],
            generation_config={"response_mime_type": "application/json"})
        text = (resp.text or "").strip()
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        allots = [a for a in (data.get("allotments") or []) if isinstance(a, dict) and (a.get("shares") or 0) > 0]
        if not allots:
            return {}
        for a in allots:
            # Cross-check: when the form states a total, shares x paid must
            # match it. A mismatch means "paid" was read as the total (or
            # the reverse); the stated total divided by the shares wins.
            agg, paid, sh = a.get("aggregate_paid"), a.get("paid"), a.get("shares")
            if isinstance(agg, (int, float)) and agg > 0 and sh:
                if not isinstance(paid, (int, float)) or abs(sh * paid - agg) > 0.05 * agg:
                    a["paid_as_read"] = paid
                    a["paid"] = round(agg / sh, 6)
        return {"allotment_date": data.get("allotment_date") or "", "allotments": allots,
                "total_shares_after": data.get("total_shares_after"), "_source": "ai"}
    except Exception as e:
        logger.warning(f"[Ladder] AI read failed for {company_name}: {e}")
        return {}


def get_funding_ladder(company_number: str, company_name: str, stored_json: str = "",
                       reread_ai: bool = False) -> Dict:
    """Read every SH01 not yet in the stored ledger and return the merged
    ledger, plus {"read": n, "ai_reads": n, "skipped": bool}. `reread_ai`
    discards the stored AI readings so the scans are read again (paid):
    for when a reading looks wrong against an independent source."""
    from services.companies_house_service import _fetch_filing_history, _download_accounts_pdf
    try:
        stored = json.loads(stored_json) if stored_json else {}
    except Exception:
        stored = {}
    seen = set(stored.get("filings_seen") or [])
    if reread_ai and stored:
        ai_ids = {r.get("transaction_id") for r in (stored.get("rounds") or [])
                  if (r.get("reading") or {}).get("_source") == "ai"}
        for r in (stored.get("rounds") or []):
            ai_ids |= {d.get("transaction_id") for d in (r.get("duplicate_filings") or [])}
        stored["rounds"] = [r for r in (stored.get("rounds") or []) if r.get("transaction_id") not in ai_ids]
        seen -= ai_ids
    upgrading = bool(stored) and (stored.get("v") or 1) < LEDGER_VERSION
    if upgrading:
        # The parser changed. A reading taken from the filing TEXT is re-read
        # (a download, no AI): the v1 parser read the 2026 statement of
        # capital as allotments and that reading must not survive a rebuild.
        # An AI reading is kept: paying again would buy the same answer.
        text_ids = {r.get("transaction_id") for r in (stored.get("rounds") or [])
                    if (r.get("reading") or {}).get("_source", "text") != "ai"}
        stored["rounds"] = [r for r in (stored.get("rounds") or []) if r.get("transaction_id") not in text_ids]
        seen -= text_ids
    if upgrading and "fills" not in stored:
        # A v1 ledger never recorded what it filled. Whatever v1's rules
        # would have written is ours to correct; a coincidence with a
        # vendor figure is the only way this is wrong, and it is rounded to
        # two decimals of a derived number.
        stored["fills"] = column_fills({**stored, "fills": {}}, {})
    history = _fetch_filing_history(company_number, category="capital", items=60)
    filings = [f for f in history
               if ("allotment" in (f.get("description") or "").lower()
                   or (f.get("type") or "").upper().startswith("SH01"))]
    for f in filings:
        f["capital_after"] = capital_from_description(f.get("description") or "")
    capital_by_id = {f["transaction_id"]: f["capital_after"] for f in filings if f.get("transaction_id") and f.get("capital_after")}
    # Capital reductions (SH19 statement of capital, RES13 resolution): after
    # one, a lower total in issue is lawful, not a misread.
    reductions = [{"date": f.get("date") or "", "capital": capital_from_description(f.get("description") or ""),
                   "type": f.get("type") or ""}
                  for f in history
                  if (f.get("type") or "").upper() == "SH19"
                  or "reduction" in (f.get("description") or "").lower()]
    todo = [f for f in filings if f.get("transaction_id") and f["transaction_id"] not in seen]
    if not todo:
        if stored and (stored.get("v") or 1) < LEDGER_VERSION:
            # Rules changed: rebuild from the stored readings, free.
            ledger = merge_ledger(stored, [], [], reductions=reductions, capital_by_id=capital_by_id)
            ledger["parsed_at"] = stored.get("parsed_at") or date.today().isoformat()
            ledger["pending"] = stored.get("pending", 0)
            return {"ledger": ledger, "read": 0, "ai_reads": 0, "skipped": False}
        return {"ledger": stored, "read": 0, "ai_reads": 0, "skipped": True}
    todo.sort(key=lambda f: f.get("date") or "", reverse=True)   # newest first: the recent rounds matter most
    # A filing whose capital-after matches one already read is the same
    # statement re-filed: it takes its twin's reading and costs nothing.
    known: Dict[float, Dict] = {}
    for r in (stored.get("rounds") or []):
        cap = r.get("capital_after") or capital_by_id.get(r.get("transaction_id") or "")
        if cap and r.get("reading"):
            known[cap] = r["reading"]
    readings, done_filings, reads, ai_reads = [], [], 0, 0
    for f in todo:
        fd = {"date": f.get("date") or "", "transaction_id": f["transaction_id"], "capital_after": f.get("capital_after")}
        cap = f.get("capital_after")
        if cap and cap in known:
            readings.append(dict(known[cap]))
            done_filings.append(fd)
            continue
        if reads >= MAX_PDF_READS_PER_RUN:
            continue
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
        if cap:
            known[cap] = r
        readings.append(r)
        done_filings.append(fd)
    ledger = merge_ledger(stored, readings, done_filings, reductions=reductions, capital_by_id=capital_by_id)
    ledger["parsed_at"] = date.today().isoformat()
    ledger["pending"] = max(0, len(todo) - len(done_filings))
    logger.info(f"[Ladder] {company_name}: {len(done_filings)} filing(s) read ({ai_reads} via AI), "
                f"{ledger.get('equity_rounds', 0)} equity rounds, total raised {ledger.get('total_raised')}")
    return {"ledger": ledger, "read": len(done_filings), "ai_reads": ai_reads, "skipped": False}
