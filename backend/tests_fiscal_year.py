#!/usr/bin/env python3
"""
Fiscal years: one convention (FY = calendar year the period ends in), one
window (five years ending in the current calendar year), and import labels
placed at the company's own year end. Ishu, 17 Sep 2026: "FY22 to FY26 ...
make sure all the data is correctly mapped to the correct years."
"""
import json
import os
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services import fiscal_year as fy  # noqa: E402
from services.doc_smartfill import cells_from_columns, cells_from_history, cells_from_record  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── The convention: the year the period ENDS in ──")
chk("31 Mar 2026 is FY26", fy.fiscal_year("2026-03-31"), 2026)
chk("31 Dec 2025 is FY25", fy.fiscal_year("2025-12-31"), 2025)
chk("30 Jun 2024 is FY24", fy.fiscal_year("2024-06-30"), 2024)
chk("label", fy.fy_label(2026), "FY26")
chk("label 2009", fy.fy_label(2009), "FY09")
chk("no date, no year", fy.fiscal_year(""), None)

print()
print("── The window: five years ending in the current calendar year ──")
chk("2026 -> FY22..FY26", fy.fiscal_window(date(2026, 9, 17)), [2022, 2023, 2024, 2025, 2026])
chk("rolls on 1 Jan", fy.fiscal_window(date(2027, 1, 1)), [2023, 2024, 2025, 2026, 2027])
chk("a Dec year end: FY26 is still running on 17 Sep 2026", fy.fy_status(2026, None, date(2026, 9, 17)), "running")
chk("a Mar year end: FY26 closed on 31 Mar 2026", fy.fy_status(2026, (3, 31), date(2026, 9, 17)), "closed")
chk("FY25 is closed either way", fy.fy_status(2025, None, date(2026, 9, 17)), "closed")

print()
print("── Reading the year out of a stored label ──")
for label, want in (("2025-03-31", 2025), ("FY2025 (Gain, reported)", 2025), ("FY2024", 2024), ("FY24", 2024),
                    ("fy 25", 2025), ("2024", 2024), ("2025-03", 2025), ("latest (Inven)", None), ("", None),
                    ("Latest", None), (None, None)):
    chk(f"{label!r} -> {want}", fy.parse_fy_label(label), want)

print()
print("── The company's year end comes from Companies House, never from an import label ──")
hist = json.dumps({"years": [{"period_end": "2025-03-31"}, {"period_end": "2024-03-31"}, {"period_end": "2023-03-31"}]})
chk("most common month/day across the filed periods", fy.year_end_of({"ch_history": hist}), (3, 31))
chk("a changed year end: the majority wins, latest breaks a tie",
    fy.year_end_from_periods(["2025-06-30", "2024-03-31", "2023-06-30", "2022-03-31"]), (6, 30))
chk("dated legacy slots count too", fy.year_end_of({"revenue_y1_date": "2024-09-30"}), (9, 30))
chk("an import label carries no day and gives no year end", fy.year_end_of({"revenue_y1_date": "FY2025 (Gain, reported)"}), None)
chk("nothing dated -> None", fy.year_end_of({}), None)

print()
print("── Placing a label in a period ──")
chk("ISO date is itself", fy.place_label("2024-03-31", {}), ("2024-03-31", ""))
pe, note = fy.place_label("FY2025 (Gain, reported)", {"ch_history": hist})
chk("Gain FY2025 lands on the company's 31 March year end, same column as the CH filing", pe, "2025-03-31")
chk("...and the evidence says how it was placed", "year end 03-31 from Companies House" in note)
pe, note = fy.place_label("FY2024", {})
chk("no year end known -> 31 December", pe, "2024-12-31")
chk("...flagged as an assumption", "assumed 31 Dec" in note)
chk("no year in the label -> NOT placed", fy.place_label("latest (Inven)", {})[0], None)
chk("29 Feb year end in a non-leap year", fy.period_end_for_fy(2025, (2, 29)), "2025-02-28")
chk("29 Feb year end in a leap year", fy.period_end_for_fy(2024, (2, 29)), "2024-02-29")

