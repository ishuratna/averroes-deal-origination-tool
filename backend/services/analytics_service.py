"""
Retention-proof pipeline analytics.

Design (mirrors the single-source-of-truth doctrine):
- `analytics_ledger` is an APPEND-ONLY fact table: one row per (company, event)
  with the EARLIEST time the event happened. Once a company has ever reached a
  stage, that fact survives deletion, merging or re-statusing of the targets
  row. Event truth: first_at is when the event happened, never when we synced.
- Events come from three surviving sources, merged idempotently:
    targets      -> stored (ingested_at), per-stage first-entry stamps,
                    current status
    activity_log -> every status_change ever logged
    email_log    -> emailed (direction 'sent'), replied (direction 'received')
- `analytics_snapshots` stores one JSON row per day so the Analytics page can
  chart how the funnel moves over time.
- The Internal Test company is excluded everywhere (source = 'Internal Test').

"Ever" counts come from the ledger; "current" counts from live targets rows.
Response rate = companies that ever replied / companies we ever emailed.
"""
import json
import logging
from typing import Dict, List

from google.cloud import bigquery

logger = logging.getLogger(__name__)

# Stage events tracked in the ledger (stored + funnel + terminal states).
STAGE_EVENTS = [
    "Qualified", "Engaged", "Contacted", "Meeting", "DD",
    "Offer", "Won", "Lost", "Not a Fit", "Under Review",
]
# Display order for the funnel (Contacted is shown as "Responded" in the UI).
FUNNEL_ORDER = [
    "Qualified", "Engaged", "Contacted", "Meeting", "DD", "Offer", "Won", "Lost",
]


def _ledger_id(bq_handler) -> str:
    return f"{bq_handler.project_id}.{bq_handler.dataset_id}.analytics_ledger"


def _snapshots_id(bq_handler) -> str:
    return f"{bq_handler.project_id}.{bq_handler.dataset_id}.analytics_snapshots"


