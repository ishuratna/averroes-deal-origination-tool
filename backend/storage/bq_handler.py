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
        
        self.init_error = None
        try:
            self.client = bigquery.Client(project=project_id)
        except Exception as e:
            logger.warning(f"BigQuery Client could not be initialized: {e}")
            self.init_error = str(e)
            self.client = None

    def save_targets(self, companies: List[Dict]) -> bool:
        """
        Inserts new target companies into BigQuery using DML INSERT (not streaming),
        so rows can be updated immediately after insertion.
        """
        if not self.client or not companies:
            return False

        # 1. Fetch existing names to avoid duplicates
        try:
            query = f"SELECT DISTINCT name FROM `{self.table_id}`"
            query_job = self.client.query(query)
            existing_names = {row.name for row in query_job.result()}
        except Exception:
            existing_names = set()

        rows_to_insert = []
        for c in companies:
            name = c.get("name", "").strip()
            if not name or name in existing_names:
                continue
            
            def safe_float(val, default=0.0):
                if val is None or val == "" or val == "nan":
                    return default
                try:
                    return float(val)
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
                "ingested_at": datetime.utcnow().isoformat()
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
                values_clauses.append(
                    f"(@{prefix}_cid, @{prefix}_name, @{prefix}_website, @{prefix}_sector, "
                    f"@{prefix}_region, @{prefix}_ownership, @{prefix}_desc, @{prefix}_score, "
                    f"@{prefix}_status, @{prefix}_source, @{prefix}_contact, @{prefix}_email, "
                    f"@{prefix}_linkedin, @{prefix}_growth, @{prefix}_ebitda, @{prefix}_ts)"
                )
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
                ])

            insert_query = f"""
                INSERT INTO `{self.table_id}` 
                (company_id, name, website, sector, region, ownership, description, 
                 match_score, status, source, contact_name, contact_email, linkedin_url,
                 growth_signals, estimated_ebitda, ingested_at)
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
        return True

    def get_pipeline(self) -> List[Dict]:
        if not self.client:
            return []
        query = f"""
            SELECT * FROM `{self.table_id}`
            WHERE status != 'Not a Fit'
            ORDER BY match_score DESC
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
