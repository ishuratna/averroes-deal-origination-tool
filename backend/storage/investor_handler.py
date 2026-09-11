"""
Investor (LP) database handler — BigQuery.

Second database alongside `targets`: potential LPs to invest through Averroes —
funds of funds, family offices, HNWIs/UHNWIs, and other private-capital investors.

Relationship stages: Identified → Researched → Contacted → Meeting → Committed / Passed
"""
import json
import uuid
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone

from google.cloud import bigquery

logger = logging.getLogger(__name__)

# THE INVESTOR LOOP mirrors the founder loop (per Ishu, 8 Sep 2026):
#   Identified   in the Investor Universe, not yet researched
#   Researched   InvestorFill done (the LP equivalent of Qualified)
#   Contacted    we emailed them, no genuine reply yet
#   Responded    they genuinely replied (autoresponders and bounces never count)
#   Meeting / Committed  real work, never changed automatically
#   Passed       closed out, with a park reason
#   Talk Later   parked, with a park reason; resurfaces later
INVESTOR_STAGES = ["Identified", "Researched", "Contacted", "Responded", "Meeting",
                   "Committed", "Passed", "Talk Later"]
INVESTOR_PARKED = ("Passed", "Talk Later")
# First-entry stamps, set once and never overwritten (event truth).
INVESTOR_STAGE_STAMPS = {"Contacted": "contacted_at", "Responded": "responded_at",
                         "Meeting": "meeting_at", "Committed": "committed_at"}

INVESTOR_TYPES = [
    "Family Office", "Fund of Funds", "HNWI", "UHNWI",
    "VC", "PE", "Angel", "Corporate", "Sovereign/Institutional",
    # Types produced by the portfolio miner (kept for filtering/intel)
    "Fund", "Agency", "Bank", "Crowdfunding", "Unknown",
]


