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
        ("other_preferences", "STRING"),
        ("registration_number", "STRING"),   # UK Companies House number where present
        ("pb_last_updated", "STRING"),
        # Companies House registry intelligence (UK entities)
        ("psc_summary", "STRING"),           # who controls the vehicle — UHNWI discovery
        ("officers_summary", "STRING"),      # active directors (principals to contact)
        ("net_assets_m", "FLOAT64"),         # filed net assets, £M — AUM proxy
        ("ingested_at", "TIMESTAMP"),
        ("updated_at", "TIMESTAMP"),
    ]

    # PitchBook fields written on merge: strings fill gaps only; PB IDs/counters always refresh
    _MERGE_FILL_STRINGS = [
        "investor_type", "region", "hq_city", "hq_country", "website", "description",
        "contact_name", "contact_email", "linkedin_url", "aka", "contact_title",
        "contact_phone", "hq_email", "global_region", "strategy_preferences",
        "geo_preferences", "open_to_first_time", "other_preferences", "registration_number",
    ]
    _MERGE_FILL_NUMERICS = [
        "aum_m", "ticket_min_m", "ticket_max_m", "year_founded",
        "num_commitments", "num_active_commitments", "num_pe_commitments", "total_commitments_m",
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
        ]
        float_cols = ["aum_m", "ticket_min_m", "ticket_max_m", "lp_fit_score",
                      "score_geography", "score_pe_appetite", "score_ticket_fit",
                      "score_tech_affinity", "total_commitments_m"]
        int_cols = ["year_founded", "num_commitments", "num_active_commitments", "num_pe_commitments"]
        all_cols = string_cols + float_cols + int_cols

        inserted = 0
        BATCH = 200  # 200 rows × 41 params = 8,200 < BQ's 10,000-parameter limit
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

        merged = 0
        for inv in to_merge:
            if self._merge_investor(inv):
                merged += 1
        logger.info(f"Upsert complete: {inserted} inserted, {merged} merged")
        return {"inserted": inserted, "merged": merged}

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
                bq_type = "INT64" if col in ("year_founded", "num_commitments", "num_active_commitments", "num_pe_commitments") else "FLOAT64"
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
            psc_summary = CASE WHEN @psc_summary != '' THEN @psc_summary ELSE psc_summary END,
            officers_summary = CASE WHEN @officers_summary != '' THEN @officers_summary ELSE officers_summary END,
            net_assets_m = IFNULL(@net_assets_m, net_assets_m),
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
