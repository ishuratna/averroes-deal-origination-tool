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
            self._ensure_expanded_schema()
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
    ]

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
                 also_known_as, legal_name, registration_number, financing_note)
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
