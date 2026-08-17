"""
Append-only archive: preserve every version of every company, forever.

WHY THIS EXISTS
The live tables are mutable by design. SmartFill overwrites, syncs advance
stages, migrations rewrite whole columns. That is correct for a working tool
and dangerous for the 12,000 companies and their enrichment: one bad job, one
migration run in the wrong order, and months of AI spend and Companies House
extraction is silently replaced.

This module never changes anything. It only ever INSERTs. When a company's data
changes, a NEW ROW is appended carrying the complete previous state, so the full
history of every company is reconstructable at any point in time.

DOCTRINE — read this before using it anywhere
`targets_archive` is WRITE-ONLY for the application. It is never read to answer
"what is true about this company now". BigQuery's `targets` remains the single
source of truth (see CLAUDE.md). The archive exists for two jobs only:
  1. recovering data that should not have been lost, by hand, deliberately
  2. answering "what did this row look like before that change"
If a feature ever needs the archive to render a page, that feature is wrong.

TWO LAYERS, DIFFERENT FAILURES
  * This archive table protects against BAD DATA overwriting good data. It
    cannot protect against the dataset itself being deleted.
  * The GCS export (export_tables_to_gcs) protects against losing BigQuery
    entirely. It cannot give you row-level history.
Both are needed; neither substitutes for the other.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ARCHIVE_TABLE = "targets_archive"

# Excluded from the change HASH but still stored in the archived row. These say
# "we looked at this company", not "this is what the company is", so a re-run
# that finds nothing new should not manufacture a new archive row.
_NOT_A_CHANGE = {"last_smartfill_at", "ch_watched_at", "ingested_at"}

# Tables exported wholesale to GCS. Everything the tool knows lives here.
EXPORT_TABLES = ("targets", "investors", "activity_log", "email_log",
                 "investor_links", "analytics_ledger", "qualification_config",
                 "targets_archive")


def _norm_value(v) -> str:
    """One canonical string per VALUE, whatever Python type carried it.

    This matters more than it looks. BigQuery hands the same figure back as
    4200000.0 or 4200000 depending on whether it arrived via a query, a JSON
    round-trip or a load job, and Decimal for NUMERIC columns. Naively calling
    str() would make an unchanged £4.2m revenue hash differently between runs
    and append a version claiming the company changed when it did not.
    """
    if isinstance(v, bool):                 # bool before int: bool IS an int
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(float(v))
    # Decimal (NUMERIC/BIGNUMERIC) and anything else: normalise via float when
    # it is genuinely numeric, otherwise fall back to the plain string.
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            f = float(v)
            return str(int(f)) if f.is_integer() else repr(f)
    except Exception:
        pass
    return str(v)


def _canonical(row: Dict) -> str:
    """Stable JSON for hashing: keys sorted, bookkeeping fields dropped, values
    normalised per _norm_value. Two rows with the same MEANING must always hash
    identically, regardless of how the types arrived."""
    clean = {}
    for k, v in row.items():
        if k in _NOT_A_CHANGE:
            continue
        if v is None or v == "":
            continue          # absent and empty are the same thing here
        clean[k] = _norm_value(v)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def row_hash(row: Dict) -> str:
    return hashlib.sha256(_canonical(row).encode()).hexdigest()[:32]


def ensure_archive_table(bq) -> str:
    """Create the archive table if missing. Partitioned by archive date and
    clustered by company so history lookups stay cheap as it grows."""
    from google.cloud import bigquery

    table_id = f"{bq.project_id}.{bq.dataset_id}.{ARCHIVE_TABLE}"
    try:
        bq.client.get_table(table_id)
        return table_id
    except Exception:
        pass

    schema = [
        bigquery.SchemaField("archived_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("row_hash", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("change_note", "STRING"),
        bigquery.SchemaField("version", "INT64"),
        # The ENTIRE row as JSON. Deliberately not mirrored as typed columns:
        # the archive must keep working when the live schema gains a column,
        # and a schema migration must never be able to corrupt history.
        bigquery.SchemaField("row_json", "STRING", mode="REQUIRED"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="archived_at")
    table.clustering_fields = ["name"]
    table.description = ("APPEND-ONLY history of averroes_deal_flow.targets. "
                         "Never UPDATE or DELETE rows in this table. Never read it "
                         "to answer what is currently true about a company.")
    bq.client.create_table(table)
    logger.info(f"[Archive] created {table_id} (append-only)")
    return table_id


def latest_hashes(bq) -> Dict[str, str]:
    """The most recent archived hash per company, so we only append changes."""
    table_id = ensure_archive_table(bq)
    try:
        rows = bq.client.query(f"""
            SELECT name, row_hash FROM (
                SELECT name, row_hash,
                       ROW_NUMBER() OVER (PARTITION BY name ORDER BY archived_at DESC) AS rn
                FROM `{table_id}`
            ) WHERE rn = 1
        """).result()
        return {r.name: r.row_hash for r in rows}
    except Exception as e:
        logger.warning(f"[Archive] could not read existing hashes: {e}")
        return {}


def latest_versions(bq) -> Dict[str, int]:
    table_id = ensure_archive_table(bq)
    try:
        rows = bq.client.query(
            f"SELECT name, MAX(version) AS v FROM `{table_id}` GROUP BY name").result()
        return {r.name: int(r.v or 0) for r in rows}
    except Exception:
        return {}


def archive_targets(bq, change_note: str = "", force: bool = False,
                    dry_run: bool = False) -> Dict:
    """Append the current state of every company whose data has changed.

    force=True re-archives every row regardless (used for the very first
    baseline, or before a migration when you want a guaranteed full snapshot).
    dry_run reports what it would write and writes nothing.
    """
    from google.cloud import bigquery

    table_id = ensure_archive_table(bq)
    rows = bq.get_universe()          # full rows, every column
    if not rows:
        return {"status": "Error", "detail": "No companies read from targets."}

    known = {} if force else latest_hashes(bq)
    versions = latest_versions(bq)
    now = datetime.now(timezone.utc).isoformat()

    to_write, unchanged = [], 0
    for r in rows:
        name = r.get("name")
        if not name:
            continue
        h = row_hash(r)
        if not force and known.get(name) == h:
            unchanged += 1
            continue
        to_write.append({
            "archived_at": now,
            "name": name,
            "row_hash": h,
            "change_note": change_note or ("baseline" if name not in known else "changed"),
            "version": versions.get(name, 0) + 1,
            # default=str so dates/Decimals never break the write
            "row_json": json.dumps(r, sort_keys=True, default=str),
        })

    if dry_run:
        return {
            "status": "Preview", "dry_run": True,
            "companies_in_targets": len(rows),
            "would_append": len(to_write),
            "unchanged": unchanged,
            "first_time_archived": sum(1 for w in to_write if w["version"] == 1),
            "message": "Nothing was written. Re-run with dry_run=0 to append.",
        }

    written = 0
    if to_write:
        # load_table_from_json APPENDS. There is no update path in this module
        # by design — the write mode is pinned to APPEND explicitly so a future
        # edit cannot quietly turn this into a truncate.
        job = bq.client.load_table_from_json(
            to_write, table_id,
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                schema_update_options=[],
            ))
        job.result()
        written = len(to_write)

    logger.info(f"[Archive] appended {written} row(s), {unchanged} unchanged")
    return {
        "status": "Success", "dry_run": False,
        "companies_in_targets": len(rows),
        "appended": written,
        "unchanged": unchanged,
        "first_time_archived": sum(1 for w in to_write if w["version"] == 1),
        "archived_at": now,
        "message": (f"Appended {written} version(s) for {len(rows)} companies "
                    f"({unchanged} unchanged since last archive)."),
    }


def company_history(bq, name: str, limit: int = 50) -> List[Dict]:
    """Every archived version of one company, newest first. The only read this
    module offers, and it is for human inspection, not for rendering pages."""
    table_id = ensure_archive_table(bq)
    from google.cloud import bigquery
    rows = bq.client.query(
        f"""SELECT CAST(archived_at AS STRING) AS archived_at, version, row_hash,
                   change_note, row_json
            FROM `{table_id}`
            WHERE LOWER(name) = LOWER(@name)
            ORDER BY archived_at DESC
            LIMIT {max(1, int(limit))}""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name)])).result()

    out = []
    prev = None
    for r in rows:
        try:
            data = json.loads(r.row_json)
        except Exception:
            data = {}
        entry = {"archived_at": r.archived_at, "version": r.version,
                 "row_hash": r.row_hash, "change_note": r.change_note, "row": data}
        out.append(entry)
    # Field-level diff against the NEXT (older) version, so it reads as
    # "this is what changed at this point".
    for i, entry in enumerate(out):
        older = out[i + 1]["row"] if i + 1 < len(out) else None
        if older is None:
            entry["changed_fields"] = []
            continue
        changed = []
        for k in set(entry["row"]) | set(older):
            a, b = older.get(k), entry["row"].get(k)
            if str(a or "") != str(b or "") and k not in _NOT_A_CHANGE:
                changed.append({"field": k, "from": a, "to": b})
        entry["changed_fields"] = sorted(changed, key=lambda c: c["field"])
    return out


