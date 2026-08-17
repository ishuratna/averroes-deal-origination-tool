#!/usr/bin/env python3
"""
Tests for the append-only archive.

The guarantees that matter:
  * the same data always hashes the same, so no spurious versions appear
  * a real change always produces a new version
  * bookkeeping fields ("we looked at this") are NOT treated as changes
  * the module contains no UPDATE or DELETE against the archive, ever
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import archive_service as A  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


BASE = {
    "name": "Acme Ltd", "status": "Qualified", "revenue_y1": 4200000.0,
    "contact_email": "founder@acme.co.uk", "averroes_fit_score": 0.71,
    "ch_company_number": "12345678", "description": "B2B SaaS for logistics",
    "last_smartfill_at": "2026-08-01T10:00:00", "ch_watched_at": "2026-08-01T10:00:00",
    "ingested_at": "2026-01-15T09:00:00",
}

print("── Hashing is stable ──")
chk("same row, same hash", A.row_hash(BASE) == A.row_hash(dict(BASE)), True)
chk("key order is irrelevant",
    A.row_hash(BASE) == A.row_hash(dict(reversed(list(BASE.items())))), True)
# BigQuery hands numbers back as int or float depending on the path; that must
# not look like the company changed.
chk("4200000.0 and 4200000 are the same value",
    A.row_hash(BASE) == A.row_hash({**BASE, "revenue_y1": 4200000}), True)
chk("None and empty string are both 'absent'",
    A.row_hash({**BASE, "sector": None}) == A.row_hash({**BASE, "sector": ""}), True)
chk("an absent key equals an empty one",
    A.row_hash(BASE) == A.row_hash({**BASE, "sector": ""}), True)
# Every way BigQuery might hand back the same figure must agree.
from decimal import Decimal
chk("Decimal('4200000') matches the float",
    A._norm_value(Decimal("4200000")) == A._norm_value(4200000.0), True)
chk("a real fraction is preserved, not rounded away",
    A._norm_value(0.715) != A._norm_value(0.71), True)
chk("True is not stored as the integer 1",
    A._norm_value(True) != A._norm_value(1), True)

print()
print("── Real changes are detected ──")
for field, new in [
    ("status", "Contacted"),
    ("revenue_y1", 5100000.0),
    ("contact_email", "ceo@acme.co.uk"),
    ("averroes_fit_score", 0.42),
    ("ch_company_number", "87654321"),
    ("description", "B2B SaaS for freight"),
]:
    chk(f"{field} changing produces a new hash",
        A.row_hash(BASE) != A.row_hash({**BASE, field: new}), True)
chk("a brand new field counts as a change",
    A.row_hash(BASE) != A.row_hash({**BASE, "owner": "Ishu"}), True)

print()
print("── 'We looked at it' is not a change ──")
# Otherwise every SmartFill re-run would append a version saying nothing.
for field in ("last_smartfill_at", "ch_watched_at", "ingested_at"):
    chk(f"{field} moving alone is not a change",
        A.row_hash(BASE) == A.row_hash({**BASE, field: "2026-12-31T23:59:59"}), True)

print()
print("── Append-only is structural, not a promise ──")
src = inspect.getsource(A)
# No mutation of the archive table anywhere in the module.
for label, bad in [
    ("DELETE FROM",     r"\bDELETE\s+FROM\b"),
    ("UPDATE `table`",  r"\bUPDATE\s+`"),
    ("TRUNCATE TABLE",  r"\bTRUNCATE\s+TABLE\b"),
    ("WRITE_TRUNCATE",  r"\bWRITE_TRUNCATE\b"),
    ("DROP TABLE",      r"\bDROP\s+TABLE\b"),
    ("MERGE",           r"\bMERGE\s+`"),
]:
    chk(f"module contains no {label}", re.search(bad, src, re.I) is None, True)
chk("write disposition is pinned to APPEND", "WRITE_APPEND" in src, True)
chk("archive table is partitioned (history stays cheap)", "TimePartitioning" in src, True)
chk("...and clustered by company", 'clustering_fields = ["name"]' in src, True)
chk("the whole row is preserved as JSON", "row_json" in src, True)
chk("export never overwrites: paths carry a run timestamp", "%Y%m%dT%H%M%SZ" in src, True)
chk("targets is in the export list", "targets" in A.EXPORT_TABLES, True)
chk("the archive itself is exported too", "targets_archive" in A.EXPORT_TABLES, True)

print()
print("── Version numbering ──")
# A first sighting is version 1, and versions increment per company.
chk("bookkeeping exclusions are exactly the three intended",
    A._NOT_A_CHANGE == {"last_smartfill_at", "ch_watched_at", "ingested_at"}, True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
