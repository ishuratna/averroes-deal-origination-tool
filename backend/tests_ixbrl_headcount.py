#!/usr/bin/env python3
"""
iXBRL reading: the headcount rule, and how far back we read.

Anchored on a REAL wrong number. FOUNDIT! GROUP LIMITED (09690801), accounts
made up to 31 August 2025, tags its staff numbers like this:

  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod"
                  contextRef="C" unitRef="Pure"
                  decimals="2" scale="-2">10</ix:nonFraction>

The document a human reads says 10 employees. scale="-2" says multiply by 0.01,
so we computed 0.1 and int() truncated it to ZERO. The card showed a company
with GBP 9.5M of revenue and no staff, and the fit score's employee-growth
dimension collapsed with it.

`scale` exists so MONEY can be reported in thousands or millions. Some filing
software mirrors `decimals` onto Pure-unit facts, where it means nothing.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import companies_house_service as chs  # noqa: E402
from services.ixbrl_accounts import parse_ixbrl  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


def doc(employee_facts: str, extra: str = "") -> str:
    """A minimal but structurally real filing: two periods, one money fact."""
    return f"""
    <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
      <ix:header><ix:resources>
        <xbrli:context id="C"><xbrli:period><xbrli:startDate>2024-09-01</xbrli:startDate>
          <xbrli:endDate>2025-08-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:context id="F"><xbrli:period><xbrli:startDate>2023-09-01</xbrli:startDate>
          <xbrli:endDate>2024-08-31</xbrli:endDate></xbrli:period></xbrli:context>
      </ix:resources></ix:header>
      <body>
        <ix:nonNumeric name="core:UKCompaniesHouseRegisteredNumber">09690801</ix:nonNumeric>
        <ix:nonFraction name="core:TurnoverRevenue" contextRef="C" unitRef="GBP">9584000</ix:nonFraction>
        {employee_facts}{extra}
      </body>
    </html>"""


print("-- the FoundIt! filing, exactly as Companies House serves it --")
real = doc("""
  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod" contextRef="C"
                  unitRef="Pure" decimals="2" scale="-2">10</ix:nonFraction>
  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod" contextRef="F"
                  unitRef="Pure" decimals="2" scale="-2">9</ix:nonFraction>""")
got = parse_ixbrl(real, "09690801")
chk("the current year reads as 10 people, not 0", got.get("employees"), 10)
chk("the prior year reads as 9, not 0", got.get("employees_prior"), 9)
chk("the money fact is untouched by the fix", got.get("revenue_current"), 9584000.0)

print()
print("-- a legitimate scale is still honoured --")
# Accounts reported in thousands: 9584 * 10^3. Nothing about the fix may
# interfere with the reason `scale` exists.
thousands = doc("", extra="""
  <ix:nonFraction name="core:CashBankOnHand" contextRef="C" unitRef="GBP" scale="3">1221</ix:nonFraction>""")
chk("cash in thousands still scales up", parse_ixbrl(thousands, "09690801").get("cash_current"), 1221000.0)

print()
print("-- the rule is narrow: only a sub-1 headcount is treated as mis-tagged --")
plain = doc("""
  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod" contextRef="C"
                  unitRef="Pure">8</ix:nonFraction>""")
chk("an untagged count is read as-is", parse_ixbrl(plain, "09690801").get("employees"), 8)
big = doc("""
  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod" contextRef="C"
                  unitRef="Pure" scale="3">2</ix:nonFraction>""")
chk("a POSITIVE scale is left alone (2,000 staff is a real number)",
    parse_ixbrl(big, "09690801").get("employees"), 2000)
zero = doc("""
  <ix:nonFraction name="core:AverageNumberEmployeesDuringPeriod" contextRef="C"
                  unitRef="Pure">0</ix:nonFraction>""")
chk("a genuine zero survives: a dormant holding company really has no staff",
    parse_ixbrl(zero, "09690801").get("employees"), 0)
none = doc("")
chk("no employee fact stays None, never 0", parse_ixbrl(none, "09690801").get("employees"), None)

print()
print("-- identity is still checked before any of this --")
chk("a document for another company is refused outright",
    "error" in parse_ixbrl(real, "13176168"))

print()
print("-- lookback: latest plus four prior years --")
chk("five filings are opened", chs.ACCOUNTS_FILINGS_PARSED, 5)
chk("...which yields six distinct years, since each filing carries its prior year",
    chs.ACCOUNTS_FILINGS_PARSED + 1 >= 5)
chk("more filings are fetched than parsed, to survive a changed year end",
    chs.ACCOUNTS_FILINGS_FETCHED > chs.ACCOUNTS_FILINGS_PARSED)
chk("the history store holds every year we can reach", chs.YEARS_KEPT >= chs.ACCOUNTS_FILINGS_PARSED + 1)
chk("the AI fallback stays pinned to the LATEST filing, so depth costs nothing",
    chs.PDF_FALLBACK_MAX_FILINGS, 1)
import inspect  # noqa: E402
src = inspect.getsource(chs.extract_ch_financials)
chk("older filings without iXBRL are skipped rather than sent to Gemini",
    "if i >= PDF_FALLBACK_MAX_FILINGS:" in src)
chk("no hard-coded filing cap survives in the loop", "filings[:3]" not in src)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