def export_tables_to_gcs(bq, bucket: str, prefix: str = "") -> Dict:
    """Extract every table straight from BigQuery to GCS as gzipped newline
    JSON. The data never passes through this service, so size is irrelevant.

    Paths are dated and include the run timestamp, so an export can never
    overwrite an earlier one even if run twice in a minute.
    """
    from google.cloud import bigquery

    if not bucket:
        return {"status": "Error", "detail": "No backup bucket configured (BACKUP_BUCKET)."}

    stamp = datetime.now(timezone.utc)
    day = stamp.strftime("%Y/%m/%d")
    run = stamp.strftime("%Y%m%dT%H%M%SZ")
    base = f"gs://{bucket}/{prefix.strip('/') + '/' if prefix else ''}{day}/{run}"

    cfg = bigquery.ExtractJobConfig(
        destination_format=bigquery.DestinationFormat.NEWLINE_DELIMITED_JSON,
        compression=bigquery.Compression.GZIP,
    )

    exported, failed = [], []
    for t in EXPORT_TABLES:
        src = f"{bq.project_id}.{bq.dataset_id}.{t}"
        # Wildcard so BigQuery can shard large tables itself.
        dest = f"{base}/{t}/{t}-*.json.gz"
        try:
            bq.client.get_table(src)
        except Exception:
            continue          # table not created yet — not an error
        try:
            job = bq.client.extract_table(src, dest, job_config=cfg)
            job.result()
            exported.append({"table": t, "uri": dest})
            logger.info(f"[Backup] exported {t} -> {dest}")
        except Exception as e:
            failed.append({"table": t, "error": str(e)})
            logger.error(f"[Backup] export failed for {t}: {e}")

    return {
        "status": "Success" if exported and not failed else ("Partial" if exported else "Error"),
        "bucket": bucket,
        "run": run,
        "path": base,
        "exported": exported,
        "failed": failed,
        "message": (f"Exported {len(exported)} table(s) to {base}"
                    + (f"; {len(failed)} FAILED" if failed else "")),
    }
