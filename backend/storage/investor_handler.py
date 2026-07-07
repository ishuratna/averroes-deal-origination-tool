"""
Investor (LP) database handler — BigQuery.

Second database alongside `targets`: potential LPs to invest through Averroes —
funds of funds, family offices, HNWIs/UHNWIs, and other private-capital investors.

Relationship stages: Identified → Researched → Contacted → Meeting → Committed / Passed
"""
import uuid
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone

from google.cloud import bigquery

logger = logging.getLogger(__name__)

INVESTOR_STAGES = ["Identified", "Researched", "Contacted", "Meeting", "Committed", "Passed"]

INVESTOR_TYPES = [
    "Family Office", "Fund of Funds", "HNWI", "UHNWI",
    "VC", "PE", "Angel", "Corporate", "Sovereign/Institutional", "Unknown",
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
        ("ingested_at", "TIMESTAMP"),
        ("updated_at", "TIMESTAMP"),
    ]

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
        query = f"SELECT * FROM `{self.table_id}` ORDER BY lp_fit_score DESC NULLS LAST, name ASC"
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
                "ingested_at": now,
                "updated_at": now,
            })

        if not rows:
            return 0
        errors = self.client.insert_rows_json(self.table_id, rows)
        if errors:
            logger.error(f"Investor insert errors: {errors}")
            return 0
        logger.info(f"Inserted {len(rows)} new investors")
        return len(rows)

    def update_status(self, name: str, new_status: str) -> bool:
        if not self.client or new_status not in INVESTOR_STAGES:
            return False
        query = f"""UPDATE `{self.table_id}`
                    SET status = @status, updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)"""
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", new_status),
            bigquery.ScalarQueryParameter("name", "STRING", name),
        ])
        try:
            self.client.query(query, job_config=job_config).result()
            return True
        except Exception as e:
            logger.error(f"Failed to update investor status: {e}")
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
            contact_email = @contact_email,
            linkedin_url = @linkedin_url,
            lp_fit_score = @lp_fit_score,
            score_geography = @score_geography,
            score_pe_appetite = @score_pe_appetite,
            score_ticket_fit = @score_ticket_fit,
            score_tech_affinity = @score_tech_affinity,
            fit_details = @fit_details,
            status = CASE WHEN status = 'Identified' THEN 'Researched' ELSE status END,
            updated_at = CURRENT_TIMESTAMP()
            WHERE LOWER(name) = LOWER(@name)"""
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("investor_type", "STRING", fields.get("investor_type") or "Unknown"),
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
