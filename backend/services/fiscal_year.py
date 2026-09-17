"""
Fiscal years: ONE convention, ONE window, ONE parser. PURE (no I/O).

THE CONVENTION (Ishu, 17 Sep 2026: "correctly tag which year's data has been
uploaded, correctly match it with the financial year with the Companies House
accounts"): a fiscal year is named after the CALENDAR YEAR IN WHICH THE
ACCOUNTING PERIOD ENDS. A year to 31 March 2026 is FY26; a year to 31
December 2025 is FY25; a year to 30 June 2024 is FY24. That is how Companies
House filings, founders' decks and the data vendors all speak, and it is the
only rule under which a Gain row saying "FY2025" and a Companies House period
ending 2025-03-31 land in the same column.

THE WINDOW is five years ending in the CURRENT calendar year: FY22 to FY26 in
2026, rolling to FY23 to FY27 on 1 January. The last column is almost always
EMPTY, on purpose: it is the running year (or, for a March year end, the year
whose accounts are not yet filed), and the card shows it as a placeholder so
the reader sees "nothing filed yet" instead of a table that silently ends a
year early.

PLACING A LABEL. Imports carry years as text, not dates: Gain writes
"FY2025 (Gain, reported)", Inven writes "FY2024" and "latest (Inven)". A year
with no day is placed at the company's OWN year end when Companies House has
told us what it is (the most common month/day across its filed periods), and
at 31 December otherwise, with the assumption recorded in the cell's
evidence. A label with no year at all ("latest") is NOT placed: a guessed
year is a wrong year with a confident face, and a figure we cannot date stays
on the record but out of the year columns.
"""
import re
from collections import Counter
from datetime import date
from typing import Dict, List, Optional, Tuple

WINDOW_YEARS = 5

_ISO = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?")
_FY = re.compile(r"\bFY\s?'?(\d{2}|\d{4})\b", re.I)
_YEAR_ONLY = re.compile(r"^(\d{4})$")
_MONTH_DAYS = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def fiscal_year(period_end: Optional[str]) -> Optional[int]:
    """The FY a period end belongs to: the calendar year it ends in."""
    m = _ISO.match((period_end or "").strip())
    return int(m.group(1)) if m else None


def fy_label(fy: Optional[int]) -> str:
    """2026 -> 'FY26'."""
    return f"FY{fy % 100:02d}" if fy else ""


def current_fiscal_year(today: Optional[date] = None) -> int:
    return (today or date.today()).year


def fiscal_window(today: Optional[date] = None, years: int = WINDOW_YEARS) -> List[int]:
    """[FY22, FY23, FY24, FY25, FY26] in 2026. The last entry is the running year."""
    cur = current_fiscal_year(today)
    return list(range(cur - years + 1, cur + 1))


def parse_fy_label(label) -> Optional[int]:
    """The fiscal year named in a stored date label, or None.

    '2025-03-31' -> 2025   'FY2025 (Gain, reported)' -> 2025   'FY24' -> 2024
    '2024' -> 2024         'latest (Inven)' -> None              '' -> None
    """
    s = str(label or "").strip()
    if not s:
        return None
    m = _ISO.match(s)
    if m:
        return int(m.group(1))
    m = _YEAR_ONLY.match(s)
    if m:
        return int(m.group(1))
    m = _FY.search(s)
    if m:
        y = int(m.group(1))
        return y if y >= 1000 else 2000 + y
    return None


def year_end_from_periods(period_ends) -> Optional[Tuple[int, int]]:
    """(month, day) the company closes its books on, from its filed period
    ends: the most common month/day, latest period breaking a tie. None when
    nothing dated is held."""
    seen = []
    for p in period_ends or []:
        m = _ISO.match((p or "").strip())
        if m and m.group(3):
            seen.append((int(m.group(2)), int(m.group(3)), p))
    if not seen:
        return None
    counts = Counter((mo, d) for mo, d, _ in seen)
    latest = {(mo, d): max(p for m2, d2, p in seen if (m2, d2) == (mo, d)) for mo, d in counts}
    best = max(counts, key=lambda k: (counts[k], latest[k]))
    return best


def year_end_of(row: Dict) -> Optional[Tuple[int, int]]:
    """The company's year end from what Companies House has told us: the
    ch_history periods first, then the dated legacy slots. Never from an
    import label (those carry no day)."""
    import json
    periods: List[str] = []
    h = row.get("ch_history")
    if h:
        try:
            data = json.loads(h) if isinstance(h, str) else h
            periods += [y.get("period_end") for y in (data or {}).get("years", []) if y.get("period_end")]
        except Exception:
            pass
    for col in ("revenue_y1_date", "revenue_y2_date", "revenue_y3_date", "profit_y1_date"):
        v = row.get(col)
        if v and _ISO.match(str(v).strip()) and _ISO.match(str(v).strip()).group(3):
            periods.append(str(v).strip())
    return year_end_from_periods(periods)


def period_end_for_fy(fy: int, year_end: Optional[Tuple[int, int]] = None) -> str:
    """'YYYY-MM-DD' for a fiscal year: the company's year end when known,
    else 31 December. 29 February in a non-leap year falls back to the 28th."""
    mo, d = year_end or (12, 31)
    d = min(d, _MONTH_DAYS.get(mo, 31))
    if mo == 2 and d == 29 and not (fy % 4 == 0 and (fy % 100 != 0 or fy % 400 == 0)):
        d = 28
    return f"{fy:04d}-{mo:02d}-{d:02d}"


def place_label(label, row: Optional[Dict] = None) -> Tuple[Optional[str], str]:
    """(period_end, note) for a stored date label.

    An ISO date is itself (note ''). A label with a year but no day is placed
    at the company's year end or 31 December (note says which). A label with
    no year returns (None, reason) and must NOT be stored as a dated cell.
    """
    s = str(label or "").strip()
    m = _ISO.match(s)
    if m and m.group(3):
        return s[:10], ""
    fy = parse_fy_label(s)
    if fy is None:
        return None, f"no fiscal year stated in '{s}'" if s else "no date on the record"
    ye = year_end_of(row or {})
    pe = period_end_for_fy(fy, ye)
    how = f"company year end {pe[5:]} from Companies House" if ye else "year end assumed 31 Dec"
    return pe, f"{s} placed at {pe} ({how})"


def fy_status(fy: int, year_end: Optional[Tuple[int, int]] = None, today: Optional[date] = None) -> str:
    """'closed' (period ended and accounts could exist), 'running' (the period
    has not ended yet). Used for the placeholder text on the card."""
    today = today or date.today()
    pe = period_end_for_fy(fy, year_end)
    return "running" if pe > today.isoformat() else "closed"
