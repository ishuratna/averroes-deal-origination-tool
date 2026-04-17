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
        
        try:
            self.client = bigquery.Client(project=project_id)
        except Exception as e:
            logger.warning(f"BigQuery Client could not be initialized: {e}")
            self.client = None

    def save_targets(self, companies: List[Dict]) -> bool:
        """
        Inserts new target companies into BigQuery.
        """
        if not self.client or not companies:
            return False

        rows_to_insert = []
        for c in companies:
            # Map Python dict to BQ Schema
            row = {
                "company_id": str(uuid.uuid4()),
                "name": c.get("name", ""),
                "website": c.get("website", ""),
                "sector": c.get("sector", ""),
                "region": c.get("region", ""),
                "ownership": c.get("ownership", ""),
                "description": c.get("description", ""),
                "match_score": float(c.get("match_score", 0.0)),
                "status": c.get("status", "Scraped"),
                "source": c.get("source", "Manual"),
                "contact_name": c.get("contact_name", None),
                "contact_email": c.get("contact_email", None),
                "linkedin_url": c.get("linkedin_url", None),
                "growth_signals": bool(c.get("growth_signals", False)),
                "estimated_ebitda": float(c.get("estimated_ebitda", 0.0)),
                "ingested_at": datetime.utcnow().isoformat()
            }
            rows_to_insert.append(row)

        errors = self.client.insert_rows_json(self.table_id, rows_to_insert)
        if errors:
            logger.error(f"Encountered errors while inserting rows: {errors}")
            return False
        
        logger.info(f"Successfully inserted {len(rows_to_insert)} rows into BigQuery.")
        return True

    def get_pipeline(self) -> List[Dict]:
        """
        Retrieves companies that are qualified for the pipeline.
        Filters out 'Not a Fit' status.
        """
        if not self.client:
            return []
            
        query = f"""
            SELECT * FROM `{self.table_id}`
            WHERE status != 'Not a Fit'
            ORDER BY match_score DESC
        """
        
        return self._run_query(query)

    def get_universe(self) -> List[Dict]:
        """
        Retrieves all companies ever scraped (Data Lake).
        """
        if not self.client:
            return []
            
        query = f"""
            SELECT * FROM `{self.table_id}`
            ORDER BY ingested_at DESC
        """
        
        return self._run_query(query)

    def update_company_enrichment(self, company_name: str, enrichment_data: Dict) -> bool:
        """
        Updates a company record with new enriched data (DML UPDATE).
        Updates status to 'Under Review' if not specified.
        """
        if not self.client:
            return False

        contact_name = enrichment_data.get('contact_name', '')
        contact_email = enrichment_data.get('contact_email', '')
        linkedin_url = enrichment_data.get('linkedin_url', '')
        
        # In BQ we recommend parameterized queries to avoid SQL injection
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
            query_job.result()  # Waits for job to complete
            return True
        except Exception as e:
            logger.error(f"Failed to update company in BigQuery: {e}")
            return False

    def _run_query(self, query: str) -> List[Dict]:
        try:
            query_job = self.client.query(query)
            results = query_job.result()
            companies = []
            for row in results:
                c = dict(row)
                # handle datetime serialization
                if 'ingested_at' in c and c['ingested_at']:
                    c['ingested_at'] = c['ingested_at'].isoformat()
                companies.append(c)
            return companies
        except Exception as e:
            logger.error(f"BigQuery Query Failed: {e}")
            return []
