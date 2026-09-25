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
print("── The e-filed layout (SH01(ef), as Companies House renders it: label, newline, value) ──")
# Arcus Global's 8 Apr 2026 filing, as pymupdf extracts it. The statement of
# capital lists every class IN ISSUE under "Number allotted" too.
SH01_EF = """SH01(ef)

Return of Allotment of Shares

Company Name:
ARCUS GLOBAL LIMITED
Company Number:
06946606
Received for filing in Electronic Format on the: 08/04/2026
XEZJ8KXL

Shares Allotted (including bonus shares)
Date or period during which
shares are allotted
From
To
10/03/2026
Class of Shares:
ORDINARY
Currency:
GBP
Number allotted
333083
Nominal value of each share
0.0005
Amount paid:
0.1
Amount unpaid:
0
No shares allotted other than for cash
Electronically filed document for Company Number:
06946606
Page: 1

Statement of Capital (Share Capital)
Class of Shares:
A
ORDINARY
Currency:
GBP
Number allotted
172219
Aggregate nominal value:
86.1095
Prescribed particulars
EACH OF THE A ORDINARY SHARES: 1. CARRY THE RIGHT TO THE DISTRIBUTION OF
PROFITS
Class of Shares:
ORDINARY
Currency:
GBP
Number allotted
1637491
Aggregate nominal value:
818.7455
Statement of Capital (Totals)
Currency:
GBP
Total number of shares:
1995765
Total aggregate nominal value:
997.8825
Total aggregate amount unpaid:
0
"""
ef = parse_sh01_text(SH01_EF)
chk("e-filed: the From/To labels precede the date", ef.get("allotment_date"), "2026-03-10")
chk("e-filed: ONE allotment, the statement of capital's classes in issue are not allotments",
    [(a["share_class"], a["shares"], a["nominal"], a["paid"]) for a in ef["allotments"]],
    [("Ordinary", 333083, 0.0005, 0.1)])
chk("e-filed: total shares after, from the totals block", ef.get("total_shares_after"), 1995765)

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
print("── Arcus's real ledger: a duplicate filing, two option exercises, a misread total ──")
# The four readings the tool actually held for Arcus on 25 Sep 2026 (two of
# them the SAME May 2019 allotment filed on 29 May and again on 5 Jun), plus
# the Mar 2026 e-filed exercise above.
may19 = {"allotment_date": "2019-05-08", "allotments": [{"share_class": "B Ordinary", "shares": 759602, "nominal": 0.01, "paid": 3.2517}],
         "total_shares_after": 2242604, "_source": "ai"}
nov19 = {"allotment_date": "2019-11-28", "allotments": [{"share_class": "Ordinary", "shares": 9000, "nominal": 0.01, "paid": 1.0}],
         "total_shares_after": 2251604, "_source": "ai"}
arcus_readings = [may19, dict(may19), nov19, ef, readings[1]]
arcus_filings = [{"date": "2019-05-29", "transaction_id": "tx-may-a"}, {"date": "2019-06-05", "transaction_id": "tx-may-b"},
                 {"date": "2020-01-20", "transaction_id": "tx-nov"}, {"date": "2026-04-08", "transaction_id": "tx-2026"},
                 {"date": "2018-03-02", "transaction_id": "tx2018"}]
A = build_ladder(arcus_readings, arcus_filings)
by = {x["transaction_id"]: x for x in A["rounds"]}
chk("the twice-filed May 2019 allotment is ONE round", "tx-may-b" in by and "tx-may-a" in by, False)
chk("...kept on the first filing, noting the second", (by["tx-may-a"].get("duplicate_filings") or [{}])[0].get("transaction_id"), "tx-may-b")
chk("...and both filings stay in filings_seen (never re-read)", {"tx-may-a", "tx-may-b"} <= set(A["filings_seen"]), True)
chk("May 2019 post-money matches Mark to Market's independent reading (~7.29m at 3.2517)",
    round(by["tx-may-a"]["post_money"] / 1e6, 2), 7.29)
chk("GBP 9,000 at GBP 1 in Nov 2019 is a small issue, not a round", by["tx-nov"]["kind"], "small issue")
chk("...with no valuation", "post_money" in by["tx-nov"], False)
chk("333,083 shares at GBP 0.10 against a last round at 3.25 is an option exercise, not a round",
    by["tx-2026"]["kind"], "small issue")
chk("...and its note says why (it raises GBP 33k, under the floor)", "not counted as a round" in (by["tx-2026"].get("note") or ""), True)
big_cheap = build_ladder([readings[1], {"allotment_date": "2019-01-01", "allotments": [{"share_class": "Ordinary", "shares": 600000, "nominal": 0.01, "paid": 0.20}],
                                        "total_shares_after": 2100000}],
                         [filings[1], {"date": "2019-01-03", "transaction_id": "tx-cheap"}])