def ensure_tables(bq_handler):
    """Create ledger + snapshots tables if missing. Idempotent."""
    if not bq_handler.client:
        return
    try:
        bq_handler.client.get_table(_ledger_id(bq_handler))
    except Exception:
        bq_handler.client.create_table(bigquery.Table(_ledger_id(bq_handler), schema=[
            bigquery.SchemaField("company_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("company_name", "STRING"),
            bigquery.SchemaField("event", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("first_at", "TIMESTAMP"),
        ]))
        logger.info("Created analytics_ledger table")
    try:
        bq_handler.client.get_table(_snapshots_id(bq_handler))
    except Exception:
        bq_handler.client.create_table(bigquery.Table(_snapshots_id(bq_handler), schema=[
            bigquery.SchemaField("snap_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("payload", "STRING"),
            bigquery.SchemaField("written_at", "TIMESTAMP"),
        ]))
        logger.info("Created analytics_snapshots table")


def ledger_sync(bq_handler) -> int:
    """One idempotent MERGE that captures every (company, event) fact from all
    sources. Keeps the EARLIEST first_at (event truth). Never deletes."""
    if not bq_handler.client:
        return 0
    ensure_tables(bq_handler)
    targets = bq_handler.table_id
    activity = bq_handler.activity_table_id
    email = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"
    stage_list = ", ".join(f"'{s}'" for s in STAGE_EVENTS)

    stamp_unions = "\n".join(
        f"""UNION ALL
            SELECT LOWER(name) AS key, name, '{stage}' AS event, {col} AS ts
            FROM `{targets}` WHERE {col} IS NOT NULL AND IFNULL(source,'') != 'Internal Test'"""
        for stage, col in bq_handler.STAGE_TIMESTAMP_COLS.items())

    query = f"""
    MERGE `{_ledger_id(bq_handler)}` T
    USING (
        SELECT key, ANY_VALUE(name) AS name, event, MIN(ts) AS first_at
        FROM (
            -- every company ever stored
            SELECT LOWER(name) AS key, name, 'stored' AS event,
                   IFNULL(ingested_at, CURRENT_TIMESTAMP()) AS ts
            FROM `{targets}` WHERE IFNULL(source,'') != 'Internal Test'
            {stamp_unions}
            UNION ALL
            -- current status (covers Engaged / Under Review / Not a Fit, which
            -- have no dedicated stamp column)
            SELECT LOWER(name), name, status,
                   IFNULL(stage_entered_at, IFNULL(ingested_at, CURRENT_TIMESTAMP()))
            FROM `{targets}`
            WHERE status IN ({stage_list}) AND IFNULL(source,'') != 'Internal Test'
            UNION ALL
            -- full status-change history (survives row deletion)
            SELECT LOWER(company_name), company_name, new_status, created_at
            FROM `{activity}`
            WHERE action_type = 'status_change' AND new_status IN ({stage_list})
              AND LOWER(company_name) NOT IN
                  (SELECT LOWER(name) FROM `{targets}` WHERE source = 'Internal Test')
            UNION ALL
            -- true inbound-reply evidence from the targets row itself
            -- (last_reply_at is only ever stamped by an actual received email)
            SELECT LOWER(name), name, 'replied', last_reply_at
            FROM `{targets}`
            WHERE last_reply_at IS NOT NULL AND IFNULL(source,'') != 'Internal Test'
            UNION ALL
            -- outreach + replies (survives row deletion)
            SELECT LOWER(entity_name), entity_name,
                   IF(direction = 'sent', 'emailed', 'replied'), sent_at
            FROM `{email}`
            WHERE entity_type = 'company' AND IFNULL(entity_name, '') != ''
              AND direction IN ('sent', 'received') AND sent_at IS NOT NULL
              AND LOWER(entity_name) NOT IN
                  (SELECT LOWER(name) FROM `{targets}` WHERE source = 'Internal Test')
        )
        WHERE key IS NOT NULL AND key != '' AND event IS NOT NULL
        GROUP BY key, event
    ) S ON T.company_key = S.key AND T.event = S.event
    WHEN MATCHED AND S.first_at < T.first_at THEN UPDATE SET first_at = S.first_at
    WHEN NOT MATCHED THEN
        INSERT (company_key, company_name, event, first_at)
        VALUES (S.key, S.name, S.event, S.first_at)
    """
    job = bq_handler.client.query(query)
    job.result()
    n = int(job.num_dml_affected_rows or 0)
    logger.info(f"[Analytics] ledger sync: {n} facts added/updated")
    return n


def compute_stats(bq_handler) -> Dict:
    """Everything the Analytics page needs, in one dict."""
    if not bq_handler.client:
        return {}
    ensure_tables(bq_handler)
    targets = bq_handler.table_id
    email = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"

    ever: Dict[str, int] = {}
    for r in bq_handler.client.query(
            f"SELECT event, COUNT(*) AS n FROM `{_ledger_id(bq_handler)}` GROUP BY event").result():
        ever[r.event] = int(r.n)

    current: Dict[str, int] = {}
    total_current = 0
    for r in bq_handler.client.query(
            f"""SELECT IFNULL(status, 'Unset') AS s, COUNT(*) AS n FROM `{targets}`
                WHERE IFNULL(source,'') != 'Internal Test' GROUP BY s""").result():
        current[r.s] = int(r.n)
        total_current += int(r.n)

    emailed_ever = ever.get("emailed", 0)
    replied_ever = ever.get("replied", 0)

    weekly: List[Dict] = []
    try:
        for r in bq_handler.client.query(
                f"""SELECT FORMAT_DATE('%Y-%m-%d', DATE_TRUNC(DATE(sent_at), WEEK(MONDAY))) AS wk,
                           COUNTIF(direction = 'sent') AS sent,
                           COUNTIF(direction = 'received') AS received
                    FROM `{email}`
                    WHERE entity_type = 'company' AND sent_at IS NOT NULL
                      AND DATE(sent_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 12 WEEK)
                    GROUP BY wk ORDER BY wk""").result():
            weekly.append({"week": r.wk, "sent": int(r.sent), "received": int(r.received)})
    except Exception as e:
        logger.warning(f"[Analytics] weekly email stats failed: {e}")

    # SEMANTICS FIX: stage history is unreliable for the outreach stages
    # (stored 'Contacted' historically meant "WE contacted them" before it was
    # redefined as Responded; Engaged predates part of the send history and
    # has no permanent stamp column). Ever-counts for both come from email
    # EVIDENCE instead, which is deletion-proof and semantically true:
    #   Engaged ever   = companies we ever emailed ('emailed' event)
    #   Responded ever = companies that ever replied ('replied' event +
    #                    last_reply_at stamps)
    _EVIDENCE = {"Engaged": "emailed", "Contacted": "replied"}
    funnel = [{
        "stage": s,
        "ever": ever.get(_EVIDENCE.get(s, s), 0),
        "current": current.get(s, 0),
    } for s in FUNNEL_ORDER]

    return {
        "stored_ever": ever.get("stored", 0),
        "stored_current": total_current,
        "funnel": funnel,
        "not_a_fit_ever": ever.get("Not a Fit", 0),
        "not_a_fit_current": current.get("Not a Fit", 0),
        "emailed_ever": emailed_ever,
        "replied_ever": replied_ever,
        "response_rate": round(replied_ever / emailed_ever, 4) if emailed_ever else None,
        "weekly_emails": weekly,
    }


def write_snapshot(bq_handler, stats: Dict):
    """Upsert today's snapshot (last write of the day wins)."""
    if not bq_handler.client or not stats:
        return
    payload = json.dumps({k: v for k, v in stats.items() if k != "weekly_emails"})
    bq_handler.client.query(
        f"""MERGE `{_snapshots_id(bq_handler)}` T
            USING (SELECT CURRENT_DATE() AS d) S ON T.snap_date = S.d
            WHEN MATCHED THEN UPDATE SET payload = @p, written_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (snap_date, payload, written_at)
                VALUES (S.d, @p, CURRENT_TIMESTAMP())""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("p", "STRING", payload),
        ])).result()


def get_snapshots(bq_handler, days: int = 180) -> List[Dict]:
    """Daily snapshot series, oldest first, for trend charts."""
    if not bq_handler.client:
        return []
    out = []
    try:
        for r in bq_handler.client.query(
                f"""SELECT FORMAT_DATE('%Y-%m-%d', snap_date) AS d, payload
                    FROM `{_snapshots_id(bq_handler)}`
                    WHERE snap_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
                    ORDER BY snap_date""").result():
            try:
                out.append({"date": r.d, **json.loads(r.payload or "{}")})
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[Analytics] snapshot read failed: {e}")
    return out


def refresh_and_stats(bq_handler, force: bool = False) -> Dict:
    """Main entry: sync ledger + write snapshot at most once per day
    (or on force), then return stats + trend series."""
    stats = {}
    try:
        have_today = False
        if not force:
            rows = list(bq_handler.client.query(
                f"""SELECT COUNT(*) AS n FROM `{_snapshots_id(bq_handler)}`
                    WHERE snap_date = CURRENT_DATE()""").result())
            have_today = bool(rows and int(rows[0].n) > 0)
        if force or not have_today:
            ledger_sync(bq_handler)
            stats = compute_stats(bq_handler)
            write_snapshot(bq_handler, stats)
        else:
            stats = compute_stats(bq_handler)
    except Exception as e:
        logger.error(f"[Analytics] refresh failed: {e}")
        try:
            stats = compute_stats(bq_handler)
        except Exception:
            stats = {}
    stats["snapshots"] = get_snapshots(bq_handler)
    return stats