print()
print("── Seeding the store from the record ──")
gain = {"name": "G", "revenue_y1": 4_400_000, "revenue_y1_date": "FY2025 (Gain, reported)", "estimated_ebitda": 0.6}
cells = cells_from_columns(gain, "Record (import)")
chk("Gain revenue placed in FY25 (31 Dec assumed)", [(c["period_end"], c["metric"], c["value"]) for c in cells if c["metric"] == "revenue"],
    [("2025-12-31", "revenue", 4_400_000.0)])
chk("Gain reported EBITDA placed in the same year, GBP M -> GBP",
    [(c["period_end"], c["value"]) for c in cells if c["metric"] == "ebitda"], [("2025-12-31", 600_000.0)])
chk("evidence records the placement", all("placed at 2025-12-31" in c["evidence"] or "Gain reported EBITDA" in c["evidence"] for c in cells))
inven = {"name": "I", "revenue_y1": 5_000_000, "revenue_y1_date": "latest (Inven)", "estimated_ebitda": 1.0,
         "revenue_y2": 4_000_000, "revenue_y2_date": "FY2024", "revenue_y3": 3_000_000, "revenue_y3_date": "FY2023"}
cells = cells_from_columns(inven, "Record (import)")
chk("Inven 'latest' has no year and is NOT placed; FY2024 and FY2023 are",
    sorted((c["period_end"], c["metric"]) for c in cells), [("2023-12-31", "revenue"), ("2024-12-31", "revenue")])
excel = {"name": "E", "revenue_y1": 2_000_000, "revenue_y1_date": "2025-03-31", "estimated_ebitda": 3.5}
chk("the EBITDA column is NOT placed for a non-Gain row (the Excel upload stores a revenue estimate there)",
    [c for c in cells_from_columns(excel, "Companies House") if c["metric"] == "ebitda"], [])

ch_row = {"name": "C", "ch_company_number": "01234567",
          "revenue_y1": 9_500_000, "revenue_y1_date": "2025-03-31", "revenue_y2": 8_000_000, "revenue_y2_date": "2024-03-31",
          "revenue_y3": 7_000_000, "revenue_y3_date": "2023-03-31",
          "ch_history": json.dumps({"v": 1, "years": [
              {"period_end": "2025-03-31", "revenue": 9_500_000, "profit": -200_000, "employees": 10},
              {"period_end": "2024-03-31", "revenue": 8_000_000, "profit": 100_000},
              {"period_end": "2023-03-31", "revenue": 7_000_000},
              {"period_end": "2022-03-31", "revenue": 5_000_000, "cash": 0},
              {"period_end": "2021-03-31", "revenue": 3_000_000},
              {"period_end": "2020-03-31", "revenue": 1_000_000}]})}
hc = cells_from_history(ch_row)
chk("every parsed CH period becomes cells, six years not three",
    sorted({c["period_end"] for c in hc}), ["2020-03-31", "2021-03-31", "2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31"])
chk("profit -> profit_before_tax, negative kept", next(c["value"] for c in hc if c["metric"] == "profit_before_tax" and c["period_end"] == "2025-03-31"), -200_000.0)
chk("employees unit is count", next(c["unit"] for c in hc if c["metric"] == "employees"), "count")
chk("a zero cash figure is dropped (unknown, not nil)", not any(c["metric"] == "cash" for c in hc))
rec = cells_from_record(ch_row, "Companies House")
chk("record = history + columns, one cell per (period, metric)",
    len(rec), len({(c["period_end"], c["metric"]) for c in rec}))
chk("FY22 revenue reaches the store for the five-year view", any(c["period_end"] == "2022-03-31" and c["metric"] == "revenue" for c in rec))
chk("a Gain label on a CH company lands on the CH year end (same column, so the filing wins the fill-only merge)",
    fy.place_label("FY2025 (Gain, reported)", ch_row)[0], "2025-03-31")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