cheap = [x for x in big_cheap["rounds"] if x["transaction_id"] == "tx-cheap"][0]
chk("GBP 120k at 0.20 against a last round at 2.10 is an option exercise (price rule), not a down round",
    (cheap["kind"], "option exercise" in (cheap.get("note") or "")), ("small issue", True))
chk("equity rounds: 2018 and May 2019 only", A["equity_rounds"], 2)
chk("total raised excludes both small issues and counts May 2019 once",
    A["total_raised"], round(400000 * 2.10 + 759602 * 3.2517, 2))
chk("last round is May 2019, not the 2026 exercise", A["last_round"]["transaction_id"], "tx-may-a")
chk("round numbering: 2018 = 1, May 2019 = 2, the rest unnumbered",
    [(x["transaction_id"], x.get("round_no")) for x in A["rounds"]],
    [("tx2018", 1), ("tx-may-a", 2), ("tx-nov", None), ("tx-2026", None)])
bad_total = build_ladder([{"allotment_date": "2021-01-01", "allotments": [{"share_class": "Ordinary", "shares": 500000, "nominal": 0.01, "paid": 2.0}],
                           "total_shares_after": 1000}], [{"date": "2021-01-02", "transaction_id": "tx-bad"}])
chk("a statement-of-capital total below the shares allotted derives no valuation",
    ("post_money" in bad_total["rounds"][0], bad_total["rounds"][0].get("total_shares_after")), (False, None))
chk("...but the money raised still counts", bad_total["total_raised"], 1000000.0)

print()
print("── A big cheque at a low price is a down round, not an option exercise; a shrinking total is a misread ──")
r18a = {"allotment_date": "2018-05-11", "allotments": [{"share_class": "A Ordinary", "shares": 184519, "nominal": 0.0005, "paid": 16.2585}],
        "total_shares_after": 1479927, "_source": "ai"}
r18b = {"allotment_date": "2018-05-11", "allotments": [{"share_class": "Ordinary", "shares": 56070, "nominal": 0.0005, "paid": 7.24}],
        "total_shares_after": 1295408, "_source": "ai"}
D = build_ladder([r18a, r18b, may19], [{"date": "2018-05-14", "transaction_id": "t18a"}, {"date": "2018-07-10", "transaction_id": "t18b"},
                                        {"date": "2019-05-29", "transaction_id": "t19"}])
dd = {x["transaction_id"]: x for x in D["rounds"]}
chk("GBP 2.47m at 3.25 after a round at 16.26 is an equity round (a down round), not a small issue",
    (dd["t19"]["kind"], dd["t19"].get("vs_prior")), ("equity round", "down"))
chk("a filing whose total in issue is below an earlier filing's derives no valuation",
    ("post_money" in dd["t18b"], dd["t18b"].get("total_inconsistent")), (False, True))
chk("...but the money still counts", dd["t18b"]["kind"], "equity round")
chk("the consistent filings keep their valuation", "post_money" in dd["t18a"] and "post_money" in dd["t19"], True)

print()
print("── A corrected ladder may replace ITS OWN earlier fill, never anyone else's ──")
v1_like = {"equity_rounds": 3, "total_raised": 4948995.64, "last_round": by["tx-nov"] | {"post_money": 2251604.0, "raised": 9000.0, "date": "2019-11-28"}}
v1_fills = column_fills(v1_like, {})
chk("what v1 would have written (the 2.25m 'valuation' from a 9k option exercise)",
    (v1_fills.get("last_financing_valuation_m"), v1_fills.get("total_raised_m")), (2.25, 4.95))
row_after_v1 = {"last_financing_valuation_m": 2.25, "total_raised_m": 4.95, "last_financing_date": "2019-11-28",
                "last_financing_size_m": 0.01, "last_financing_type": "Equity (SH01 allotment)", "last_valuation_date": "2019-11-28"}
A2 = dict(A, fills=v1_fills)
f3 = column_fills(A2, row_after_v1)
chk("the ladder's own 2.25 is replaced by the real last round's post-money",
    (f3.get("last_financing_valuation_m"), f3.get("last_financing_date")), (7.29, "2019-05-08"))
chk("total raised corrected too", f3.get("total_raised_m"), round(A["total_raised"] / 1e6, 2))
gain_row = dict(row_after_v1, last_financing_valuation_m=9.0, total_raised_m=6.68)
f4 = column_fills(A2, gain_row)
chk("a Gain/PitchBook number on the row is still never touched",
    ("last_financing_valuation_m" in f4, "total_raised_m" in f4), (False, False))
chk("a stored ledger's fills survive a rebuild", merge_ledger(A2, [], [])["fills"], v1_fills)

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
