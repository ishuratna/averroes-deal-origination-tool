#!/usr/bin/env python3
"""
Fit score v4 (Ishu, 19 Aug 2026): the size steps, the zero for declining
revenue, the CH employee-growth waterfall, and the zero-AI local rescore.
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from ai.scoring import (  # noqa: E402
    _compute_employee_growth_ch, _revenue_growth_score, _revenue_size_score,
    rescore_company_local,
)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Revenue size: the v4 step table, boundaries included ──")
for rev, want in [(0.5, 0.3), (0.99, 0.3), (1.0, 0.5), (2.0, 0.5), (2.49, 0.5),
                  (2.5, 0.8), (4.9, 0.8), (5.0, 1.0), (8.0, 1.0), (10.0, 1.0),
                  (10.01, 0.7), (15.0, 0.7), (20.0, 0.7), (20.01, 0.2),
                  (40.0, 0.2), (40.01, 0.0), (100.0, 0.0)]:
    chk(f"£{rev}M -> {want}", _revenue_size_score(rev), want)

print()
print("── Revenue growth: any decline is zero ──")
chk("-1% scores 0", _revenue_growth_score(-1), 0.0)
chk("-50% scores 0", _revenue_growth_score(-50), 0.0)
chk("-0.1% scores 0", _revenue_growth_score(-0.1), 0.0)
chk("0% keeps the curve floor", _revenue_growth_score(0), 0.2)
chk("20% unchanged", round(_revenue_growth_score(20), 3), round(0.5 + (10 / 15) * 0.25, 3))
chk("60% unchanged", _revenue_growth_score(60), 0.91)

print()
print("── Employee growth from Companies House filings ──")
hist = json.dumps({"v": 1, "years": [
    {"period_end": "2025-12-31", "employees": 60, "revenue": 8_000_000},
    {"period_end": "2024-12-31", "employees": 40},
    {"period_end": "2023-12-31", "employees": 35},
]})
got = _compute_employee_growth_ch({"ch_history": hist})
chk("uses the two most recent staffed periods (+50%)", got["value"], 50.0)
chk("scores through the shared employee curve", got["score"], 0.9)
chk("explanation names the source", "Companies House" in got["explanation"], True)
chk("one staffed year only -> None",
    _compute_employee_growth_ch({"ch_history": json.dumps(
        {"v": 1, "years": [{"period_end": "2025-12-31", "employees": 60}]})}), None)
chk("periods without employees are skipped, not treated as zero",
    _compute_employee_growth_ch({"ch_history": json.dumps({"v": 1, "years": [
        {"period_end": "2025-12-31", "employees": 50},
        {"period_end": "2024-12-31", "revenue": 1},
        {"period_end": "2023-12-31", "employees": 40},
    ]})})["value"], 25.0)
chk("no history -> None", _compute_employee_growth_ch({}), None)
chk("garbage json -> None", _compute_employee_growth_ch({"ch_history": "{oops"}), None)
chk("zero prior headcount -> None, never a division",
    _compute_employee_growth_ch({"ch_history": json.dumps({"v": 1, "years": [
        {"period_end": "2025-12-31", "employees": 10},
        {"period_end": "2024-12-31", "employees": 0},
    ]})}), None)

print()
print("── Employee decline keeps the gentler curve (unlike revenue) ──")
down = _compute_employee_growth_ch({"ch_history": json.dumps({"v": 1, "years": [
    {"period_end": "2025-12-31", "employees": 45},
    {"period_end": "2024-12-31", "employees": 50},
]})})
chk("-10% headcount scores 0.15, not 0", down["score"], 0.15)

print()
print("── Local rescore: zero AI, current rules ──")
company = {
    "name": "Acme",
    "averroes_fit_score": 0.8,
    "score_business_fit": 0.9,
    "score_market_sentiment": 0.6,
    "score_employee_growth": 0.5,   # old web-search judgement
    "score_details": json.dumps({
        # Old v3 scores stored; values are what matter.
        "revenue_size": {"score": 1.0, "value": 15.0},
        "revenue_growth": {"score": 0.15, "value": -8.0},
    }),
    "ch_history": hist,             # now yields +50% -> 0.9, beats the web 0.5
}
u = rescore_company_local(company)
chk("size rescored under v4 (15M: 1.0 -> 0.7)", u["score_revenue_size"], 0.7)
chk("declining revenue rescored to 0", u["score_revenue_growth"], 0.0)
chk("employee growth upgraded from web to CH filings", u["score_employee_growth"], 0.9)
chk("composite is the mean of the five",
    u["averroes_fit_score"], round((0.7 + 0.0 + 0.9 + 0.9 + 0.6) / 5, 3))
chk("details JSON updated for the profile",
    json.loads(u["score_details"])["revenue_size"]["score"], 0.7)

# The 4-of-5 rule still guards the composite.
thin = {"name": "Thin", "averroes_fit_score": None,
        "score_business_fit": 0.8,
        "score_details": json.dumps({"revenue_size": {"score": 1.0, "value": 6.0}})}
u2 = rescore_company_local(thin)
chk("two metrics -> no composite", u2["averroes_fit_score"], None)
chk("...but the per-metric scores still update", u2["score_revenue_size"], 1.0)

# A never-scored, detail-less row is a no-op.
chk("nothing stored -> None (no-op)",
    rescore_company_local({"name": "Blank", "averroes_fit_score": None}), None)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
