#!/usr/bin/env python3
"""
iXBRL accounts parsing: exact tagged figures instead of a Gemini PDF read.

What matters: scale/sign handling (a mis-scaled turnover is a 1000x error),
current-vs-prior period assignment, the in-document company-number identity
check, and honest emptiness (a doc with no useful facts must return {} so the
caller falls back to the PDF path).
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services.ixbrl_accounts import parse_ixbrl  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


DOC = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<body>
<xbrli:context id="CUR"><xbrli:period>
  <xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate>
</xbrli:period></xbrli:context>
<xbrli:context id="CURBAL"><xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="PRI"><xbrli:period>
  <xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2024-03-31</xbrli:endDate>
</xbrli:period></xbrli:context>
<xbrli:context id="PRIBAL"><xbrli:period><xbrli:instant>2024-03-31</xbrli:instant></xbrli:period></xbrli:context>

<ix:nonNumeric name="uk-bus:UKCompaniesHouseRegisteredNumber" contextRef="CUR">11361720</ix:nonNumeric>

<ix:nonFraction name="uk-core:TurnoverRevenue" contextRef="CUR" scale="3" unitRef="GBP">5,200</ix:nonFraction>
<ix:nonFraction name="uk-core:TurnoverRevenue" contextRef="PRI" scale="3" unitRef="GBP">4,000</ix:nonFraction>
<ix:nonFraction name="uk-core:GrossProfitLoss" contextRef="CUR" scale="0">4160000</ix:nonFraction>
<ix:nonFraction name="uk-core:ProfitLossBeforeTax" contextRef="CUR" sign="-" scale="3">250</ix:nonFraction>
<ix:nonFraction name="uk-core:CashBankOnHand" contextRef="CURBAL" scale="0">812,345</ix:nonFraction>
<ix:nonFraction name="uk-core:CashBankOnHand" contextRef="PRIBAL" scale="0">400,000</ix:nonFraction>
<ix:nonFraction name="uk-core:NetAssetsLiabilities" contextRef="CURBAL" scale="3">1,900</ix:nonFraction>
<ix:nonFraction name="uk-core:AverageNumberEmployeesDuringPeriod" contextRef="CUR">34</ix:nonFraction>
<ix:nonFraction name="uk-core:AverageNumberEmployeesDuringPeriod" contextRef="PRI">28</ix:nonFraction>
</body></html>"""

print("── A real-shaped filing ──")
got = parse_ixbrl(DOC, "11361720")
chk("revenue current, scale applied (5,200 x10^3)", got.get("revenue_current"), 5_200_000.0)
chk("revenue prior assigned to the older period", got.get("revenue_prior"), 4_000_000.0)
chk("gross profit unscaled", got.get("gross_profit_current"), 4_160_000.0)
chk("negative sign honoured (a loss is a loss)", got.get("profit_current"), -250_000.0)
chk("balance-sheet instant maps to the same period end", got.get("cash_current"), 812_345.0)
chk("prior cash from the prior instant", got.get("cash_prior"), 400_000.0)
chk("net assets scaled", got.get("net_assets_current"), 1_900_000.0)
chk("employees current + prior as ints", (got.get("employees"), got.get("employees_prior")), (34, 28))
chk("period ends identified", (got.get("period_end_current"), got.get("period_end_prior")),
    ("2025-03-31", "2024-03-31"))
chk("source marked so callers can tell", got.get("_source"), "ixbrl")
chk("zero AI stated in the notes", "zero AI" in (got.get("notes") or ""))

print()
print("── Identity: the document says whose accounts these are ──")
chk("leading zeros in our number never break the match",
    parse_ixbrl(DOC, "011361720").get("revenue_current"), 5_200_000.0)
wrong = parse_ixbrl(DOC, "99999999")
chk("wrong company number is REFUSED, not parsed", "error" in wrong)
chk("...naming both numbers", "11361720" in wrong.get("error", "") and "99999999" in wrong.get("error", ""))

print()
print("── Honest emptiness (the caller must fall back) ──")
chk("empty document -> {}", parse_ixbrl("", "123"), {})
chk("html with no tagged facts -> {}", parse_ixbrl("<html><body><p>scan of paper</p></body></html>", "123"), {})
FILLETED = DOC.replace('name="uk-core:TurnoverRevenue"', 'name="uk-core:SomethingUnmapped"')
got2 = parse_ixbrl(FILLETED, "11361720")
chk("filleted (no revenue tag) still yields the balance sheet", got2.get("cash_current"), 812_345.0)
chk("...with revenue honestly null", got2.get("revenue_current"), None)

print()
print("── The wider read (25 Sep 2026): EBIT, EBITDA derived, staff, directors, borrowings ──")
WIDE = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>
<xbrli:context id="CUR"><xbrli:period><xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="CURBAL"><xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="DIR1"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="d:Dirs">d:Director1</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
  <xbrli:period><xbrli:startDate>2024-07-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<ix:nonFraction name="core:TurnoverRevenue" contextRef="CUR" unitRef="GBP" decimals="0">6993811</ix:nonFraction>
<ix:nonFraction name="core:OperatingProfitLoss" contextRef="CUR" unitRef="GBP" decimals="0">455634</ix:nonFraction>
<ix:nonFraction name="core:DepreciationExpense" contextRef="CUR" unitRef="GBP" decimals="0">31347</ix:nonFraction>
<ix:nonFraction name="core:AmortisationExpense" contextRef="CUR" unitRef="GBP" decimals="0">624053</ix:nonFraction>
<ix:nonFraction name="core:DirectorRemuneration" contextRef="DIR1" unitRef="GBP" decimals="0">200000</ix:nonFraction>
<ix:nonFraction name="core:DirectorRemuneration" contextRef="CUR" unitRef="GBP" decimals="0">567138</ix:nonFraction>
<ix:nonFraction name="core:StaffCostsEmployeeBenefitsExpense" contextRef="CUR" unitRef="GBP" decimals="0">4878462</ix:nonFraction>
<ix:nonFraction name="core:Borrowings" contextRef="CURBAL" unitRef="GBP" decimals="0">1665701</ix:nonFraction>
<ix:nonFraction name="core:CashBankOnHand" contextRef="CURBAL" unitRef="GBP" decimals="0">1921468</ix:nonFraction>
<ix:nonFraction name="core:TradeDebtorsTradeReceivables" contextRef="CURBAL" unitRef="GBP" decimals="0">1200000</ix:nonFraction>
</body></html>"""
w = parse_ixbrl(WIDE)
chk("operating profit (EBIT) read", w.get("operating_profit_current"), 455634.0)
chk("EBITDA derived = EBIT + depreciation + amortisation", w.get("ebitda_current"), 455634.0 + 31347 + 624053)
chk("...and the derivation is stated", "operating profit + depreciation" in (w.get("ebitda_basis") or ""))
chk("director remuneration takes the company TOTAL, not the first per-director slice",
    w.get("director_pay_current"), 567138.0)
chk("staff costs", w.get("staff_costs_current"), 4878462.0)
chk("borrowings, trade debtors (balance-sheet instants)", (w.get("borrowings_current"), w.get("trade_debtors_current")), (1665701.0, 1200000.0))
chk("a filing without operating profit derives no EBITDA", parse_ixbrl(DOC).get("ebitda_current"), None)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
