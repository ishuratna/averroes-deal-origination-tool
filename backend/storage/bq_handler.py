import os
import uuid
import logging
from typing import List, Dict
from google.cloud import bigquery
from datetime import datetime

logger = logging.getLogger(__name__)

class BigQueryHandler:
    """
    Handles read/write operations to Google BigQuery for the targets database.
    """
    def __init__(self, project_id: str, dataset_id: str = "averroes_deal_flow"):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.table_id = f"{self.project_id}.{self.dataset_id}.targets"
        self.activity_table_id = f"{self.project_id}.{self.dataset_id}.activity_log"
        self.config_table_id = f"{self.project_id}.{self.dataset_id}.qualification_config"

        self.init_error = None
        try:
            self.client = bigquery.Client(project=project_id)
            self._ensure_expanded_schema()
            self._ensure_activity_table()
            self._ensure_config_table()
        except Exception as e:
            logger.warning(f"BigQuery Client could not be initialized: {e}")
            self.init_error = str(e)
            self.client = None

    # New columns to add to the targets table (PitchBook expanded schema)
    EXPANDED_COLUMNS = [
        ("contact_title", "STRING"), ("contact_phone", "STRING"),
        ("hq_email", "STRING"), ("hq_phone", "STRING"),
        ("hq_location", "STRING"), ("hq_city", "STRING"), ("hq_country", "STRING"),
        ("employees", "INT64"), ("year_founded", "INT64"),
        ("keywords", "STRING"), ("verticals", "STRING"),
        ("industry_group", "STRING"), ("industry_code", "STRING"),
        ("emerging_spaces", "STRING"), ("business_status", "STRING"),
        ("financing_status", "STRING"), ("total_raised_m", "FLOAT64"),
        ("revenue_m", "FLOAT64"), ("net_income_m", "FLOAT64"),
        ("enterprise_value_m", "FLOAT64"), ("revenue_growth_pct", "FLOAT64"),
        ("valuation_estimate_m", "FLOAT64"), ("last_valuation_m", "FLOAT64"),
        ("last_valuation_date", "STRING"), ("active_investors", "STRING"),
        ("num_active_investors", "INT64"), ("former_investors", "STRING"),
        ("last_financing_date", "STRING"), ("last_financing_size_m", "FLOAT64"),
        ("last_financing_valuation_m", "FLOAT64"), ("last_financing_type", "STRING"),
        ("first_financing_date", "STRING"), ("first_financing_size_m", "FLOAT64"),
        ("pitchbook_growth_rate", "FLOAT64"), ("growth_rate_percentile", "INT64"),
        ("web_visitors", "INT64"), ("opportunity_score", "INT64"),
        ("success_probability", "INT64"), ("ma_probability", "INT64"),
        ("predicted_exit_type", "STRING"), ("total_patents", "INT64"),
        ("competitors", "STRING"), ("also_known_as", "STRING"),
        ("legal_name", "STRING"), ("registration_number", "STRING"),
        ("financing_note", "STRING"),
        ("size_bucket", "STRING"),
        # Companies House financial data
        ("ch_company_number", "STRING"),
        ("ch_official_name", "STRING"),
        ("ch_status", "STRING"),
        ("ch_incorporated_date", "STRING"),
        ("ch_sic_codes", "STRING"),
        ("revenue_y1", "FLOAT64"), ("revenue_y1_date", "STRING"),
        ("revenue_y2", "FLOAT64"), ("revenue_y2_date", "STRING"),
        ("revenue_y3", "FLOAT64"), ("revenue_y3_date", "STRING"),
        ("profit_y1", "FLOAT64"), ("profit_y1_date", "STRING"),
        ("profit_y2", "FLOAT64"), ("profit_y3", "FLOAT64"),
        ("total_assets_y1", "FLOAT64"),
        ("net_assets_y1", "FLOAT64"),
        ("cash_y1", "FLOAT64"),
        ("employees_ch", "INT64"),
        ("filing_type", "STRING"),
        ("ch_match_confidence", "STRING"),
        ("ch_notes", "STRING"),
        ("ch_pdf_path", "STRING"),
        # Averroes fit scoring
        ("averroes_fit_score", "FLOAT64"),
        ("score_employee_growth", "FLOAT64"),
        ("score_revenue_growth", "FLOAT64"),
        ("score_revenue_size", "FLOAT64"),
        ("score_business_fit", "FLOAT64"),
        ("score_market_sentiment", "FLOAT64"),
        ("score_details", "STRING"),
        ("revenue_band", "STRING"),
        ("revenue_estimate_m", "FLOAT64"),
        ("revenue_source", "STRING"),
        ("revenue_confidence", "STRING"),
        ("gross_profit_y1", "FLOAT64"),
        ("gross_profit_y2", "FLOAT64"),
        ("last_smartfill_at", "TIMESTAMP"),
        # Why the company failed the hard filters (shown on hover in the UI)
        ("unfit_reason", "STRING"),
        # Persisted outreach draft (review-and-send flow)
        ("outreach_draft_subject", "STRING"),
        ("outreach_draft_body", "STRING"),
        ("outreach_draft_to", "STRING"),
        ("outreach_drafted_at", "TIMESTAMP"),
        ("outreach_sent_at", "TIMESTAMP"),
        # Email reply intelligence (from the Gmail sync)
        ("last_reply_at", "TIMESTAMP"),
        ("reply_classification", "STRING"),
        # Responded-stage action buckets: what the reply means for us, combining
        # our fit data with what the founder actually said. Set on sync.
        ("action_bucket", "STRING"),
        ("action_rationale", "STRING"),
        ("action_follow_up_date", "STRING"),
        ("action_set_at", "TIMESTAMP"),
        ("action_reply_subject", "STRING"),
        ("action_reply_body", "STRING"),
        # IC memo one-pager (JSON) for Engaged-or-later companies
        ("ic_memo", "STRING"),
        ("ic_memo_at", "TIMESTAMP"),
        # Companies House registry intelligence
        ("ch_psc_summary", "STRING"),
        ("ch_ownership_verified", "STRING"),
        ("ch_charges_count", "INT64"),
        ("ch_charges_summary", "STRING"),
        ("ch_last_share_allotment", "STRING"),
        ("ch_accounts_next_due", "STRING"),
        # Inven export fields (local growth metrics feed the fit score)
        ("revenue_cagr_3yr_pct", "FLOAT64"),
        ("employee_growth_1yr_pct", "FLOAT64"),
        ("employee_growth_3yr_pct", "FLOAT64"),
        ("ebitda_margin_pct", "FLOAT64"),
        ("directors", "STRING"),
        ("company_linkedin", "STRING"),
        # CH v4: distress flags, filing intelligence, cap table, watch job
        ("ch_accounts_overdue", "BOOL"),
        ("ch_insolvency_summary", "STRING"),
        ("ch_last_resolution", "STRING"),
        ("ch_accounts_regime", "STRING"),
        ("ch_cap_table", "STRING"),
        ("ch_cap_table_date", "STRING"),
        ("ch_founder_pct", "FLOAT64"),
        ("ch_watched_at", "STRING"),
        # Full multi-year financial history from CH filings (JSON) — feeds the
        # profile's revenue chart, employee chart and multi-year P&L table
        ("ch_history", "STRING"),
        # Stage timeline: when the company entered its CURRENT stage (drives
        # kanban sorting + stale flag), plus permanent first-entry timestamps
        # per stage (never overwritten — the Contacted date survives later moves)
        ("stage_entered_at", "TIMESTAMP"),
        ("qualified_at", "TIMESTAMP"),
        ("contacted_at", "TIMESTAMP"),
        ("meeting_at", "TIMESTAMP"),
        ("dd_at", "TIMESTAMP"),
        ("offer_at", "TIMESTAMP"),
        ("won_at", "TIMESTAMP"),
        ("lost_at", "TIMESTAMP"),
    ]

    # Map stage name → its permanent first-entry timestamp column
    STAGE_TIMESTAMP_COLS = {
        "Qualified": "qualified_at",
        "Contacted": "contacted_at",
        "Meeting": "meeting_at",
        "DD": "dd_at",
        "Offer": "offer_at",
        "Won": "won_at",
        "Lost": "lost_at",
    }

    def _ensure_expanded_schema(self):
        """Add new columns to the BQ targets table if they don't exist yet. Idempotent."""
        if not self.client:
            return
        try:
            table = self.client.get_table(self.table_id)
            existing_cols = {f.name for f in table.schema}
            new_fields = []
            for col_name, col_type in self.EXPANDED_COLUMNS:
                if col_name not in existing_cols:
                    new_fields.append(bigquery.SchemaField(col_name, col_type, mode="NULLABLE"))
            if new_fields:
                table.schema = list(table.schema) + new_fields
                self.client.update_table(table, ["schema"])
                logger.info(f"BQ schema expanded: added {len(new_fields)} new columns")
            else:
                logger.info("BQ schema already up to date")
        except Exception as e:
            logger.warning(f"Schema expansion check failed (non-fatal): {e}")

    # ── Activity log table ─────────────────────────────────────────────────────
    def _ensure_activity_table(self):
        """Create the activity_log table if it doesn't exist. Idempotent."""
        if not self.client:
            return
        try:
            self.client.get_table(self.activity_table_id)
            logger.info("Activity log table already exists")
        except Exception:
            schema = [
                bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("company_name", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("action_type", "STRING"),  # status_change, note, outreach_sent
                bigquery.SchemaField("old_status", "STRING"),
                bigquery.SchemaField("new_status", "STRING"),
                bigquery.SchemaField("note_text", "STRING"),
                bigquery.SchemaField("created_by", "STRING"),
                bigquery.SchemaField("created_at", "TIMESTAMP"),
            ]
            table = bigquery.Table(self.activity_table_id, schema=schema)
            self.client.create_table(table)
            logger.info("Created activity_log table in BigQuery")

    # ── Qualification config table ────────────────────────────────────────────────

    DEFAULT_CRITERIA = {
        "geography": {
            "label": "Geography",
            "description": "Company must be headquartered in one of these regions",
            "regions": ["UK", "Ireland", "United Kingdom", "Great Britain",
                        "London", "Dublin", "Edinburgh", "Manchester",
                        "Birmingham", "Belfast", "Glasgow", "Bristol",
                        "Leeds", "Cardiff", "Cork", "Galway", "Limerick"],
            "country_codes": ["uk", "gb", "ie", "england", "scotland", "wales", "northern ireland"],
        },
        "industry": {
            "label": "Industry / Sector",
            "description": "Company must be technology or tech-related",
            "keywords": [
                "software", "saas", "platform", "cloud", "paas", "iaas",
                "tech", "technology", "digital", "ai", "artificial intelligence",
                "machine learning", "data", "analytics", "automation",
                "cyber", "fintech", "healthtech", "edtech", "insurtech",
                "proptech", "regtech", "legaltech", "martech", "adtech",
                "devops", "api", "iot", "blockchain", "robotics",
                "it services", "managed services", "hosting",
                "e-commerce platform", "marketplace platform",
            ],
        },
        "focus": "B2B SaaS / Software / High-Margin Tech-Enabled Services / Industrial Tech",
        "target_ebitda": "£15-40M equity cheques, majority or significant minority. Revenue £5-40M (core £8-20M), EBITDA ~£3-15M.",
    }

    def _ensure_config_table(self):
        """Create the qualification_config table if it doesn't exist. Seed with defaults."""
        if not self.client:
            return
        try:
            self.client.get_table(self.config_table_id)
            logger.info("Qualification config table already exists")
        except Exception:
            schema = [
                bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("criteria_json", "STRING"),
                bigquery.SchemaField("updated_by", "STRING"),
                bigquery.SchemaField("updated_at", "TIMESTAMP"),
                bigquery.SchemaField("version", "INT64"),
            ]
            table = bigquery.Table(self.config_table_id, schema=schema)
            self.client.create_table(table)
            logger.info("Created qualification_config table in BigQuery")
            # Seed with default criteria
            self._save_criteria(self.DEFAULT_CRITERIA, "System (initial seed)", version=1)

    def get_criteria(self) -> dict:
        """Get the latest qualification criteria from BQ. Falls back to defaults."""
        if not self.client:
            return self.DEFAULT_CRITERIA
        try:
            query = f"SELECT criteria_json, updated_by, updated_at, version FROM `{self.config_table_id}` ORDER BY version DESC LIMIT 1"
            rows = list(self.client.query(query).result())
            if rows:
                import json
                return json.loads(rows[0].criteria_json)
            return self.DEFAULT_CRITERIA
        except Exception as e:
            logger.warning(f"Failed to read criteria from BQ: {e}")
            return self.DEFAULT_CRITERIA

    def get_criteria_meta(self) -> dict:
        """Get criteria + metadata (who updated, when, version)."""
        if not self.client:
            return {"criteria": self.DEFAULT_CRITERIA, "updated_by": "System", "updated_at": None, "version": 0}
        try:
            query = f"SELECT criteria_json, updated_by, updated_at, version FROM `{self.config_table_id}` ORDER BY version DESC LIMIT 1"
            rows = list(self.client.query(query).result())
            if rows:
                import json
                row = rows[0]
                return {
                    "criteria": json.loads(row.criteria_json),
                    "updated_by": row.updated_by,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "version": row.version,
                }
            return {"criteria": self.DEFAULT_CRITERIA, "updated_by": "System", "updated_at": None, "version": 0}
        except Exception as e:
            logger.warning(f"Failed to read criteria meta: {e}")
            return {"criteria": self.DEFAULT_CRITERIA, "updated_by": "System", "updated_at": None, "version": 0}

    def save_criteria(self, criteria: dict, updated_by: str = "Ishu Ratna") -> bool:
        """Save new criteria to BQ as a new version."""
        meta = self.get_criteria_meta()
        new_version = meta.get("version", 0) + 1
        return self._save_criteria(criteria, updated_by, new_version)

    def _save_criteria(self, criteria: dict, updated_by: str, version: int) -> bool:
        if not self.client:
            return False
        try:
            import json
            query = f"""
                INSERT INTO `{self.config_table_id}` (id, criteria_json, updated_by, updated_at, version)
                VALUES (@id, @criteria_json, @updated_by, CURRENT_TIMESTAMP(), @version)
            """
            self.client.query(query, job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("id", "STRING", str(uuid.uuid4())),
                    bigquery.ScalarQueryParameter("criteria_json", "STRING", json.dumps(criteria)),
                    bigquery.ScalarQueryParameter("updated_by", "STRING", updated_by),
                    bigquery.ScalarQueryParameter("version", "INT64", version),
                ]
            )).result()
            return True
        except Exception as e:
            logger.error(f"Failed to save criteria: {e}")
            return False

    # ── Deal lifecycle: status updates ──────────────────────────────────────────

    # Valid deal stages in pipeline order
    DEAL_STAGES = ["Scraped", "Uploaded", "Not a Fit", "Qualified", "Contacted", "Meeting", "DD", "Offer", "Won", "Lost"]
    ACTIVE_PIPELINE_STAGES = ("Qualified", "Contacted", "Meeting", "DD", "Offer")

    def update_company_status(self, company_name: str, new_status: str, created_by: str = "Ishu Ratna") -> bool:
        """Update a company's status and log the change in activity_log."""
        if not self.client:
            return False
        if new_status not in self.DEAL_STAGES:
            logger.error(f"Invalid status: {new_status}")
            return False

        try:
            # Get current status
            q = f"SELECT status FROM `{self.table_id}` WHERE name = @name LIMIT 1"
            job = self.client.query(q, job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", company_name)]
            ))
            rows = list(job.result())
            old_status = rows[0].status if rows else "Unknown"

            # Update status + stage timeline in targets table:
            #  - stage_entered_at: reset whenever the stage actually changes
            #  - per-stage column: stamped on FIRST entry only, preserved forever
            set_clauses = [
                "status = @new_status",
                "stage_entered_at = CASE WHEN IFNULL(status, '') != @new_status THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END",
            ]
            stage_col = self.STAGE_TIMESTAMP_COLS.get(new_status)
            if stage_col:
                set_clauses.append(f"{stage_col} = IFNULL({stage_col}, CURRENT_TIMESTAMP())")
            update_q = f"UPDATE `{self.table_id}` SET {', '.join(set_clauses)} WHERE name = @name"
            self.client.query(update_q, job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
                    bigquery.ScalarQueryParameter("name", "STRING", company_name),
                ]
            )).result()

            # Log the status change
            self._log_activity(company_name, "status_change", created_by,
                               old_status=old_status, new_status=new_status,
                               note_text=f"Status changed from {old_status} to {new_status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update status for {company_name}: {e}")
            return False

    # Worst-case grounded (Google Search) Gemini calls per operation type.
    # The shared daily budget guarantees we NEVER cross the free 1,500/day tier.
    # smartfill/smartenrich +1 (Jul 2026): the contact-finder retry ladder can
    # fire one extra grounded search when the first pass finds no email.
    GROUNDING_WEIGHTS = {"smartfill": 4, "smartenrich": 3, "investorfill": 1, "newslookup": 1, "icmemo": 1}

    def count_smartfills_today(self) -> int:
        """How many SmartFill/SmartEnrich runs have been logged today (UTC)."""
        if not self.client:
            return 0
        query = f"""SELECT COUNT(*) AS n FROM `{self.activity_table_id}`
                    WHERE action_type IN ('smartfill', 'smartenrich', 'smartfill_gated') AND DATE(created_at) = CURRENT_DATE()"""
        try:
            rows = list(self.client.query(query).result())
            return int(rows[0].n) if rows else 0
        except Exception as e:
            logger.error(f"Failed to count today's smartfills: {e}")
            return 0

    def grounded_calls_used_today(self) -> int:
        """
        Worst-case grounded search calls consumed today across ALL AI operations
        (SmartFill ×3, SmartEnrich ×2, InvestorFill ×1). Conservative by design:
        assumes every run used its maximum, so the free tier can never be crossed.
        """
        if not self.client:
            return 0
        query = f"""SELECT action_type, COUNT(*) AS n FROM `{self.activity_table_id}`
                    WHERE action_type IN ('smartfill', 'smartenrich', 'investorfill', 'newslookup')
                      AND DATE(created_at) = CURRENT_DATE()
                    GROUP BY action_type"""
        try:
            total = 0
            for row in self.client.query(query).result():
                total += self.GROUNDING_WEIGHTS.get(row.action_type, 3) * int(row.n)
            return total
        except Exception as e:
            logger.error(f"Failed to count grounded calls: {e}")
            return 0

    def log_smartfill(self, company_name: str, kind: str = "smartfill") -> bool:
        """Record a SmartFill/SmartEnrich run in the activity log (feeds the daily cap counter)."""
        return self._log_activity(company_name, kind, "system", note_text=f"{kind} run")

    def _ensure_email_log_table(self):
        """Create the email_log table if missing. Called lazily on first sync."""
        table_id = f"{self.project_id}.{self.dataset_id}.email_log"
        try:
            self.client.get_table(table_id)
        except Exception:
            schema = [bigquery.SchemaField(n, t) for n, t in [
                ("message_id", "STRING"), ("thread_id", "STRING"), ("direction", "STRING"),
                ("counterparty_email", "STRING"), ("counterparty_name", "STRING"),
                ("entity_type", "STRING"), ("entity_name", "STRING"),
                ("subject", "STRING"), ("snippet", "STRING"),
                ("classification", "STRING"), ("summary", "STRING"),
                ("sent_at", "TIMESTAMP"), ("synced_at", "TIMESTAMP"),
            ]]
            self.client.create_table(bigquery.Table(table_id, schema=schema))
            logger.info("Created email_log table")
        return table_id

    def get_logged_message_ids(self) -> set:
        """Message-IDs already in the log (dedup for re-syncs)."""
        if not self.client:
            return set()
        table_id = self._ensure_email_log_table()
        try:
            rows = self.client.query(f"SELECT message_id FROM `{table_id}`").result()
            return {r.message_id for r in rows}
        except Exception as e:
            logger.error(f"Failed to load logged message ids: {e}")
            return set()

    def save_email_log(self, entries: List[Dict]) -> int:
        """DML-insert new email log entries. Returns inserted count."""
        if not self.client or not entries:
            return 0
        table_id = self._ensure_email_log_table()
        inserted = 0
        for e in entries:
            q = f"""INSERT INTO `{table_id}`
                (message_id, thread_id, direction, counterparty_email, counterparty_name,
                 entity_type, entity_name, subject, snippet, classification, summary, sent_at, synced_at)
                VALUES (@mid, @tid, @dir, @cemail, @cname, @etype, @ename, @subj, @snip, @cls, @summ, @sent, CURRENT_TIMESTAMP())"""
            params = [
                bigquery.ScalarQueryParameter("mid", "STRING", e.get("message_id") or ""),
                bigquery.ScalarQueryParameter("tid", "STRING", e.get("thread_id") or ""),
                bigquery.ScalarQueryParameter("dir", "STRING", e.get("direction") or ""),
                bigquery.ScalarQueryParameter("cemail", "STRING", e.get("counterparty_email") or ""),
                bigquery.ScalarQueryParameter("cname", "STRING", e.get("counterparty_name") or ""),
                bigquery.ScalarQueryParameter("etype", "STRING", e.get("entity_type") or ""),
                bigquery.ScalarQueryParameter("ename", "STRING", e.get("entity_name") or ""),
                bigquery.ScalarQueryParameter("subj", "STRING", e.get("subject") or ""),
                bigquery.ScalarQueryParameter("snip", "STRING", e.get("snippet") or ""),
                bigquery.ScalarQueryParameter("cls", "STRING", e.get("classification") or ""),
                bigquery.ScalarQueryParameter("summ", "STRING", e.get("summary") or ""),
                bigquery.ScalarQueryParameter("sent", "TIMESTAMP", e.get("sent_at")),
            ]
            try:
                self.client.query(q, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
                inserted += 1
            except Exception as ex:
                logger.error(f"Email log insert failed: {ex}")
        return inserted

    def add_activity_note(self, company_name: str, note_text: str, created_by: str = "Ishu Ratna") -> bool:
        """Add a note to a company's activity log."""
        if not self.client or not note_text.strip():
            return False
        return self._log_activity(company_name, "note", created_by, note_text=note_text)

    def get_activity_log(self, company_name: str, limit: int = 50) -> List[Dict]:
        """Get the activity log for a company, most recent first."""
        if not self.client:
            return []
        query = f"""
            SELECT id, company_name, action_type, old_status, new_status,
                   note_text, created_by, created_at
            FROM `{self.activity_table_id}`
            WHERE company_name = @name
            ORDER BY created_at DESC
            LIMIT {limit}
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", company_name)]
        )
        try:
            job = self.client.query(query, job_config=job_config)
            results = []
            for row in job.result():
                entry = dict(row)
                if entry.get("created_at"):
                    entry["created_at"] = entry["created_at"].isoformat()
                results.append(entry)
            return results
        except Exception as e:
            logger.error(f"Failed to get activity log for {company_name}: {e}")
            return []

    def _log_activity(self, company_name: str, action_type: str, created_by: str,
                      old_status: str = "", new_status: str = "", note_text: str = "",
                      event_time: str = None) -> bool:
        """
        Insert a row into the activity_log table.
        event_time: ISO timestamp of when the event ACTUALLY happened (e.g. when
        an email was received) — defaults to now for real-time events.
        """
        try:
            ts_expr = "@event_time" if event_time else "CURRENT_TIMESTAMP()"
            query = f"""
                INSERT INTO `{self.activity_table_id}`
                (id, company_name, action_type, old_status, new_status, note_text, created_by, created_at)
                VALUES (@id, @company_name, @action_type, @old_status, @new_status, @note_text, @created_by, {ts_expr})
            """
            params = [
                bigquery.ScalarQueryParameter("id", "STRING", str(uuid.uuid4())),
                bigquery.ScalarQueryParameter("company_name", "STRING", company_name),
                bigquery.ScalarQueryParameter("action_type", "STRING", action_type),
                bigquery.ScalarQueryParameter("old_status", "STRING", old_status),
                bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
                bigquery.ScalarQueryParameter("note_text", "STRING", note_text),
                bigquery.ScalarQueryParameter("created_by", "STRING", created_by),
            ]
            if event_time:
                params.append(bigquery.ScalarQueryParameter("event_time", "TIMESTAMP", event_time))
            self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
            return True
        except Exception as e:
            logger.error(f"Failed to log activity for {company_name}: {e}")
            return False

    # Fields that should NEVER be overwritten by a merge (they're set by SmartFill or are identity fields)
    PROTECTED_FIELDS = {"company_id", "name", "ingested_at", "match_score", "status"}

    def save_targets(self, companies: List[Dict]) -> bool:
        """
        Smart-dedup insert: new companies are INSERTed, existing companies get
        empty/null fields filled in from the incoming data (merge, never overwrite).
        """
        if not self.client or not companies:
            return False

        # 1. Fetch existing names
        try:
            query = f"SELECT DISTINCT name FROM `{self.table_id}`"
            query_job = self.client.query(query)
            existing_names = {row.name for row in query_job.result()}
        except Exception:
            existing_names = set()

        rows_to_insert = []
        rows_to_merge = []
        for c in companies:
            name = c.get("name", "").strip()
            if not name:
                continue
            if name in existing_names:
                rows_to_merge.append(c)
                continue

            def safe_float(val, default=0.0):
                if val is None or val == "" or val == "nan":
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            def safe_int(val, default=None):
                if val is None or val == "" or str(val).strip() == "":
                    return default
                try:
                    return int(float(val))
                except (ValueError, TypeError):
                    return default

            row = {
                "company_id": str(uuid.uuid4()),
                "name": name,
                "website": c.get("website", "") or "",
                "sector": c.get("sector", "") or "",
                "region": c.get("region", "") or "",
                "ownership": c.get("ownership", "") or "",
                "description": c.get("description", "") or "",
                "match_score": safe_float(c.get("match_score"), 0.0),
                "status": c.get("status", "Scraped") or "Scraped",
                "source": c.get("source", "Manual") or "Manual",
                "contact_name": c.get("contact_name") or "",
                "contact_email": c.get("contact_email") or "",
                "linkedin_url": c.get("linkedin_url") or "",
                "growth_signals": bool(c.get("growth_signals", False)),
                "estimated_ebitda": safe_float(c.get("estimated_ebitda"), 0.0),
                "ingested_at": datetime.utcnow().isoformat(),
                # ── Expanded PitchBook fields ──
                "contact_title": c.get("contact_title") or "",
                "contact_phone": c.get("contact_phone") or "",
                "hq_email": c.get("hq_email") or "",
                "hq_phone": c.get("hq_phone") or "",
                "hq_location": c.get("hq_location") or "",
                "hq_city": c.get("hq_city") or "",
                "hq_country": c.get("hq_country") or "",
                "employees": safe_int(c.get("employees")),
                "year_founded": safe_int(c.get("year_founded")),
                "keywords": c.get("keywords") or "",
                "verticals": c.get("verticals") or "",
                "industry_group": c.get("industry_group") or "",
                "industry_code": c.get("industry_code") or "",
                "emerging_spaces": c.get("emerging_spaces") or "",
                "business_status": c.get("business_status") or "",
                "financing_status": c.get("financing_status") or "",
                "total_raised_m": safe_float(c.get("total_raised_m")),
                "revenue_m": safe_float(c.get("revenue_m")),
                "net_income_m": safe_float(c.get("net_income_m")),
                "enterprise_value_m": safe_float(c.get("enterprise_value_m")),
                "revenue_growth_pct": safe_float(c.get("revenue_growth_pct")),
                "valuation_estimate_m": safe_float(c.get("valuation_estimate_m")),
                "last_valuation_m": safe_float(c.get("last_valuation_m")),
                "last_valuation_date": c.get("last_valuation_date") or "",
                "active_investors": c.get("active_investors") or "",
                "num_active_investors": safe_int(c.get("num_active_investors")),
                "former_investors": c.get("former_investors") or "",
                "last_financing_date": c.get("last_financing_date") or "",
                "last_financing_size_m": safe_float(c.get("last_financing_size_m")),
                "last_financing_valuation_m": safe_float(c.get("last_financing_valuation_m")),
                "last_financing_type": c.get("last_financing_type") or "",
                "first_financing_date": c.get("first_financing_date") or "",
                "first_financing_size_m": safe_float(c.get("first_financing_size_m")),
                "pitchbook_growth_rate": safe_float(c.get("pitchbook_growth_rate")),
                "growth_rate_percentile": safe_int(c.get("growth_rate_percentile")),
                "web_visitors": safe_int(c.get("web_visitors")),
                "opportunity_score": safe_int(c.get("opportunity_score")),
                "success_probability": safe_int(c.get("success_probability")),
                "ma_probability": safe_int(c.get("ma_probability")),
                "predicted_exit_type": c.get("predicted_exit_type") or "",
                "total_patents": safe_int(c.get("total_patents")),
                "competitors": c.get("competitors") or "",
                "also_known_as": c.get("also_known_as") or "",
                "legal_name": c.get("legal_name") or "",
                "registration_number": c.get("registration_number") or "",
                "financing_note": c.get("financing_note") or "",
                # ── Filed-financial fields (CH convention, raw GBP) — used by
                # Inven uploads which arrive with actual revenue/assets ──
                "revenue_y1": safe_float(c.get("revenue_y1"), None),
                "revenue_y1_date": c.get("revenue_y1_date") or "",
                "revenue_y2": safe_float(c.get("revenue_y2"), None),
                "revenue_y2_date": c.get("revenue_y2_date") or "",
                "revenue_y3": safe_float(c.get("revenue_y3"), None),
                "revenue_y3_date": c.get("revenue_y3_date") or "",
                "total_assets_y1": safe_float(c.get("total_assets_y1"), None),
                # ── Inven growth/identity fields ──
                "revenue_cagr_3yr_pct": safe_float(c.get("revenue_cagr_3yr_pct"), None),
                "employee_growth_1yr_pct": safe_float(c.get("employee_growth_1yr_pct"), None),
                "employee_growth_3yr_pct": safe_float(c.get("employee_growth_3yr_pct"), None),
                "ebitda_margin_pct": safe_float(c.get("ebitda_margin_pct"), None),
                "directors": c.get("directors") or "",
                "company_linkedin": c.get("company_linkedin") or "",
            }
            rows_to_insert.append(row)
            existing_names.add(name)

        if not rows_to_insert:
            logger.info("No new unique targets to insert.")
            return True

        # Use DML INSERT instead of streaming to avoid streaming buffer lock
        # Process in batches of 50 to stay within BQ query size limits
        batch_size = 50
        total_inserted = 0
        for i in range(0, len(rows_to_insert), batch_size):
            batch = rows_to_insert[i:i + batch_size]
            values_clauses = []
            params = []
            for idx, row in enumerate(batch):
                prefix = f"r{idx}"
                placeholders = ", ".join([
                    f"@{prefix}_cid", f"@{prefix}_name", f"@{prefix}_website",
                    f"@{prefix}_sector", f"@{prefix}_region", f"@{prefix}_ownership",
                    f"@{prefix}_desc", f"@{prefix}_score", f"@{prefix}_status",
                    f"@{prefix}_source", f"@{prefix}_contact", f"@{prefix}_email",
                    f"@{prefix}_linkedin", f"@{prefix}_growth", f"@{prefix}_ebitda",
                    f"@{prefix}_ts",
                    f"@{prefix}_contact_title", f"@{prefix}_contact_phone",
                    f"@{prefix}_hq_email", f"@{prefix}_hq_phone",
                    f"@{prefix}_hq_location", f"@{prefix}_hq_city", f"@{prefix}_hq_country",
                    f"@{prefix}_employees", f"@{prefix}_year_founded",
                    f"@{prefix}_keywords", f"@{prefix}_verticals",
                    f"@{prefix}_industry_group", f"@{prefix}_industry_code",
                    f"@{prefix}_emerging_spaces", f"@{prefix}_business_status",
                    f"@{prefix}_financing_status", f"@{prefix}_total_raised_m",
                    f"@{prefix}_revenue_m", f"@{prefix}_net_income_m",
                    f"@{prefix}_enterprise_value_m", f"@{prefix}_revenue_growth_pct",
                    f"@{prefix}_valuation_estimate_m", f"@{prefix}_last_valuation_m",
                    f"@{prefix}_last_valuation_date", f"@{prefix}_active_investors",
                    f"@{prefix}_num_active_investors", f"@{prefix}_former_investors",
                    f"@{prefix}_last_financing_date", f"@{prefix}_last_financing_size_m",
                    f"@{prefix}_last_financing_valuation_m", f"@{prefix}_last_financing_type",
                    f"@{prefix}_first_financing_date", f"@{prefix}_first_financing_size_m",
                    f"@{prefix}_pitchbook_growth_rate", f"@{prefix}_growth_rate_percentile",
                    f"@{prefix}_web_visitors", f"@{prefix}_opportunity_score",
                    f"@{prefix}_success_probability", f"@{prefix}_ma_probability",
                    f"@{prefix}_predicted_exit_type", f"@{prefix}_total_patents",
                    f"@{prefix}_competitors", f"@{prefix}_also_known_as",
                    f"@{prefix}_legal_name", f"@{prefix}_registration_number",
                    f"@{prefix}_financing_note",
                    f"@{prefix}_revenue_y1", f"@{prefix}_revenue_y1_date",
                    f"@{prefix}_revenue_y2", f"@{prefix}_revenue_y2_date",
                    f"@{prefix}_revenue_y3", f"@{prefix}_revenue_y3_date",
                    f"@{prefix}_total_assets_y1",
                    f"@{prefix}_revenue_cagr_3yr_pct", f"@{prefix}_employee_growth_1yr_pct",
                    f"@{prefix}_employee_growth_3yr_pct", f"@{prefix}_ebitda_margin_pct",
                    f"@{prefix}_directors", f"@{prefix}_company_linkedin",
                ])
                values_clauses.append(f"({placeholders})")
                params.extend([
                    bigquery.ScalarQueryParameter(f"{prefix}_cid", "STRING", row["company_id"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_name", "STRING", row["name"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_website", "STRING", row["website"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_sector", "STRING", row["sector"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_region", "STRING", row["region"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_ownership", "STRING", row["ownership"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_desc", "STRING", row["description"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_score", "FLOAT64", row["match_score"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_status", "STRING", row["status"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_source", "STRING", row["source"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_contact", "STRING", row["contact_name"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_email", "STRING", row["contact_email"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_linkedin", "STRING", row["linkedin_url"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_growth", "BOOL", row["growth_signals"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_ebitda", "FLOAT64", row["estimated_ebitda"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_ts", "STRING", row["ingested_at"]),
                    # ── Expanded fields ──
                    bigquery.ScalarQueryParameter(f"{prefix}_contact_title", "STRING", row["contact_title"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_contact_phone", "STRING", row["contact_phone"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_hq_email", "STRING", row["hq_email"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_hq_phone", "STRING", row["hq_phone"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_hq_location", "STRING", row["hq_location"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_hq_city", "STRING", row["hq_city"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_hq_country", "STRING", row["hq_country"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_employees", "INT64", row["employees"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_year_founded", "INT64", row["year_founded"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_keywords", "STRING", row["keywords"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_verticals", "STRING", row["verticals"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_industry_group", "STRING", row["industry_group"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_industry_code", "STRING", row["industry_code"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_emerging_spaces", "STRING", row["emerging_spaces"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_business_status", "STRING", row["business_status"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_financing_status", "STRING", row["financing_status"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_total_raised_m", "FLOAT64", row["total_raised_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_m", "FLOAT64", row["revenue_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_net_income_m", "FLOAT64", row["net_income_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_enterprise_value_m", "FLOAT64", row["enterprise_value_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_growth_pct", "FLOAT64", row["revenue_growth_pct"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_valuation_estimate_m", "FLOAT64", row["valuation_estimate_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_valuation_m", "FLOAT64", row["last_valuation_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_valuation_date", "STRING", row["last_valuation_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_active_investors", "STRING", row["active_investors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_num_active_investors", "INT64", row["num_active_investors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_former_investors", "STRING", row["former_investors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_financing_date", "STRING", row["last_financing_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_financing_size_m", "FLOAT64", row["last_financing_size_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_financing_valuation_m", "FLOAT64", row["last_financing_valuation_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_last_financing_type", "STRING", row["last_financing_type"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_first_financing_date", "STRING", row["first_financing_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_first_financing_size_m", "FLOAT64", row["first_financing_size_m"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_pitchbook_growth_rate", "FLOAT64", row["pitchbook_growth_rate"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_growth_rate_percentile", "INT64", row["growth_rate_percentile"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_web_visitors", "INT64", row["web_visitors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_opportunity_score", "INT64", row["opportunity_score"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_success_probability", "INT64", row["success_probability"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_ma_probability", "INT64", row["ma_probability"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_predicted_exit_type", "STRING", row["predicted_exit_type"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_total_patents", "INT64", row["total_patents"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_competitors", "STRING", row["competitors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_also_known_as", "STRING", row["also_known_as"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_legal_name", "STRING", row["legal_name"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_registration_number", "STRING", row["registration_number"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_financing_note", "STRING", row["financing_note"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y1", "FLOAT64", row["revenue_y1"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y1_date", "STRING", row["revenue_y1_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y2", "FLOAT64", row["revenue_y2"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y2_date", "STRING", row["revenue_y2_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y3", "FLOAT64", row["revenue_y3"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_y3_date", "STRING", row["revenue_y3_date"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_total_assets_y1", "FLOAT64", row["total_assets_y1"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_revenue_cagr_3yr_pct", "FLOAT64", row["revenue_cagr_3yr_pct"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_employee_growth_1yr_pct", "FLOAT64", row["employee_growth_1yr_pct"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_employee_growth_3yr_pct", "FLOAT64", row["employee_growth_3yr_pct"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_ebitda_margin_pct", "FLOAT64", row["ebitda_margin_pct"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_directors", "STRING", row["directors"]),
                    bigquery.ScalarQueryParameter(f"{prefix}_company_linkedin", "STRING", row["company_linkedin"]),
                ])

            insert_query = f"""
                INSERT INTO `{self.table_id}`
                (company_id, name, website, sector, region, ownership, description,
                 match_score, status, source, contact_name, contact_email, linkedin_url,
                 growth_signals, estimated_ebitda, ingested_at,
                 contact_title, contact_phone, hq_email, hq_phone,
                 hq_location, hq_city, hq_country,
                 employees, year_founded, keywords, verticals,
                 industry_group, industry_code, emerging_spaces, business_status,
                 financing_status, total_raised_m, revenue_m, net_income_m,
                 enterprise_value_m, revenue_growth_pct,
                 valuation_estimate_m, last_valuation_m, last_valuation_date,
                 active_investors, num_active_investors, former_investors,
                 last_financing_date, last_financing_size_m, last_financing_valuation_m,
                 last_financing_type, first_financing_date, first_financing_size_m,
                 pitchbook_growth_rate, growth_rate_percentile, web_visitors,
                 opportunity_score, success_probability, ma_probability,
                 predicted_exit_type, total_patents, competitors,
                 also_known_as, legal_name, registration_number, financing_note,
                 revenue_y1, revenue_y1_date, revenue_y2, revenue_y2_date,
                 revenue_y3, revenue_y3_date, total_assets_y1,
                 revenue_cagr_3yr_pct, employee_growth_1yr_pct,
                 employee_growth_3yr_pct, ebitda_margin_pct,
                 directors, company_linkedin)
                VALUES {', '.join(values_clauses)}
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            try:
                job = self.client.query(insert_query, job_config=job_config)
                job.result()
                total_inserted += len(batch)
            except Exception as e:
                logger.error(f"DML INSERT batch failed: {e}")
                return False

        logger.info(f"Successfully inserted {total_inserted} unique rows into BigQuery via DML.")

        # ── Phase 2: Merge new data into existing duplicates ──
        if rows_to_merge:
            merged_count = self._merge_duplicates(rows_to_merge)
            logger.info(f"Smart-merge: updated {merged_count} existing records with new field data.")

        return True

    def _merge_duplicates(self, companies: List[Dict]) -> int:
        """
        For each duplicate company, fill in fields that are currently empty/null
        in BQ with values from the incoming data. Never overwrite non-empty fields.
        Protected fields (match_score, status, company_id, ingested_at) are never touched.
        """
        # All mergeable field names and their BQ types
        MERGE_FIELDS = [
            ("website", "STRING"), ("sector", "STRING"), ("region", "STRING"),
            ("ownership", "STRING"), ("description", "STRING"),
            ("source", "STRING"), ("contact_name", "STRING"), ("contact_email", "STRING"),
            ("linkedin_url", "STRING"),
            ("estimated_ebitda", "FLOAT64"),
            ("contact_title", "STRING"), ("contact_phone", "STRING"),
            ("hq_email", "STRING"), ("hq_phone", "STRING"),
            ("hq_location", "STRING"), ("hq_city", "STRING"), ("hq_country", "STRING"),
            ("employees", "INT64"), ("year_founded", "INT64"),
            ("keywords", "STRING"), ("verticals", "STRING"),
            ("industry_group", "STRING"), ("industry_code", "STRING"),
            ("emerging_spaces", "STRING"), ("business_status", "STRING"),
            ("financing_status", "STRING"), ("total_raised_m", "FLOAT64"),
            ("revenue_m", "FLOAT64"), ("net_income_m", "FLOAT64"),
            ("enterprise_value_m", "FLOAT64"), ("revenue_growth_pct", "FLOAT64"),
            ("valuation_estimate_m", "FLOAT64"), ("last_valuation_m", "FLOAT64"),
            ("last_valuation_date", "STRING"), ("active_investors", "STRING"),
            ("num_active_investors", "INT64"), ("former_investors", "STRING"),
            ("last_financing_date", "STRING"), ("last_financing_size_m", "FLOAT64"),
            ("last_financing_valuation_m", "FLOAT64"), ("last_financing_type", "STRING"),
            ("first_financing_date", "STRING"), ("first_financing_size_m", "FLOAT64"),
            ("pitchbook_growth_rate", "FLOAT64"), ("growth_rate_percentile", "INT64"),
            ("web_visitors", "INT64"), ("opportunity_score", "INT64"),
            ("success_probability", "INT64"), ("ma_probability", "INT64"),
            ("predicted_exit_type", "STRING"), ("total_patents", "INT64"),
            ("competitors", "STRING"), ("also_known_as", "STRING"),
            ("legal_name", "STRING"), ("registration_number", "STRING"),
            ("financing_note", "STRING"),
        ]

        merged = 0
        for c in companies:
            name = c.get("name", "").strip()
            if not name:
                continue

            # Build SET clauses: only update fields where existing is empty/null AND incoming has data
            set_clauses = []
            params = [bigquery.ScalarQueryParameter("merge_name", "STRING", name)]

            for field_name, field_type in MERGE_FIELDS:
                val = c.get(field_name)
                # Skip if incoming value is empty/None/zero
                if val is None or val == "" or val == 0 or val == 0.0:
                    continue

                param_name = f"m_{field_name}"

                # For STRING fields: update if existing is NULL or empty string
                if field_type == "STRING":
                    set_clauses.append(
                        f"{field_name} = CASE WHEN ({field_name} IS NULL OR {field_name} = '') THEN @{param_name} ELSE {field_name} END"
                    )
                    params.append(bigquery.ScalarQueryParameter(param_name, "STRING", str(val)))
                # For numeric fields: update if existing is NULL or 0
                elif field_type == "FLOAT64":
                    try:
                        float_val = float(val)
                    except (ValueError, TypeError):
                        continue
                    if float_val == 0.0:
                        continue
                    set_clauses.append(
                        f"{field_name} = CASE WHEN ({field_name} IS NULL OR {field_name} = 0.0) THEN @{param_name} ELSE {field_name} END"
                    )
                    params.append(bigquery.ScalarQueryParameter(param_name, "FLOAT64", float_val))
                elif field_type == "INT64":
                    try:
                        int_val = int(float(val))
                    except (ValueError, TypeError):
                        continue
                    if int_val == 0:
                        continue
                    set_clauses.append(
                        f"{field_name} = CASE WHEN ({field_name} IS NULL OR {field_name} = 0) THEN @{param_name} ELSE {field_name} END"
                    )
                    params.append(bigquery.ScalarQueryParameter(param_name, "INT64", int_val))

            if not set_clauses:
                continue

            update_query = f"""
                UPDATE `{self.table_id}`
                SET {', '.join(set_clauses)}
                WHERE name = @merge_name
            """
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            try:
                job = self.client.query(update_query, job_config=job_config)
                job.result()
                merged += 1
            except Exception as e:
                logger.warning(f"Smart-merge failed for '{name}': {e}")

        return merged

    def get_pipeline(self) -> List[Dict]:
        if not self.client:
            return []
        query = f"""
            SELECT * FROM `{self.table_id}`
            WHERE status IN ('Qualified', 'Contacted', 'Meeting', 'DD', 'Offer', 'Engaged')
            ORDER BY name ASC
        """
        return self._run_query(query)

    def get_universe(self) -> List[Dict]:
        if not self.client:
            return []
        query = f"""
            SELECT * FROM `{self.table_id}`
            ORDER BY ingested_at DESC
        """
        return self._run_query(query)

    def update_company_enrichment(self, company_name: str, enrichment_data: Dict) -> bool:
        if not self.client:
            return False
        contact_name = enrichment_data.get('contact_name', '')
        contact_email = enrichment_data.get('contact_email', '')
        linkedin_url = enrichment_data.get('linkedin_url', '')
        query = f"""
            UPDATE `{self.table_id}`
            SET 
                contact_name = @contact_name,
                contact_email = @contact_email,
                linkedin_url = @linkedin_url,
                status = 'Under Review'
            WHERE name = @name
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("contact_name", "STRING", contact_name),
                bigquery.ScalarQueryParameter("contact_email", "STRING", contact_email),
                bigquery.ScalarQueryParameter("linkedin_url", "STRING", linkedin_url),
                bigquery.ScalarQueryParameter("name", "STRING", company_name)
            ]
        )
        try:
            query_job = self.client.query(query, job_config=job_config)
            query_job.result()
            return True
        except Exception as e:
            logger.error(f"Failed to update company in BigQuery: {e}")
            return False

    def get_unenriched_targets(self) -> List[Dict]:
        query = f"""
            SELECT name, website, sector, region FROM `{self.table_id}`
            WHERE (contact_name IS NULL OR contact_name = '')
               OR (contact_email IS NULL OR contact_email = '')
            LIMIT 50
        """
        return self._run_query(query)

    def _run_query(self, query: str) -> List[Dict]:
        try:
            query_job = self.client.query(query)
            results = query_job.result()
            companies = []
            for row in results:
                c = dict(row)
                if 'ingested_at' in c and c['ingested_at']:
                    c['ingested_at'] = c['ingested_at'].isoformat()
                companies.append(c)
            return companies
        except Exception as e:
            logger.error(f"BigQuery Query Failed: {e}")
            return []