class InvestorBQHandler:
    """CRUD for the investors table. Mirrors the targets handler pattern."""

    SCHEMA = [
        ("investor_id", "STRING"),
        ("name", "STRING"),
        ("investor_type", "STRING"),        # Family Office / FoF / HNWI / UHNWI / VC / PE / ...
        ("aum_m", "FLOAT64"),               # assets under management, £M
        ("ticket_min_m", "FLOAT64"),        # typical commitment size range, £M
        ("ticket_max_m", "FLOAT64"),
        ("region", "STRING"),               # UK / Europe / KSA / US / ...
        ("hq_city", "STRING"),
        ("hq_country", "STRING"),
        ("website", "STRING"),
        ("description", "STRING"),
        ("contact_name", "STRING"),
        ("contact_email", "STRING"),
        ("linkedin_url", "STRING"),
        ("source", "STRING"),               # Mined from portfolio / PitchBook LP upload / AI search
        ("source_companies", "STRING"),     # portfolio companies in our universe they invest in
        ("status", "STRING"),               # relationship stage
        ("lp_fit_score", "FLOAT64"),        # 0-1 composite
        ("score_geography", "FLOAT64"),     # UK/Europe/KSA
        ("score_pe_appetite", "FLOAT64"),   # private-markets track record
        ("score_ticket_fit", "FLOAT64"),    # £250K-5M commitment range
        ("score_tech_affinity", "FLOAT64"), # B2B software exposure
        ("fit_details", "STRING"),          # JSON explanations
        ("notes", "STRING"),
        # ── PitchBook LP export fields (USD figures) ──
        ("pb_id", "STRING"),                 # PitchBook Limited Partner ID — dedup/update key
        ("aka", "STRING"),                   # also known as
        ("contact_title", "STRING"),
        # "found" (published address) or "inferred" (guessed from the domain).
        # An inferred address must never be presented as a found one.
        ("contact_confidence", "STRING"),
        # The investor gate's verdict (ai/investor_gate.py): where they sit and,
        # when refused, the one sentence explaining why. Stored so the card can
        # show the reason without re-running anything.
        ("gate_region", "STRING"),
        ("gate_unfit_reason", "STRING"),
        # Most recent known investment, for lp_priority's recency dimension.
        ("last_commitment_date", "STRING"),
        # Doctrine 4a on the investor side: did the research come back about the
        # investor we ASKED for? confirmed | unverified | mismatch.
        ("identity_status", "STRING"),
        ("identity_note", "STRING"),
        ("contact_phone", "STRING"),
        ("hq_email", "STRING"),
        ("global_region", "STRING"),         # HQ Global Region (e.g. Europe, Middle East)
        ("year_founded", "INT64"),
        ("strategy_preferences", "STRING"),  # condensed: PE-relevant strategies only
        ("geo_preferences", "STRING"),       # condensed: UK/Europe/ME mandate hits
        ("open_to_first_time", "STRING"),    # Yes / No / ''
        ("num_commitments", "INT64"),
        ("num_active_commitments", "INT64"),
        ("num_pe_commitments", "INT64"),
        ("total_commitments_m", "FLOAT64"),  # $M
        # Commitments breakdown v2 (PitchBook aggregates, $M USD as reported)
        ("total_active_commitments_m", "FLOAT64"),
        ("total_pe_commitments_m", "FLOAT64"),
        ("num_vc_commitments", "INT64"),
        ("total_vc_commitments_m", "FLOAT64"),
        ("sold_secondaries", "STRING"),      # Yes/No — transacts in secondaries
        ("bought_secondaries", "STRING"),
        ("policy_description", "STRING"),    # investment policy (profile view)
        ("other_preferences", "STRING"),
        ("registration_number", "STRING"),   # UK Companies House number where present
        ("pb_last_updated", "STRING"),
        # Companies House registry intelligence (UK entities)
        ("psc_summary", "STRING"),           # who controls the vehicle — UHNWI discovery
        ("officers_summary", "STRING"),      # active directors (principals to contact)
        ("net_assets_m", "FLOAT64"),         # filed net assets, £M — AUM proxy
        # ── The outreach loop (same column names as targets, so the shared
        #    frontend button/modal logic in lib/outreach.ts applies unchanged) ──
        ("outreach_draft_subject", "STRING"), ("outreach_draft_body", "STRING"),
        ("outreach_draft_to", "STRING"), ("outreach_drafted_at", "TIMESTAMP"),
        ("outreach_sent_at", "TIMESTAMP"),   # refreshed on every send
        ("contacted_at", "TIMESTAMP"),       # first send only
        ("responded_at", "TIMESTAMP"), ("meeting_at", "TIMESTAMP"), ("committed_at", "TIMESTAMP"),
        ("stage_entered_at", "TIMESTAMP"),
        ("last_reply_at", "TIMESTAMP"), ("reply_classification", "STRING"),
        ("park_reason", "STRING"), ("park_reason_detail", "STRING"),
        ("bounced_email", "STRING"),         # dead address preserved after a bounce
        # ── Prioritisation for the co-investment raise (ai/lp_priority.py) ──
        ("network_tags", "STRING"),          # comma list: GCC, Bea, Partner, Co-investor, ...
        ("priority_score", "FLOAT64"),       # 0-100, recomputed on every write that changes an input
        ("priority_tier", "STRING"),         # A | B | C | Parked
        ("priority_details", "STRING"),      # JSON: each component's score, weight and why
        # Smart Upload: unmapped source columns preserved as JSON
        ("extra_data", "STRING"),
        ("ingested_at", "TIMESTAMP"),
        ("updated_at", "TIMESTAMP"),
    ]

    # PitchBook fields written on merge: strings fill gaps only; PB IDs/counters always refresh
    _MERGE_FILL_STRINGS = [
        "investor_type", "region", "hq_city", "hq_country", "website", "description",
        "contact_name", "contact_email", "linkedin_url", "aka", "contact_title",
        "contact_phone", "hq_email", "global_region", "strategy_preferences",
        "geo_preferences", "open_to_first_time", "other_preferences", "registration_number",
        "sold_secondaries", "bought_secondaries", "policy_description",
    ]
    _MERGE_FILL_NUMERICS = [
        "aum_m", "ticket_min_m", "ticket_max_m", "year_founded",
        "num_commitments", "num_active_commitments", "num_pe_commitments", "total_commitments_m",
        "total_active_commitments_m", "total_pe_commitments_m",
        "num_vc_commitments", "total_vc_commitments_m",
    ]
    _MERGE_INT_NUMERICS = (
        "year_founded", "num_commitments", "num_active_commitments",
        "num_pe_commitments", "num_vc_commitments",
    )

    def __init__(self, client: Optional[bigquery.Client], project_id: str, dataset_id: str = "averroes_deal_flow"):
        self.client = client
        self.table_id = f"{project_id}.{dataset_id}.investors"
        if self.client:
            try:
                self._ensure_table()
            except Exception as e:
                logger.warning(f"Could not ensure investors table: {e}")

    def _ensure_table(self):
        """Create the investors table if it doesn't exist. Idempotent."""
        try:
            self.client.get_table(self.table_id)
            # Auto-expand: add any missing columns
            table = self.client.get_table(self.table_id)
            existing = {f.name for f in table.schema}
            missing = [(n, t) for n, t in self.SCHEMA if n not in existing]
            if missing:
                new_schema = list(table.schema) + [bigquery.SchemaField(n, t) for n, t in missing]
                table.schema = new_schema
                self.client.update_table(table, ["schema"])
                logger.info(f"Added {len(missing)} columns to investors table")
        except Exception:
            schema = [bigquery.SchemaField(n, t) for n, t in self.SCHEMA]
            table = bigquery.Table(self.table_id, schema=schema)
            self.client.create_table(table)
            logger.info("Created investors table in BigQuery")

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_all(self) -> List[Dict]:
        if not self.client:
            return []
        # Chronological: order by when first added to the database (ingested_at is
        # set once at insert and never modified by merges/enrichment)
        query = f"SELECT * FROM `{self.table_id}` ORDER BY ingested_at ASC, name ASC"
        try:
            rows = [dict(r) for r in self.client.query(query).result()]
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, datetime):
                        r[k] = v.isoformat()
            return rows
        except Exception as e:
            logger.error(f"Failed to load investors: {e}")
            return []

    def get_existing_names(self) -> set:
        if not self.client:
            return set()
        try:
            rows = self.client.query(f"SELECT LOWER(name) AS n FROM `{self.table_id}`").result()
            return {r["n"] for r in rows}
        except Exception as e:
            logger.error(f"Failed to load investor names: {e}")
            return set()

    # ── Writes ────────────────────────────────────────────────────────────────

    def save_investors(self, investors: List[Dict]) -> int:
        """Insert new investors (dedup by name, case-insensitive). Returns inserted count."""
        if not self.client or not investors:
            return 0
        existing = self.get_existing_names()
        now = datetime.now(timezone.utc).isoformat()

        rows = []
        seen_batch = set()
        for inv in investors:
            name = (inv.get("name") or "").strip()
            if not name or name.lower() in existing or name.lower() in seen_batch:
                continue
            seen_batch.add(name.lower())
            rows.append({
                "investor_id": str(uuid.uuid4()),
                "name": name,
                "investor_type": inv.get("investor_type") or "Unknown",
                "aum_m": inv.get("aum_m"),
                "ticket_min_m": inv.get("ticket_min_m"),
                "ticket_max_m": inv.get("ticket_max_m"),
                "region": inv.get("region") or "",
                "hq_city": inv.get("hq_city") or "",
                "hq_country": inv.get("hq_country") or "",
                "website": inv.get("website") or "",
                "description": inv.get("description") or "",
                "contact_name": inv.get("contact_name") or "",
                "contact_email": inv.get("contact_email") or "",
                "linkedin_url": inv.get("linkedin_url") or "",
                "source": inv.get("source") or "Manual",
                "source_companies": inv.get("source_companies") or "",
                "status": inv.get("status") or "Identified",
                "lp_fit_score": inv.get("lp_fit_score"),
                "score_geography": inv.get("score_geography"),
                "score_pe_appetite": inv.get("score_pe_appetite"),
                "score_ticket_fit": inv.get("score_ticket_fit"),
                "score_tech_affinity": inv.get("score_tech_affinity"),
                "fit_details": inv.get("fit_details") or "",
                "notes": inv.get("notes") or "",
                "pb_id": inv.get("pb_id") or "",
                "aka": inv.get("aka") or "",
                "contact_title": inv.get("contact_title") or "",
                "contact_phone": inv.get("contact_phone") or "",
                "hq_email": inv.get("hq_email") or "",
                "global_region": inv.get("global_region") or "",
                "year_founded": inv.get("year_founded"),
                "strategy_preferences": inv.get("strategy_preferences") or "",
                "geo_preferences": inv.get("geo_preferences") or "",
                "open_to_first_time": inv.get("open_to_first_time") or "",
                "num_commitments": inv.get("num_commitments"),
                "num_active_commitments": inv.get("num_active_commitments"),
                "num_pe_commitments": inv.get("num_pe_commitments"),
                "total_commitments_m": inv.get("total_commitments_m"),
                "total_active_commitments_m": inv.get("total_active_commitments_m"),
                "total_pe_commitments_m": inv.get("total_pe_commitments_m"),
                "num_vc_commitments": inv.get("num_vc_commitments"),
                "total_vc_commitments_m": inv.get("total_vc_commitments_m"),
                "sold_secondaries": inv.get("sold_secondaries") or "",
                "bought_secondaries": inv.get("bought_secondaries") or "",
                "policy_description": inv.get("policy_description") or "",
                "other_preferences": inv.get("other_preferences") or "",
                "registration_number": inv.get("registration_number") or "",
                "pb_last_updated": inv.get("pb_last_updated") or "",
                "ingested_at": now,
                "updated_at": now,
            })

        if not rows:
            return 0

        # DML INSERT (not streaming insert_rows_json): streaming-buffered rows
        # cannot be UPDATEd/DELETEd for up to 90 minutes, which broke
        # InvestorFill / relabelling right after upload. DML rows are
        # immediately mutable. Batched to stay under BQ's query-parameter limit.
        string_cols = [
            "investor_id", "name", "investor_type", "region", "hq_city", "hq_country",
            "website", "description", "contact_name", "contact_email", "linkedin_url",
            "source", "source_companies", "status", "fit_details", "notes", "pb_id",
            "aka", "contact_title", "contact_phone", "hq_email", "global_region",
            "strategy_preferences", "geo_preferences", "open_to_first_time",
            "other_preferences", "registration_number", "pb_last_updated",
            "sold_secondaries", "bought_secondaries", "policy_description",
        ]
        float_cols = ["aum_m", "ticket_min_m", "ticket_max_m", "lp_fit_score",
                      "score_geography", "score_pe_appetite", "score_ticket_fit",
                      "score_tech_affinity", "total_commitments_m",
                      "total_active_commitments_m", "total_pe_commitments_m",
                      "total_vc_commitments_m"]
        int_cols = ["year_founded", "num_commitments", "num_active_commitments",
                    "num_pe_commitments", "num_vc_commitments"]
        all_cols = string_cols + float_cols + int_cols

        inserted = 0
        BATCH = 180  # 180 rows × 48 params = 8,640 < BQ's 10,000-parameter limit
        for b in range(0, len(rows), BATCH):
            batch = rows[b:b + BATCH]
            values_sql = []
            params = []
            for i, r in enumerate(batch):
                placeholders = []
                for col in all_cols:
                    pname = f"p{i}_{col}"
                    placeholders.append(f"@{pname}")
                    if col in string_cols:
                        params.append(bigquery.ScalarQueryParameter(pname, "STRING", r.get(col) or ""))
                    elif col in float_cols:
                        params.append(bigquery.ScalarQueryParameter(pname, "FLOAT64", r.get(col)))
                    else:
                        params.append(bigquery.ScalarQueryParameter(pname, "INT64", r.get(col)))
                placeholders.append("CURRENT_TIMESTAMP()")  # ingested_at
                placeholders.append("CURRENT_TIMESTAMP()")  # updated_at
                values_sql.append(f"({', '.join(placeholders)})")

            query = (
                f"INSERT INTO `{self.table_id}` ({', '.join(all_cols)}, ingested_at, updated_at) "
                f"VALUES {', '.join(values_sql)}"
            )
            try:
                self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
                inserted += len(batch)
            except Exception as e:
                logger.error(f"Investor DML insert failed for batch {b}-{b + len(batch)}: {e}")

        logger.info(f"Inserted {inserted} new investors (DML)")
        return inserted

    def upsert_investors(self, investors: List[Dict]) -> Dict:
        """
        Insert new investors; MERGE data into existing ones (PitchBook fills gaps —
        string fields only where currently empty, numerics only where currently null).
        Returns {"inserted": n, "merged": n}.
        """
        if not self.client or not investors:
            return {"inserted": 0, "merged": 0}
        existing = self.get_existing_names()
        new_rows = [i for i in investors if (i.get("name") or "").strip().lower() not in existing]
        to_merge = [i for i in investors if (i.get("name") or "").strip().lower() in existing]

        inserted = self.save_investors(new_rows)
        merged = self.merge_fill_bulk(to_merge)
        logger.info(f"Upsert complete: {inserted} inserted, {merged} merged")
        return {"inserted": inserted, "merged": merged}

    def merge_fill_bulk(self, investors: List[Dict]) -> int:
        """Fill-only MERGE of upload fields into EXISTING rows — one statement
        per batch (per-row UPDATEs take hours at PitchBook scale). Rules match
        _merge_investor: strings fill blanks, numerics fill NULLs, description
        longer-wins, pb_id/pb_last_updated refresh (authoritative). Never
        overwrites an existing value with a thinner one."""
        if not self.client or not investors:
            return 0

        # Source SELECT: extract every merge field from the JSON payload
        sel = ["LOWER(JSON_EXTRACT_SCALAR(j, '$.name')) AS lname"]
        for c in self._MERGE_FILL_STRINGS + ["pb_id", "pb_last_updated"]:
            sel.append(f"JSON_EXTRACT_SCALAR(j, '$.{c}') AS {c}")
        for c in self._MERGE_FILL_NUMERICS:
            bq_type = "INT64" if c in self._MERGE_INT_NUMERICS else "FLOAT64"
            sel.append(f"SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.{c}') AS {bq_type}) AS {c}")
        sel.append("ROW_NUMBER() OVER (PARTITION BY LOWER(JSON_EXTRACT_SCALAR(j, '$.name')) "
                   "ORDER BY JSON_EXTRACT_SCALAR(j, '$.pb_id')) AS rn")

        sets = []
        for c in self._MERGE_FILL_STRINGS:
            if c == "description":  # longer wins — never replace good text with thinner text
                sets.append("description = IF(LENGTH(IFNULL(S.description, '')) > "
                            "LENGTH(IFNULL(T.description, '')), S.description, T.description)")
            else:
                sets.append(f"{c} = IFNULL(NULLIF(T.{c}, ''), S.{c})")
        for c in self._MERGE_FILL_NUMERICS:
            sets.append(f"{c} = IFNULL(T.{c}, S.{c})")
        for c in ("pb_id", "pb_last_updated"):  # PitchBook identifiers always refresh
            sets.append(f"{c} = IFNULL(NULLIF(S.{c}, ''), T.{c})")
        sets.append("updated_at = CURRENT_TIMESTAMP()")

        keys = ["name", "pb_id", "pb_last_updated"] + self._MERGE_FILL_STRINGS + self._MERGE_FILL_NUMERICS
        merged = 0
        BATCH = 400
        for b in range(0, len(investors), BATCH):
            batch = [{k: inv.get(k) for k in keys if inv.get(k) not in (None, "")}
                     for inv in investors[b:b + BATCH]]
            query = f"""
                MERGE `{self.table_id}` T
                USING (
                    SELECT * FROM (SELECT {', '.join(sel)}
                                   FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j)
                    WHERE rn = 1
                ) S ON LOWER(T.name) = S.lname
                WHEN MATCHED THEN UPDATE SET {', '.join(sets)}"""
            try:
                job = self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("payload", "STRING", json.dumps(batch)),
                ]))
                job.result()
                merged += int(job.num_dml_affected_rows or 0)
            except Exception as e:
                logger.error(f"Bulk fill-merge failed for batch {b}-{b + len(batch)}: {e}")
        return merged

    def _merge_investor(self, inv: Dict) -> bool:
        """Fill-gaps merge of one investor's PitchBook fields into an existing row."""
        name = (inv.get("name") or "").strip()
        if not name:
            return False

        set_clauses = []
        params = [bigquery.ScalarQueryParameter("name", "STRING", name)]

        for col in self._MERGE_FILL_STRINGS:
            val = inv.get(col)
            if val:
                set_clauses.append(f"{col} = CASE WHEN (IFNULL({col}, '') = '') THEN @{col} ELSE {col} END")
                params.append(bigquery.ScalarQueryParameter(col, "STRING", str(val)))
        for col in self._MERGE_FILL_NUMERICS:
            val = inv.get(col)
            if val is not None:
                bq_type = "INT64" if col in self._MERGE_INT_NUMERICS else "FLOAT64"
                set_clauses.append(f"{col} = IFNULL({col}, @{col})")
                params.append(bigquery.ScalarQueryParameter(col, bq_type, val))

        # PitchBook identifiers/counters always refresh (authoritative)
        for col in ("pb_id", "pb_last_updated"):
            val = inv.get(col)
            if val:
                set_clauses.append(f"{col} = @{col}")
                params.append(bigquery.ScalarQueryParameter(col, "STRING", str(val)))

        if not set_clauses:
            return False
        set_clauses.append("updated_at = CURRENT_TIMESTAMP()")

        query = f"UPDATE `{self.table_id}` SET {', '.join(set_clauses)} WHERE LOWER(name) = LOWER(@name)"
        try:
            self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
            return True
        except Exception as e:
            logger.error(f"Merge failed for investor '{name}': {e}")
            return False

    def get_by_name(self, name: str) -> Optional[Dict]:
        if not self.client:
            return None
        try:
            rows = list(self.client.query(
                f"SELECT * FROM `{self.table_id}` WHERE LOWER(name) = LOWER(@name) LIMIT 1",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("name", "STRING", name)])).result())
            if not rows:
                return None
            d = dict(rows[0])
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            return d
        except Exception as e:
            logger.error(f"get_by_name failed for investor '{name}': {e}")
            return None

    def update_status(self, name: str, new_status: str, created_by: str = "Ishu Ratna",
                      reason: str = "", reason_detail: str = "") -> bool:
        """Move an investor to a stage. Stamps stage_entered_at (reset on every
        real move) and the first-entry column for the stage (once), records
        the park reason for Passed / Talk Later and clears it on unpark, and
        writes the move into the notes audit trail. ONE writer for status."""
        if not self.client or new_status not in INVESTOR_STAGES:
            return False
        cur = self.get_by_name(name) or {}
        old_status = cur.get("status") or "Unknown"
        sets = ["status = @status", "updated_at = CURRENT_TIMESTAMP()",
                "stage_entered_at = CASE WHEN IFNULL(status, '') != @status THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END"]
        stamp = INVESTOR_STAGE_STAMPS.get(new_status)
        if stamp:
            sets.append(f"{stamp} = IFNULL({stamp}, CURRENT_TIMESTAMP())")
        if new_status in INVESTOR_PARKED:
            sets += ["park_reason = @reason", "park_reason_detail = @detail"]
        else:
            sets += ["park_reason = NULL", "park_reason_detail = NULL"]
        query = f"UPDATE `{self.table_id}` SET {', '.join(sets)} WHERE LOWER(name) = LOWER(@name)"
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", new_status),
            bigquery.ScalarQueryParameter("reason", "STRING", reason or ""),
            bigquery.ScalarQueryParameter("detail", "STRING", reason_detail or ""),
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ])
        try:
            self.client.query(query, job_config=job_config).result()
            if old_status != new_status:
                why = f" ({reason}{': ' + reason_detail if reason_detail else ''})" if reason else ""
                self.add_note(name, f"Stage {old_status} -> {new_status}{why} [{created_by}]")
            return True
        except Exception as e:
            logger.error(f"Failed to update investor status: {e}")
            return False

    # Stages the bulk park must never touch. A gate verdict is not allowed to
    # undo work done or to re-park the parked (same rule as the company-side
    # stage guard, doctrine 3a).
    PARK_BULK_PROTECTED = ("Passed", "Talk Later", "Contacted", "Responded", "Meeting", "Committed")

    def park_bulk(self, items: list, reason: str, created_by: str, chunk: int = 500) -> int:
        """Park many investors as Passed in a handful of statements.

        THE BULK TWIN OF update_status, and it must stay in step with it: the
        same status write, the same stage_entered_at reset, the same park
        reason columns, and the same audit line appended to notes, computed in
        SQL from the row's CURRENT status (BigQuery evaluates the SET list
        against pre-update values, so `status` inside the CONCAT is the OLD
        stage). `tests_investor_gate.py` checks the two never drift.

        WHY IT EXISTS (11 Sep 2026): applying the gate to 1,292 investors
        through update_status meant three sequential BigQuery queries per row,
        roughly an hour of work, and Cloud Run cuts a request at ten minutes.
        Ishu watched curl die at 10:00 with a partial apply. This does the same
        work in three statements and returns in seconds.

        items: [(name, reason_detail), ...]. Returns rows actually updated.
        Rows at a protected stage are skipped inside the WHERE, so it is safe
        to re-run and safe to hand a list that includes already-parked rows.
        """
        if not self.client or not items:
            return 0
        total = 0
        for i in range(0, len(items), chunk):
            batch = items[i:i + chunk]
            names = [n for n, _ in batch]
            details = [(d or "")[:900] for _, d in batch]
            query = f"""UPDATE `{self.table_id}` t SET
                status = 'Passed',
                stage_entered_at = CURRENT_TIMESTAMP(),
                park_reason = @reason,
                park_reason_detail = @details[OFFSET(o)],
                notes = CONCAT(IFNULL(notes, ''),
                               '[', FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', CURRENT_TIMESTAMP()), '] ',
                               'Stage ', IFNULL(status, 'Unknown'), ' -> Passed (', @reason,
                               IF(@details[OFFSET(o)] != '', CONCAT(': ', @details[OFFSET(o)]), ''),
                               ') [', @by, ']\\n'),
                updated_at = CURRENT_TIMESTAMP()
            FROM UNNEST(@names) AS n WITH OFFSET AS o
            WHERE LOWER(t.name) = LOWER(n)
              AND IFNULL(t.status, '') NOT IN UNNEST(@protected)"""
            job_config = bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("names", "STRING", names),
                bigquery.ArrayQueryParameter("details", "STRING", details),
                bigquery.ArrayQueryParameter("protected", "STRING", list(self.PARK_BULK_PROTECTED)),
                bigquery.ScalarQueryParameter("reason", "STRING", reason or ""),
                bigquery.ScalarQueryParameter("by", "STRING", created_by or "system"),
            ])
            try:
                job = self.client.query(query, job_config=job_config)
                job.result()
                total += int(job.num_dml_affected_rows or 0)
            except Exception as e:
                logger.error(f"park_bulk failed on chunk {i // chunk + 1}: {e}")
                raise
        return total

    def pull_back_undelivered(self, name: str, reason: str, dead_address: str = "") -> bool:
        """Mirror of the company rule: a bounced LP email never reached anyone,
        so the investor returns to Researched (the pre-outreach stage), the
        send stamps are cleared so the Outreach button resets, and on a bounce
        the dead address moves to bounced_email so it is never re-suggested."""
        if not self.client:
            return False
        sets = ["status = 'Researched'", "stage_entered_at = CURRENT_TIMESTAMP()",
                "outreach_sent_at = NULL", "contacted_at = NULL", "updated_at = CURRENT_TIMESTAMP()"]
        if dead_address:
            sets += ["bounced_email = @dead", "contact_email = IF(LOWER(contact_email) = LOWER(@dead), NULL, contact_email)",
                     "outreach_draft_to = IF(LOWER(outreach_draft_to) = LOWER(@dead), NULL, outreach_draft_to)"]
        try:
            self.client.query(f"UPDATE `{self.table_id}` SET {', '.join(sets)} WHERE LOWER(name) = LOWER(@name)",
                              job_config=bigquery.QueryJobConfig(query_parameters=[
                                  bigquery.ScalarQueryParameter("dead", "STRING", dead_address or ""),
                                  bigquery.ScalarQueryParameter("name", "STRING", name)])).result()
            self.add_note(name, f"Stage Contacted -> Researched: email bounced ({reason})"
                                + (f", dead address {dead_address} kept aside" if dead_address else "") + " [delivery-check]")
            return True
        except Exception as e:
            logger.error(f"pull_back_undelivered failed for investor '{name}': {e}")
            return False

    # ── Priority (ai/lp_priority.lp_priority is the ONE definition) ────────────

    def write_priorities(self, rows: List[Dict]) -> int:
        """Recompute and store priority for the given investor rows (full rows,
        as returned by get_all/get_by_name). One MERGE per 400 rows."""
        if not self.client or not rows:
            return 0
        from ai.lp_priority import lp_priority, priority_json
        done = 0
        for start in range(0, len(rows), 400):
            chunk = rows[start:start + 400]
            structs, params = [], []
            for i, r in enumerate(chunk):
                res = lp_priority(r)
                structs.append(f"(@n{i}, @s{i}, @t{i}, @d{i})")
                params += [
                    bigquery.ScalarQueryParameter(f"n{i}", "STRING", r.get("name")),
                    bigquery.ScalarQueryParameter(f"s{i}", "FLOAT64", res["score"]),
                    bigquery.ScalarQueryParameter(f"t{i}", "STRING", res["tier"]),
                    bigquery.ScalarQueryParameter(f"d{i}", "STRING", priority_json(res)),
                ]
            query = f"""
                MERGE `{self.table_id}` T
                USING (SELECT * FROM UNNEST([STRUCT<name STRING, s FLOAT64, t STRING, d STRING>
                       {', '.join(structs)}])) S
                ON T.name = S.name
                WHEN MATCHED THEN UPDATE SET priority_score = S.s, priority_tier = S.t, priority_details = S.d"""
            self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
            done += len(chunk)
        return done

    def recompute_priority(self, name: Optional[str] = None) -> int:
        """One investor (after a write that changed an input) or the whole book."""
        if name:
            row = self.get_by_name(name)
            return self.write_priorities([row]) if row else 0
        return self.write_priorities(self.get_all())

    def set_tags(self, name: str, tags: List[str], created_by: str = "Ishu Ratna") -> bool:
        """Replace the network tags (warm paths) and recompute priority."""
        if not self.client:
            return False
        from ai.lp_priority import parse_tags
        clean = parse_tags(", ".join(tags))
        try:
            self.client.query(f"""UPDATE `{self.table_id}` SET network_tags = @t, updated_at = CURRENT_TIMESTAMP()
                                  WHERE LOWER(name) = LOWER(@n)""",
                              job_config=bigquery.QueryJobConfig(query_parameters=[
                                  bigquery.ScalarQueryParameter("t", "STRING", ", ".join(clean)),
                                  bigquery.ScalarQueryParameter("n", "STRING", name)])).result()
            self.add_note(name, f"Network tags set: {', '.join(clean) or '(none)'} [{created_by}]")
            self.recompute_priority(name)
            return True
        except Exception as e:
            logger.error(f"set_tags failed for investor '{name}': {e}")
            return False

    def add_tags_bulk(self, names: List[str], tags: List[str]) -> int:
        """Union the given tags onto many investors (uploads tag their rows)."""
        if not self.client or not names or not tags:
            return 0
        from ai.lp_priority import parse_tags
        clean = parse_tags(", ".join(tags))
        if not clean:
            return 0
        try:
            self.client.query(f"""UPDATE `{self.table_id}` SET
                    network_tags = ARRAY_TO_STRING(ARRAY(
                        SELECT DISTINCT x FROM UNNEST(ARRAY_CONCAT(
                            SPLIT(IFNULL(network_tags, ''), ','), @tags)) x WHERE TRIM(x) != ''), ', '),
                    updated_at = CURRENT_TIMESTAMP()
                WHERE name IN UNNEST(@names)""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ArrayQueryParameter("tags", "STRING", clean),
                    bigquery.ArrayQueryParameter("names", "STRING", names)])).result()
            rows = [r for r in self.get_all() if r.get("name") in set(names)]
            self.write_priorities(rows)
            return len(names)
        except Exception as e:
            logger.error(f"add_tags_bulk failed: {e}")
            return 0

    def stamp_reply(self, name: str, reply_at: str, classification: str) -> bool:
        """The sync's stamp: their last genuine message and its class."""
        if not self.client:
            return False
        try:
            self.client.query(f"""UPDATE `{self.table_id}`
                    SET last_reply_at = @ts, reply_classification = @cls, updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("ts", "TIMESTAMP", reply_at),
                    bigquery.ScalarQueryParameter("cls", "STRING", classification or ""),
                    bigquery.ScalarQueryParameter("name", "STRING", name),
                ])).result()
            return True
        except Exception as e:
            logger.error(f"stamp_reply failed for investor '{name}': {e}")
            return False

    def save_outreach_draft(self, name: str, to: str, subject: str, body: str) -> bool:
        if not self.client:
            return False
        try:
            self.client.query(f"""UPDATE `{self.table_id}`
                    SET outreach_draft_to = @to, outreach_draft_subject = @s, outreach_draft_body = @b,
                        outreach_drafted_at = CURRENT_TIMESTAMP(), updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("to", "STRING", to or ""),
                    bigquery.ScalarQueryParameter("s", "STRING", subject or ""),
                    bigquery.ScalarQueryParameter("b", "STRING", body or ""),
                    bigquery.ScalarQueryParameter("name", "STRING", name),
                ])).result()
            return True
        except Exception as e:
            logger.error(f"save_outreach_draft failed for investor '{name}': {e}")
            return False

    def record_send(self, name: str, to: str) -> bool:
        """After a successful send: outreach_sent_at refreshed, contacted_at
        stamped once, the address actually used kept, stage moved FORWARD
        only (Identified/Researched -> Contacted; anything later untouched)."""
        if not self.client:
            return False
        try:
            self.client.query(f"""UPDATE `{self.table_id}` SET
                    outreach_sent_at = CURRENT_TIMESTAMP(),
                    contacted_at = IFNULL(contacted_at, CURRENT_TIMESTAMP()),
                    outreach_draft_to = @to,
                    stage_entered_at = CASE WHEN IFNULL(status, '') IN ('Identified', 'Researched', '')
                                            THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                    status = CASE WHEN IFNULL(status, '') IN ('Identified', 'Researched', '')
                                  THEN 'Contacted' ELSE status END,
                    updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("to", "STRING", to or ""),
                    bigquery.ScalarQueryParameter("name", "STRING", name),
                ])).result()
            return True
        except Exception as e:
            logger.error(f"record_send failed for investor '{name}': {e}")
            return False

    def update_enrichment(self, name: str, fields: Dict) -> bool:
        """Write InvestorFill results back to the row."""
        if not self.client:
            return False
        query = f"""UPDATE `{self.table_id}` SET
            investor_type = @investor_type,
            aum_m = @aum_m,
            ticket_min_m = @ticket_min_m,
            ticket_max_m = @ticket_max_m,
            region = @region,
            hq_city = @hq_city,
            hq_country = @hq_country,
            website = @website,
            description = CASE WHEN (@description != '' AND LENGTH(@description) > LENGTH(IFNULL(description, ''))) THEN @description ELSE description END,
            contact_name = @contact_name,
            contact_title = CASE WHEN @contact_title != '' THEN @contact_title ELSE contact_title END,
            contact_email = @contact_email,
            contact_confidence = CASE WHEN @contact_email != '' THEN @contact_confidence ELSE contact_confidence END,
            linkedin_url = @linkedin_url,
            gate_region = CASE WHEN @gate_region != '' THEN @gate_region ELSE gate_region END,
            gate_unfit_reason = @gate_unfit_reason,
            identity_status = CASE WHEN @identity_status != '' THEN @identity_status ELSE identity_status END,
            identity_note = CASE WHEN @identity_note != '' THEN @identity_note ELSE identity_note END,
            -- lp_priority puts its HEAVIEST weight (0.30, co-invest appetite) on
            -- these three, and the GATE's mandate route reads geo_preferences.
            -- FILL-ONLY: PitchBook's own wording is authoritative where it
            -- exists, and an AI paraphrase must never replace it.
            strategy_preferences = IFNULL(NULLIF(strategy_preferences, ''), @strategy_preferences),
            other_preferences = IFNULL(NULLIF(other_preferences, ''), @other_preferences),
            policy_description = IFNULL(NULLIF(policy_description, ''), @policy_description),
            geo_preferences = IFNULL(NULLIF(geo_preferences, ''), @geo_preferences),
            num_pe_commitments = IFNULL(num_pe_commitments, @num_pe_commitments),
            num_vc_commitments = IFNULL(num_vc_commitments, @num_vc_commitments),
            last_commitment_date = IFNULL(NULLIF(last_commitment_date, ''), @last_commitment_date),
            lp_fit_score = @lp_fit_score,
            score_geography = @score_geography,
            score_pe_appetite = @score_pe_appetite,
            score_ticket_fit = @score_ticket_fit,
            score_tech_affinity = @score_tech_affinity,
            fit_details = @fit_details,
            psc_summary = CASE WHEN @psc_summary != '' THEN @psc_summary ELSE psc_summary END,
            officers_summary = CASE WHEN @officers_summary != '' THEN @officers_summary ELSE officers_summary END,
            net_assets_m = IFNULL(@net_assets_m, net_assets_m),
            status = CASE WHEN status = 'Identified' THEN 'Researched' ELSE status END,
            updated_at = CURRENT_TIMESTAMP()
            WHERE LOWER(name) = LOWER(@name)"""
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("investor_type", "STRING", fields.get("investor_type") or "Unknown"),
            bigquery.ScalarQueryParameter("contact_title", "STRING", fields.get("contact_title") or ""),
            bigquery.ScalarQueryParameter("contact_confidence", "STRING", fields.get("contact_confidence") or ""),
            bigquery.ScalarQueryParameter("gate_region", "STRING", fields.get("gate_region") or ""),
            bigquery.ScalarQueryParameter("gate_unfit_reason", "STRING", fields.get("gate_unfit_reason") or ""),
            bigquery.ScalarQueryParameter("identity_status", "STRING", fields.get("identity_status") or ""),
            bigquery.ScalarQueryParameter("identity_note", "STRING", fields.get("identity_note") or ""),
            bigquery.ScalarQueryParameter("strategy_preferences", "STRING", fields.get("strategy_preferences") or ""),
            bigquery.ScalarQueryParameter("other_preferences", "STRING", fields.get("other_preferences") or ""),
            bigquery.ScalarQueryParameter("policy_description", "STRING", fields.get("policy_description") or ""),
            bigquery.ScalarQueryParameter("geo_preferences", "STRING", fields.get("geo_preferences") or ""),
            bigquery.ScalarQueryParameter("num_pe_commitments", "INT64", fields.get("num_pe_commitments")),
            bigquery.ScalarQueryParameter("num_vc_commitments", "INT64", fields.get("num_vc_commitments")),
            bigquery.ScalarQueryParameter("last_commitment_date", "STRING", fields.get("last_commitment_date") or ""),
            bigquery.ScalarQueryParameter("aum_m", "FLOAT64", fields.get("aum_m")),
            bigquery.ScalarQueryParameter("ticket_min_m", "FLOAT64", fields.get("ticket_min_m")),
            bigquery.ScalarQueryParameter("ticket_max_m", "FLOAT64", fields.get("ticket_max_m")),
            bigquery.ScalarQueryParameter("region", "STRING", fields.get("region") or ""),
            bigquery.ScalarQueryParameter("hq_city", "STRING", fields.get("hq_city") or ""),
            bigquery.ScalarQueryParameter("hq_country", "STRING", fields.get("hq_country") or ""),
            bigquery.ScalarQueryParameter("website", "STRING", fields.get("website") or ""),
            bigquery.ScalarQueryParameter("description", "STRING", fields.get("description") or ""),
            bigquery.ScalarQueryParameter("contact_name", "STRING", fields.get("contact_name") or ""),
            bigquery.ScalarQueryParameter("contact_email", "STRING", fields.get("contact_email") or ""),
            bigquery.ScalarQueryParameter("linkedin_url", "STRING", fields.get("linkedin_url") or ""),
            bigquery.ScalarQueryParameter("lp_fit_score", "FLOAT64", fields.get("lp_fit_score")),
            bigquery.ScalarQueryParameter("score_geography", "FLOAT64", fields.get("score_geography")),
            bigquery.ScalarQueryParameter("score_pe_appetite", "FLOAT64", fields.get("score_pe_appetite")),
            bigquery.ScalarQueryParameter("score_ticket_fit", "FLOAT64", fields.get("score_ticket_fit")),
            bigquery.ScalarQueryParameter("score_tech_affinity", "FLOAT64", fields.get("score_tech_affinity")),
            bigquery.ScalarQueryParameter("fit_details", "STRING", fields.get("fit_details") or ""),
            bigquery.ScalarQueryParameter("psc_summary", "STRING", fields.get("psc_summary") or ""),
            bigquery.ScalarQueryParameter("officers_summary", "STRING", fields.get("officers_summary") or ""),
            bigquery.ScalarQueryParameter("net_assets_m", "FLOAT64", fields.get("net_assets_m")),
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ])
        try:
            self.client.query(query, job_config=job_config).result()
            return True
        except Exception as e:
            logger.error(f"Failed to update investor enrichment: {e}")
            return False

    def add_note(self, name: str, note: str) -> bool:
        if not self.client:
            return False
        query = f"""UPDATE `{self.table_id}`
                    SET notes = CONCAT(IFNULL(notes, ''), @note),
                        updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)"""
        stamped = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}] {note}\n"
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("note", "STRING", stamped),
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ])
        try:
            self.client.query(query, job_config=job_config).result()
            return True
        except Exception as e:
            logger.error(f"Failed to add investor note: {e}")
            return False

    # ── Investor ↔ Company connection layer ─────────────────────────────────
    # Edge table: one row per (investor, company, link_type) with evidence.
    # This is what makes interconnection queries possible: co-investors,
    # sibling portfolio companies, shared backers across the universe.

    @property
    def links_table_id(self) -> str:
        return self.table_id.rsplit(".", 1)[0] + ".investor_links"

    def _ensure_links_table(self):
        try:
            self.client.get_table(self.links_table_id)
        except Exception:
            schema = [bigquery.SchemaField(n, t) for n, t in [
                ("investor_key", "STRING"),   # canonical lowercase key
                ("investor_name", "STRING"),
                ("investor_type", "STRING"),
                ("company_name", "STRING"),
                ("link_type", "STRING"),      # equity_holder / pitchbook_active / pitchbook_former / inven_investor / inven_owner
                ("pct", "FLOAT64"),           # stake, when known (cap table)
                ("detail", "STRING"),
                ("source", "STRING"),
                ("updated_at", "TIMESTAMP"),
            ]]
            self.client.create_table(bigquery.Table(self.links_table_id, schema=schema))
            logger.info("Created investor_links table in BigQuery")

    def save_links_bulk(self, per_company: Dict[str, List[Dict]]) -> int:
        """All companies in ONE DELETE + ONE INSERT (per-company DML made the
        full sweep take minutes and killed the request on hostile networks)."""
        if not self.client or not per_company:
            return 0
        import json as _json
        self._ensure_links_table()
        names = list(per_company.keys())
        self.client.query(
            f"DELETE FROM `{self.links_table_id}` WHERE source = 'mining' AND company_name IN UNNEST(@names)",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("names", "STRING", names)])).result()
        rows = []
        for cname, links in per_company.items():
            for l in links:
                rows.append({"c": cname, "k": l.get("investor_key") or "",
                             "n": l.get("investor_name") or "", "t": l.get("investor_type") or "Unknown",
                             "lt": l.get("link_type") or "", "p": l.get("pct"),
                             "d": (l.get("detail") or "")[:400]})
        if not rows:
            return 0
        self.client.query(
            f"""INSERT INTO `{self.links_table_id}`
                (investor_key, investor_name, investor_type, company_name, link_type, pct, detail, source, updated_at)
                SELECT JSON_EXTRACT_SCALAR(j, '$.k'), JSON_EXTRACT_SCALAR(j, '$.n'),
                       JSON_EXTRACT_SCALAR(j, '$.t'), JSON_EXTRACT_SCALAR(j, '$.c'),
                       JSON_EXTRACT_SCALAR(j, '$.lt'),
                       SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.p') AS FLOAT64),
                       JSON_EXTRACT_SCALAR(j, '$.d'), 'mining', CURRENT_TIMESTAMP()
                FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("payload", "STRING", _json.dumps(rows))])).result()
        return len(rows)

    def save_links(self, company_name: str, links: List[Dict]) -> int:
        """Snapshot semantics per company: mining is authoritative for the
        companies it just processed — replace their mining edges wholesale."""
        if not self.client:
            return 0
        import json as _json
        self._ensure_links_table()
        self.client.query(
            f"DELETE FROM `{self.links_table_id}` WHERE company_name = @c AND source = 'mining'",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("c", "STRING", company_name)])).result()
        if not links:
            return 0
        payload = _json.dumps([{
            "k": l.get("investor_key") or "", "n": l.get("investor_name") or "",
            "t": l.get("investor_type") or "Unknown", "lt": l.get("link_type") or "",
            "p": l.get("pct"), "d": (l.get("detail") or "")[:400],
        } for l in links])
        self.client.query(
            f"""INSERT INTO `{self.links_table_id}`
                (investor_key, investor_name, investor_type, company_name, link_type, pct, detail, source, updated_at)
                SELECT JSON_EXTRACT_SCALAR(j, '$.k'), JSON_EXTRACT_SCALAR(j, '$.n'),
                       JSON_EXTRACT_SCALAR(j, '$.t'), @c, JSON_EXTRACT_SCALAR(j, '$.lt'),
                       SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.p') AS FLOAT64),
                       JSON_EXTRACT_SCALAR(j, '$.d'), 'mining', CURRENT_TIMESTAMP()
                FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("c", "STRING", company_name),
                bigquery.ScalarQueryParameter("payload", "STRING", payload)])).result()
        return len(links)

    def merge_source_companies(self, pairs: List[Dict]) -> int:
        """Batch-append portfolio overlaps to existing investors in ONE DML.
        pairs: [{"key": lower_name, "source_companies": merged_string}]"""
        if not self.client or not pairs:
            return 0
        import json as _json
        payload = _json.dumps([{"k": p["key"], "sc": p["source_companies"][:2000]} for p in pairs])
        q = f"""MERGE `{self.table_id}` T
                USING (SELECT JSON_EXTRACT_SCALAR(j, '$.k') AS k, JSON_EXTRACT_SCALAR(j, '$.sc') AS sc
                       FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j) S
                ON LOWER(T.name) = S.k
                WHEN MATCHED THEN UPDATE SET source_companies = S.sc, updated_at = CURRENT_TIMESTAMP()"""
        self.client.query(q, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("payload", "STRING", payload)])).result()
        return len(pairs)

    def get_company_connections(self, company_name: str) -> Dict:
        """Investors of a company + sibling companies that share any of them."""
        if not self.client:
            return {"investors": [], "siblings": []}
        self._ensure_links_table()
        inv = [dict(r) for r in self.client.query(
            f"""SELECT investor_key, investor_name, investor_type, link_type, pct, detail
                FROM `{self.links_table_id}` WHERE company_name = @c
                ORDER BY pct IS NULL, pct DESC""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("c", "STRING", company_name)])).result()]
        sib = [dict(r) for r in self.client.query(
            f"""SELECT l2.company_name, l2.investor_name AS via, l2.investor_type
                FROM `{self.links_table_id}` l1
                JOIN `{self.links_table_id}` l2 ON l1.investor_key = l2.investor_key
                WHERE l1.company_name = @c AND l2.company_name != @c
                ORDER BY l2.company_name""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("c", "STRING", company_name)])).result()]
        return {"investors": inv, "siblings": sib}

    def get_investor_connections(self, investor_name: str) -> Dict:
        """Portfolio companies of an investor + co-investors sharing them."""
        if not self.client:
            return {"companies": [], "co_investors": []}
        self._ensure_links_table()
        key = (investor_name or "").strip().lower()
        comp = [dict(r) for r in self.client.query(
            f"""SELECT company_name, link_type, pct, detail FROM `{self.links_table_id}`
                WHERE investor_key = @k OR LOWER(investor_name) = @k ORDER BY company_name""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("k", "STRING", key)])).result()]
        co = [dict(r) for r in self.client.query(
            f"""SELECT DISTINCT l2.investor_name, l2.investor_type, l2.company_name AS shared_company
                FROM `{self.links_table_id}` l1
                JOIN `{self.links_table_id}` l2 ON l1.company_name = l2.company_name
                WHERE (l1.investor_key = @k OR LOWER(l1.investor_name) = @k)
                  AND l2.investor_key != l1.investor_key
                ORDER BY l2.investor_name""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("k", "STRING", key)])).result()]
        return {"companies": comp, "co_investors": co}

    def get_all_links(self, limit: int = 4000) -> List[Dict]:
        """Compact edge list for chat context and graph views."""
        if not self.client:
            return []
        self._ensure_links_table()
        rows = self.client.query(
            f"""SELECT investor_name, investor_type, company_name, link_type, pct
                FROM `{self.links_table_id}` ORDER BY investor_name LIMIT {int(limit)}""").result()
        return [dict(r) for r in rows]
