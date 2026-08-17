#!/usr/bin/env python3
"""
Tests for the nightly bulk SmartFill: the queue ordering and the stop rule.

The ordering decides where real money goes first, so it is proven on the pure
function rather than assumed. The stop rule is what makes the job safe to run
unattended: manual daytime runs count toward the same target, so the night can
only ever top the day up, never stack on it.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from main import (  # noqa: E402
    AUTO_SMARTFILL_BATCH, AUTO_SMARTFILL_TARGET, _auto_smartfill_rank,
)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


def order(*companies):
    return [c["name"] for c in sorted(companies, key=_auto_smartfill_rank)]


print("── Best prospects first ──")
rich = {"name": "RichCo", "revenue_y1": 4_200_000, "employees": 40,
        "website": "https://rich.co.uk", "description": "x" * 300, "source": "Inven Export"}
mid = {"name": "MidCo", "website": "https://mid.co.uk",
       "description": "x" * 300, "source": "SaaStock 2026"}
bare = {"name": "BareCo", "source": "Scraped list"}
chk("financials beat a bare name", order(bare, rich), ["RichCo", "BareCo"])
chk("full ordering is rich > mid > bare",
    order(bare, mid, rich), ["RichCo", "MidCo", "BareCo"])

print()
print("── Each signal moves the queue ──")
base = {"name": "A", "source": ""}
for field, value, label in [
    ("revenue_y1", 1_000_000, "stored revenue"),
    ("revenue_estimate_m", 3.5, "a revenue estimate"),
    ("employees", 25, "employee count"),
    ("employees_ch", 25, "CH employee count"),
    ("website", "https://a.co", "a website"),
    ("description", "x" * 200, "a substantive description"),
]:
    with_it = {**base, field: value}
    chk(f"{label} ranks above nothing",
        _auto_smartfill_rank(with_it) < _auto_smartfill_rank(base), True)
chk("a 3-line description is not 'substantive'",
    _auto_smartfill_rank({**base, "description": "x" * 100})
    == _auto_smartfill_rank(base), True)

print()
print("── Source quality ──")
chk("Inven beats a conference",
    _auto_smartfill_rank({**base, "source": "Inven Export Q3"})
    < _auto_smartfill_rank({**base, "source": "SaaStock 2026"}), True)
chk("a conference beats an anonymous scrape",
    _auto_smartfill_rank({**base, "source": "conference booth list"})
    < _auto_smartfill_rank({**base, "source": "Scraped"}), True)

print()
print("── The order is stable, so ticks never skip or repeat ──")
a = {"name": "Alpha", "source": ""}
b = {"name": "Beta", "source": ""}
chk("ties break alphabetically", order(b, a), ["Alpha", "Beta"])
chk("case does not matter to the tie-break",
    order({"name": "beta", "source": ""}, {"name": "Alpha", "source": ""}),
    ["Alpha", "beta"])
chk("None fields never crash the ranking",
    isinstance(_auto_smartfill_rank({"name": None, "website": None,
                                     "description": None, "source": None}), tuple), True)

print()
print("── The stop rule arithmetic ──")
# remaining = target - used_today, and used_today counts MANUAL runs too. The
# endpoint refuses when remaining <= 0, so the combined day can never exceed
# the target and the free grounding allowance maths holds.
chk("target leaves free-tier headroom (250 x 4 = 1000 of 1500)",
    AUTO_SMARTFILL_TARGET * 4 <= 1200, True)
chk("a night of ticks can meet the target (20 ticks x batch >= target)",
    20 * AUTO_SMARTFILL_BATCH >= AUTO_SMARTFILL_TARGET, True)
chk("one tick fits its 12-minute slot (~25s per company)",
    AUTO_SMARTFILL_BATCH * 25 <= 11 * 60, True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
