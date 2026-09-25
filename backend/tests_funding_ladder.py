#!/usr/bin/env python3
"""
The funding ladder is read from the SH01 filing's own text, with no AI, and
the rounds, prices and implied valuations are derived in code. Figures below
follow the shape of Arcus Global's Round 6 (8 May 2019: 2,251,604 shares in
issue after, GBP 3.23 a share, GBP 7.26m post-money) as Mark to Market shows
it, so the derivation can be checked against an independent reading.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services.funding_ladder import (parse_sh01_text, build_ladder, merge_ledger,  # noqa: E402
                                     column_fills)

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── Reading one SH01's text (the e-filed form layout) ──")
SH01_2019 = """
SH01
Return of allotment of shares
1 Company details
Company number 06946606
Company name in full ARCUS GLOBAL LIMITED
2 Allotment dates
From Date 08/05/2019
To Date 08/05/2019
3 Shares allotted
Class of shares B ORDINARY
Currency GBP
Number allotted 576,622
Nominal value of each share 0.01
Amount paid (including share premium) on each share 3.23
Amount (if any) unpaid (including share premium) on each share 0
Class of shares A ORDINARY
Currency GBP
Number allotted 184,519
Nominal value of each share 0.01
Amount paid (including share premium) on each share 3.23
Amount (if any) unpaid (including share premium) on each share 0
4 Statement of capital
Currency GBP
Class of shares ORDINARY
Number of shares 1,083,963
...
Total number of shares 2,251,604
Total aggregate nominal value 22,516.04
"""
r = parse_sh01_text(SH01_2019)
chk("allotment date from the From date", r.get("allotment_date"), "2019-05-08")
chk("two priced classes read", [(a["share_class"], a["shares"], a["paid"], a["nominal"]) for a in r["allotments"]][:2],
    [("B Ordinary", 576622, 3.23, 0.01), ("A Ordinary", 184519, 3.23, 0.01)])
chk("statement-of-capital class rows (no 'Number allotted') are not counted as allotments",
    len(r["allotments"]), 2)
chk("total shares after the allotment", r.get("total_shares_after"), 2251604)
chk("a scan with no text reads as nothing", parse_sh01_text("   "), {})
chk("a different form reads as nothing", parse_sh01_text("CS01 Confirmation statement. Shareholders: ..."), {})

print()
print("── The ladder: rounds, price, raised, implied valuation ──")
readings = [
    r,
    {"allotment_date": "2018-02-27", "allotments": [{"share_class": "B Ordinary", "shares": 400000, "nominal": 0.01, "paid": 2.10}],
     "total_shares_after": 1490463},
    {"allotment_date": "2020-11-03", "allotments": [{"share_class": "Ordinary", "shares": 15000, "nominal": 0.01, "paid": 0.01}],
     "total_shares_after": 2266604},
]
filings = [{"date": "2019-05-10", "transaction_id": "tx2019"},
           {"date": "2018-03-02", "transaction_id": "tx2018"},
           {"date": "2020-11-05", "transaction_id": "tx2020"}]
lad = build_ladder(readings, filings)
rounds = {x["transaction_id"]: x for x in lad["rounds"]}
chk("rounds sorted by allotment date", [x["transaction_id"] for x in lad["rounds"]], ["tx2018", "tx2019", "tx2020"])
r19 = rounds["tx2019"]
chk("2019: shares allotted", r19["shares"], 761141)
chk("2019: price per share (weighted)", r19["price"], 3.23)
chk("2019: money raised = shares x price", r19["raised"], round(761141 * 3.23, 2))
chk("2019: post-money = total shares after x price (matches the independent reading, ~7.27m)",
    round(r19["post_money"] / 1e6, 2), 7.27)
chk("2019: pre-money = post - raised", r19["pre_money"], round(2251604 * 3.23 - 761141 * 3.23, 2))
chk("2019: an up round vs 2018 (2.10 -> 3.23)", (r19.get("vs_prior"), r19.get("vs_prior_pct")), ("up", 53.8))
chk("2020: option exercise at nominal is a nominal issue, not a round", rounds["tx2020"]["kind"], "nominal issue")
chk("...and carries no valuation", "post_money" in rounds["tx2020"], False)
chk("equity rounds counted without the nominal issue", lad["equity_rounds"], 2)
chk("total raised sums the equity rounds only", lad["total_raised"], round(400000 * 2.10 + 761141 * 3.23, 2))
chk("cumulative raised runs in date order", [x["cumulative_raised"] for x in lad["rounds"]],
    [840000.0, round(840000 + 761141 * 3.23, 2), round(840000 + 761141 * 3.23, 2)])
chk("last round is the newest EQUITY round", lad["last_round"]["transaction_id"], "tx2019")
chk("round numbering skips nominal issues", [x.get("round_no") for x in lad["rounds"]], [1, 2, None])

print()
print("── Incremental: a stored ledger is extended, never re-read ──")
stored = build_ladder(readings[:2], filings[:2])
merged = merge_ledger(stored, [readings[2]], [filings[2]])
chk("filings already read are kept", set(merged["filings_seen"]), {"tx2018", "tx2019", "tx2020"})
chk("kept rounds are rebuilt from their own reading (same numbers)", merged["rounds"][1]["post_money"], r19["post_money"])
chk("kind survives the rebuild", merged["rounds"][2]["kind"], "nominal issue")
re_merged = merge_ledger(merged, [readings[2]], [filings[2]])
chk("re-reading a known filing does not duplicate it", len(re_merged["rounds"]), 3)

print()
print("── Fill-only into the legacy columns ──")
f = column_fills(lad, {"total_raised_m": None, "last_financing_type": ""})
chk("total raised in GBP m", f.get("total_raised_m"), round(lad["total_raised"] / 1e6, 2))
chk("last round date, size, type, valuation", (f.get("last_financing_date"), f.get("last_financing_size_m"),
    f.get("last_financing_type"), f.get("last_financing_valuation_m")),
    ("2019-05-08", round(761141 * 3.23 / 1e6, 2), "Equity (SH01 allotment)", 7.27))
f2 = column_fills(lad, {"total_raised_m": 6.68, "last_financing_valuation_m": 9.0, "last_financing_type": "Series A (Gain)"})
chk("a value already on the row (Gain / PitchBook) is never replaced", ("total_raised_m" in f2, "last_financing_valuation_m" in f2, "last_financing_type" in f2), (False, False, False))
chk("no equity rounds -> nothing filled", column_fills(build_ladder([readings[2]], [filings[2]]), {}), {})

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
