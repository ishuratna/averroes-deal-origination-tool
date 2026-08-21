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
    "Qualified", "Contacted", "Responded", "Meeting", "DD",
    "Offer", "Won", "Lost", "Not a Fit", "Under Review",
]
# Display order for the funnel. Stored values now match what is displayed:
# Contacted = we emailed them, Responded = they replied.
FUNNEL_ORDER = [
    "Qualified", "Contacted", "Responded", "Meeting", "DD", "Offer", "Won", "Lost",
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


def _facts_sql(bq_handler) -> str:
    """Every (company, event) fact, derived from EVIDENCE only.

    Three sources, in descending order of trustworthiness:
      targets stamps  — per-stage first-entry columns, written once and never
                        overwritten. A stage was genuinely reached.
      activity_log    — every status_change ever logged. Survives row deletion.
      email_log       — the emails themselves. The most durable evidence there is.

    DELIBERATELY NOT A SOURCE: the company's CURRENT status. It used to be, and
    that is how 18 false facts became permanent: while a migration had those
    companies wrongly sitting in Responded, the ledger banked "ever reached
    Responded" for each. Correcting the live rows could not correct the ledger,
    because it is append-only. A snapshot of a mutable field is not evidence that
    an event happened. The only exception is a stage with NO timestamp column of
    its own (Not a Fit, Under Review), where the current status is the only record
    that exists — and those are not funnel stages, so a wrong one misleads nobody
    about conversion.
    """
    targets = bq_handler.table_id
    activity = bq_handler.activity_table_id
    email = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"
    # Only stages that have no first-entry stamp of their own.
    unstamped = [s for s in STAGE_EVENTS if s not in bq_handler.STAGE_TIMESTAMP_COLS]
    unstamped_list = ", ".join(f"'{s}'" for s in unstamped)
    stage_list = ", ".join(f"'{s}'" for s in STAGE_EVENTS)
    # 'replied' means the SAME thing here as everywhere else. Built from the
    # shared constant so an autoresponder or a bounce can never be counted as a
    # reply in the analytics while the pipeline correctly ignores it. That
    # mismatch inflated the headline response rate.
    non_reply = ", ".join(f"'{c}'" for c in bq_handler.NON_REPLY_CLASSES)

    stamp_unions = "\n".join(
        f"""UNION ALL
            SELECT LOWER(name) AS key, name, '{stage}' AS event, {col} AS ts
            FROM `{targets}` WHERE {col} IS NOT NULL AND IFNULL(source,'') != 'Internal Test'"""
        for stage, col in bq_handler.STAGE_TIMESTAMP_COLS.items())

    return f"""
        SELECT key, ANY_VALUE(name) AS name, event, MIN(ts) AS first_at
        FROM (
            -- every company ever stored
            SELECT LOWER(name) AS key, name, 'stored' AS event,
                   IFNULL(ingested_at, CURRENT_TIMESTAMP()) AS ts
            FROM `{targets}` WHERE IFNULL(source,'') != 'Internal Test'
            {stamp_unions}
            UNION ALL
            -- Stages with no stamp column of their own. Current status is the
            -- only record they have.
            SELECT LOWER(name), name, status,
                   IFNULL(stage_entered_at, IFNULL(ingested_at, CURRENT_TIMESTAMP()))
            FROM `{targets}`
            WHERE status IN ({unstamped_list}) AND IFNULL(source,'') != 'Internal Test'
            UNION ALL
            -- full status-change history (survives row deletion)
            SELECT LOWER(company_name), company_name, new_status, created_at
            FROM `{activity}`
            WHERE action_type = 'status_change' AND new_status IN ({stage_list})
              AND LOWER(company_name) NOT IN
                  (SELECT LOWER(name) FROM `{targets}` WHERE source = 'Internal Test')
            UNION ALL
            -- We emailed them: an outbound message exists.
            SELECT LOWER(entity_name), entity_name, 'emailed', sent_at
            FROM `{email}`
            WHERE entity_type = 'company' AND IFNULL(entity_name, '') != ''
              AND direction = 'sent' AND sent_at IS NOT NULL
              AND LOWER(entity_name) NOT IN
                  (SELECT LOWER(name) FROM `{targets}` WHERE source = 'Internal Test')
            UNION ALL
            -- They replied: an inbound message that is NOT an autoresponder and
            -- NOT a bounce. Same definition the pipeline uses.
            SELECT LOWER(entity_name), entity_name, 'replied', sent_at
            FROM `{email}`
            WHERE entity_type = 'company' AND IFNULL(entity_name, '') != ''
              AND direction = 'received' AND sent_at IS NOT NULL
              AND IFNULL(classification, '') NOT IN ({non_reply})
              AND LOWER(entity_name) NOT IN
                  (SELECT LOWER(name) FROM `{targets}` WHERE source = 'Internal Test')
        )
        WHERE key IS NOT NULL AND key != '' AND event IS NOT NULL
        GROUP BY key, event
    """


def ledger_sync(bq_handler) -> int:
    """One idempotent MERGE that captures every (company, event) fact from all
    sources. Keeps the EARLIEST first_at (event truth). Never deletes."""
    if not bq_handler.client:
        return 0
    ensure_tables(bq_handler)

    query = f"""
    MERGE `{_ledger_id(bq_handler)}` T
    USING (
        {_facts_sql(bq_handler)}
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


def ledger_rebuild(bq_handler, dry_run: bool = True) -> Dict:
    """Recompute every fact for companies that still exist, from evidence.

    WHY A REBUILD IS LEGITIMATE AND NOT A DOCTRINE VIOLATION. The ledger is a
    DERIVED CACHE. The primary sources are targets, activity_log and email_log,
    and they are untouched here. Append-only is the right shape for an archive of
    primary data (see archive_service, which never deletes anything); it is the
    wrong shape for a cache of derived conclusions, because a wrong conclusion
    then becomes permanent. That happened: while a migration wrongly held 18
    companies in Responded, the ledger banked "ever reached Responded" for each,
    and fixing the live rows could not undo it.

    WHAT IS PRESERVED. Only facts for companies still present in targets are
    recomputed. A company deleted or renamed away keeps every fact it ever had,
    which is the whole reason the ledger exists — the counts must survive a row
    disappearing. So this restores correctness without giving up retention.

    Defaults to a dry run reporting the delta per event.
    """
    if not bq_handler.client:
        return {}
    ensure_tables(bq_handler)
    ledger = _ledger_id(bq_handler)
    targets = bq_handler.table_id

    def _counts(sql: str) -> Dict[str, int]:
        return {r.event: int(r.n) for r in bq_handler.client.query(sql).result()}

    before = _counts(f"SELECT event, COUNT(*) AS n FROM `{ledger}` GROUP BY event")
    # What the evidence actually supports, for live companies only.
    after_live = _counts(f"""
        SELECT event, COUNT(*) AS n FROM ({_facts_sql(bq_handler)})
        GROUP BY event""")
    # Facts we will keep untouched because the company is gone from targets.
    orphans = _counts(f"""
        SELECT event, COUNT(*) AS n FROM `{ledger}`
        WHERE company_key NOT IN (SELECT LOWER(name) FROM `{targets}`)
        GROUP BY event""")

    expected = {e: after_live.get(e, 0) + orphans.get(e, 0)
                for e in set(after_live) | set(orphans)}
    delta = {e: expected.get(e, 0) - before.get(e, 0)
             for e in set(expected) | set(before)
             if expected.get(e, 0) != before.get(e, 0)}

    result = {
        "dry_run": dry_run,
        "before": before,
        "expected_after": expected,
        "delta": delta,
        "preserved_for_deleted_companies": sum(orphans.values()),
    }
    if dry_run:
        result["message"] = ("Nothing was changed. Any negative delta is a fact the "
                             "evidence does not support. Re-run with dry_run=0 to apply.")
        return result

    # Delete then re-derive, for live companies only. Not wrapped in a
    # transaction: BigQuery DML is atomic per statement, and if the insert failed
    # the next nightly ledger_sync would rebuild what was removed, because every
    # fact here is derivable from sources that were never touched.
    bq_handler.client.query(
        f"""DELETE FROM `{ledger}`
            WHERE company_key IN (SELECT LOWER(name) FROM `{targets}`)""").result()
    job = bq_handler.client.query(
        f"""INSERT INTO `{ledger}` (company_key, company_name, event, first_at)
            SELECT key, name, event, first_at FROM ({_facts_sql(bq_handler)})""")
    job.result()
    result["rows_written"] = int(job.num_dml_affected_rows or 0)
    result["after"] = _counts(f"SELECT event, COUNT(*) AS n FROM `{ledger}` GROUP BY event")
    result["message"] = (f"Rebuilt {result['rows_written']} facts from evidence; "
                         f"{result['preserved_for_deleted_companies']} preserved for "
                         f"companies no longer in targets.")
    logger.info(f"[Analytics] ledger rebuild: {result['message']}")
    return result


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

    # Current counts per stage, plus the LIVE evidence for each row.
    #
    # Evidence here is read from email_log through the shared genuine-reply
    # predicate, NOT from the ledger and NOT from last_reply_at:
    #   * the ledger holds EVER facts, so a company that replied once and was
    #     later moved back would still look like it had a reply now;
    #   * last_reply_at was historically stamped by out-of-office replies, before
    #     autoresponders were understood.
    # The whole point of these numbers is to answer "does this stage agree with
    # the evidence RIGHT NOW", so they have to use the same definition of a reply
    # that the pipeline uses. Otherwise the alarm below can fire on a
    # disagreement between two of our own measurements rather than a real fault.
    #
    # reply_exempt companies are excluded from the reply evidence test: they sit
    # in Responded BY AN EXPLICIT HUMAN DECISION, with no logged reply, so
    # counting them as inconsistent would make the alarm permanently non-zero and
    # therefore useless.
    current: Dict[str, int] = {}
    with_email: Dict[str, int] = {}
    with_reply: Dict[str, int] = {}
    total_current = 0
    for r in bq_handler.client.query(
            f"""WITH ev AS ({bq_handler._genuine_reply_sql()})
                SELECT IFNULL(t.status, 'Unset') AS s, COUNT(*) AS n,
                       COUNTIF(t.outreach_sent_at IS NOT NULL
                               OR IFNULL(ev.sent_count, 0) > 0) AS we,
                       COUNTIF(IFNULL(ev.recv_count, 0) > 0
                               OR t.reply_exempt_at IS NOT NULL) AS wr
                FROM `{targets}` t
                LEFT JOIN ev ON ev.entity_name = t.name
                WHERE IFNULL(t.source,'') != 'Internal Test' GROUP BY s""").result():
        current[r.s] = int(r.n)
        with_email[r.s] = int(r.we)
        with_reply[r.s] = int(r.wr)
        total_current += int(r.n)

    emailed_ever = ever.get("emailed", 0)
    replied_ever = ever.get("replied", 0)

    # ── Activity series for the page's charts (per Ishu, 20 Aug 2026) ────────
    # Daily for the working week, monthly for the long arc. All from primary
    # sources (email_log, activity_log, targets), zero AI.

    # Emails per day, last 7 days.
    daily_emails: List[Dict] = []
    try:
        for r in bq_handler.client.query(
                f"""SELECT FORMAT_DATE('%Y-%m-%d', DATE(sent_at)) AS d,
                           COUNTIF(direction = 'sent') AS sent,
                           COUNTIF(direction = 'received') AS received
                    FROM `{email}`
                    WHERE entity_type = 'company' AND sent_at IS NOT NULL
                      AND DATE(sent_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
                    GROUP BY d ORDER BY d""").result():
            daily_emails.append({"day": r.d, "sent": int(r.sent), "received": int(r.received)})
    except Exception as e:
        logger.warning(f"[Analytics] daily email stats failed: {e}")

    # SmartFill runs per day vs companies that became Qualified that day, last 7
    # days. Qualified is drawn INSIDE the SmartFill column on the page - not a
    # strict mathematical subset (a hand-move to Qualified counts too), but the
    # practical reading is "of the enrichment volume, how much became pipeline".
    daily_enrichment: List[Dict] = []
    try:
        activity = bq_handler.activity_table_id
        rows = {r.d: {"smartfills": int(r.n)} for r in bq_handler.client.query(
            f"""SELECT FORMAT_DATE('%Y-%m-%d', DATE(created_at)) AS d, COUNT(*) AS n
                FROM `{activity}`
                WHERE action_type IN ('smartfill', 'smartenrich', 'smartfill_gated')
                  AND DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
                  AND IFNULL(company_name, '') NOT IN ('auto-run', 'chat')
                GROUP BY d""").result()}
        for r in bq_handler.client.query(
                f"""SELECT FORMAT_DATE('%Y-%m-%d', DATE(first_at)) AS d, COUNT(*) AS n
                    FROM `{_ledger_id(bq_handler)}`
                    WHERE event = 'Qualified'
                      AND DATE(first_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
                    GROUP BY d""").result():
            rows.setdefault(r.d, {})["qualified"] = int(r.n)
        daily_enrichment = [{"day": d, "smartfills": v.get("smartfills", 0),
                             "qualified": v.get("qualified", 0)}
                            for d, v in sorted(rows.items())]
    except Exception as e:
        logger.warning(f"[Analytics] daily enrichment stats failed: {e}")

    # Universe growth: cumulative companies by month, from ingested_at. Uploads
    # show as visible jumps, which is the honest shape of how it was built.
    universe_growth: List[Dict] = []
    try:
        running = 0
        for r in bq_handler.client.query(
                f"""SELECT FORMAT_DATE('%Y-%m', DATE(ingested_at)) AS m, COUNT(*) AS n
                    FROM `{targets}`
                    WHERE ingested_at IS NOT NULL AND IFNULL(source,'') != 'Internal Test'
                    GROUP BY m ORDER BY m""").result():
            running += int(r.n)
            universe_growth.append({"month": r.m, "added": int(r.n), "cumulative": running})
    except Exception as e:
        logger.warning(f"[Analytics] universe growth failed: {e}")

    # EVER-COUNTS for the two outreach stages come from email EVIDENCE, not from
    # stage history, and deliberately so:
    #   Contacted ever = companies we ever emailed  ('emailed' event)
    #   Responded ever = companies that ever genuinely replied ('replied' event)
    #
    # Stage history for these two is unreliable across the rename (the old
    # 'Engaged' became Contacted and the old 'Contacted' became Responded, and the
    # rename mislabelled 18 rows). The emails themselves carry no such ambiguity:
    # an outbound message is an outbound message whatever the stage was called.
    # Since 'replied' now excludes autoresponders and bounces, these two lines are
    # exactly "how many did we write to" and "how many wrote back".
    _EVIDENCE = {"Contacted": "emailed", "Responded": "replied"}

    # CURRENT COUNTS COME STRAIGHT FROM STATUS, for every stage.
    #
    # This used to substitute email evidence for the Contacted and Responded
    # counts, because status could not be trusted while the rename was half
    # applied. That produced a THIRD number for Responded: the board counted
    # status, the Responded page counted the email log, and this chart counted
    # evidence, so no two agreed. Status is now kept honest by the reply rule
    # (bq_handler.reconcile_reply_stages), so every surface reads status and the
    # numbers reconcile. Rows whose evidence disagrees are reported below as
    # inconsistencies rather than quietly changing the count.
    # CUMULATIVE funnel (the page's word, per Ishu): how many companies have
    # EVER reached each stage, with each stage's conversion from the previous
    # one precomputed here so the page renders numbers, not maths. "current"
    # is no longer shown on the page but kept in the payload for the snapshot
    # history and any future need.
    funnel = []
    prev = None
    for s in FUNNEL_ORDER:
        cum = ever.get(_EVIDENCE.get(s, s), 0)
        funnel.append({
            "stage": s,
            "cumulative": cum,
            "ever": cum,                      # legacy name, kept for snapshots
            "current": current.get(s, 0),
            # % of the previous stage that converted. None for the first stage
            # and whenever the previous stage is 0 (a rate against nothing).
            "conversion_pct": round(cum / prev * 100, 1) if prev else None,
        })
        prev = cum if cum else prev

    # Stage/evidence disagreements, surfaced for the page (0 = clean):
    inconsistencies = {
        # sitting in Contacted but no outbound email on record
        "contacted_without_email": current.get("Contacted", 0) - with_email.get("Contacted", 0),
        # sitting in Responded but no inbound reply on record
        "responded_without_reply": current.get("Responded", 0) - with_reply.get("Responded", 0),
        # replied although we never emailed them (inbound-first threads);
        # this is why Responded-ever is not mathematically forced to be a
        # subset of Contacted-ever
        "replied_never_emailed": max(0, ever.get("replied", 0) - ever.get("emailed", 0)),
    }

    return {
        "stored_ever": ever.get("stored", 0),
        "stored_current": total_current,
        "funnel": funnel,
        "not_a_fit_ever": ever.get("Not a Fit", 0),
        "not_a_fit_current": current.get("Not a Fit", 0),
        "emailed_ever": emailed_ever,
        "replied_ever": replied_ever,
        "response_rate": round(replied_ever / emailed_ever, 4) if emailed_ever else None,
        "daily_emails": daily_emails,
        "daily_enrichment": daily_enrichment,
        "universe_growth": universe_growth,
        "inconsistencies": inconsistencies,
    }


def write_snapshot(bq_handler, stats: Dict):
    """Upsert today's snapshot (last write of the day wins)."""
    if not bq_handler.client or not stats:
        return
    # Activity series are recomputable from primary sources any time; snapshots
    # exist to preserve the FUNNEL's history, so only that (and the scalars).
    _RECOMPUTABLE = {"weekly_emails", "daily_emails", "daily_enrichment", "universe_growth"}
    payload = json.dumps({k: v for k, v in stats.items() if k not in _RECOMPUTABLE})
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
