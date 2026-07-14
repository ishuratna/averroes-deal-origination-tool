import os
import json
import logging
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, File, UploadFile
from pydantic import BaseModel
from typing import Dict, List, Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from scrapers.conference_scraper import ConferenceScraper
from scrapers.marketplace_scraper import MarketplaceScraper
from scrapers.ranking_scraper import RankingListScraper
from scrapers.directory_scraper import DirectoryScraper
from scrapers.network_scraper import NetworkScraper
from scrapers.investor_scraper import InvestorScraper
from storage.investor_handler import InvestorBQHandler, INVESTOR_STAGES
from ai.investor_fill import investor_fill, mine_investors_from_companies
from services.investor_upload_service import parse_investor_file
from auth import auth_middleware, auth_enabled, AUTH_CLIENT_ID, ALLOWED_DOMAIN
from storage.gcs_handler import GCSHandler
from storage.bq_handler import BigQueryHandler
from services.excel_service import parse_proprietary_excel
from services.pitchbook_service import parse_pitchbook_excel
from services.outreach_service import draft_outreach_email, send_email
from ai.criteria import (
    AverroesPhilosophy, evaluate_target, generate_analysis_prompt,
    qualify_company, qualify_company_with_gemini,
    set_criteria_from_bq, preview_criteria,
)
from ai.enrichment import EnrichmentAgent
from services.companies_house_service import extract_ch_financials
from ai.scoring import score_company, compute_revenue_band, estimate_revenue_m
from config.sourcing_config import SOURCING_CRITERIA

# Load .env for local development; Cloud Run injects env vars directly
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── GCP Configuration (read from environment) ───────────────────────────────
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "averroes-deal-origination")
GCS_BUCKET  = os.getenv("GCS_BUCKET", "averroes-deal-intelligence")
BQ_DATASET  = os.getenv("BIGQUERY_DATASET", "averroes_deal_flow")

logger.info(f"Starting API | project={GCP_PROJECT} | bucket={GCS_BUCKET} | bq_dataset={BQ_DATASET}")

# Ensure the data directory exists for local JSON storage
os.makedirs("data", exist_ok=True)

app = FastAPI(title="Averroes Deal Origination API")

# Google Sign-In authentication (enforced only when GOOGLE_OAUTH_CLIENT_ID is set).
# Registered BEFORE CORS so CORS is outermost — auth 401/403 responses then
# carry CORS headers and are readable by the frontend.
app.middleware("http")(auth_middleware)

# Add CORS Middleware to allow the Next.js frontend to fetch data
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to the frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if auth_enabled():
    logger.info(f"Auth ENABLED — allowed domain: @{ALLOWED_DOMAIN}")
else:
    logger.warning("Auth DISABLED — set GOOGLE_OAUTH_CLIENT_ID to enforce sign-in")


@app.get("/auth/config")
async def auth_config():
    """Frontend bootstrap: is auth on, and which OAuth client to use. Public by design."""
    return {"auth_enabled": auth_enabled(), "client_id": AUTH_CLIENT_ID, "allowed_domain": ALLOWED_DOMAIN}


class SessionRequest(BaseModel):
    credential: str


@app.post("/auth/session")
async def create_session(req: SessionRequest):
    """Exchange a fresh Google ID token (1h life) for a 12h session token."""
    from auth import issue_session_token
    try:
        token, exp = issue_session_token(req.credential)
        return {"session_token": token, "exp": exp}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception:
        raise HTTPException(status_code=401, detail="Sign-in could not be verified")

conf_scraper = ConferenceScraper()
market_scraper = MarketplaceScraper()
rank_scraper = RankingListScraper()
directory_scraper = DirectoryScraper()
network_scraper = NetworkScraper()
enrichment_agent = EnrichmentAgent()
gcs_handler = GCSHandler(bucket_name=GCS_BUCKET)
bq_handler = BigQueryHandler(project_id=GCP_PROJECT, dataset_id=BQ_DATASET)
investor_handler = InvestorBQHandler(bq_handler.client, bq_handler.project_id, dataset_id=BQ_DATASET)
investor_scraper = InvestorScraper()

# ─── Load qualification criteria from BQ into criteria module at startup ──────
try:
    _startup_criteria = bq_handler.get_criteria()
    set_criteria_from_bq(_startup_criteria)
    logger.info(f"Loaded qualification criteria from BQ (v{_startup_criteria.get('_version', '?')})")
except Exception as _e:
    logger.warning(f"Could not load BQ criteria at startup, using defaults: {_e}")

# --- Utilities ---

def _sync_to_databases(refined_companies: List[dict]):
    """
    Saves evaluated targets into BigQuery.
    """
    # Insert evaluated companies directly into BigQuery
    success = bq_handler.save_targets(refined_companies)
    if not success:
        logger.error("Failed to sync to target database in BigQuery.")
        
    # Get true total counts from BigQuery to return to the frontend
    universe_total = len(bq_handler.get_universe())
    pipeline_total = len(bq_handler.get_pipeline())
        
    return universe_total, pipeline_total

def _process_and_refine(raw_companies: List[dict]):
    """
    Unified AI evaluation and enrichment logic.
    """
    philosophy = AverroesPhilosophy()
    refined = []
    
    for c in raw_companies:
        score = evaluate_target(c, philosophy)
        c["match_score"] = score
        
        ingestion_threshold = SOURCING_CRITERIA.get("min_ingestion_score", 0.3)
        
        if score >= ingestion_threshold:
            c["status"] = "Under Review" if score >= 0.6 else "Qualified"
            # Fully automated enrichment for all potential targets
            founder_info = enrichment_agent.enrich_founder_details(c['name'])
            # Only update if found something real, avoid overwriting with NA
            for key, val in founder_info.items():
                if val:
                    c[key] = val
        else:
            c["status"] = "Not a Fit"
            
        refined.append(c)
    
    uni_count, cand_count = _sync_to_databases(refined)
    return refined, uni_count, cand_count

# --- Models ---
class CompanyTarget(BaseModel):
    name: str
    website: Optional[str] = None
    sector: Optional[str] = "Unknown"
    description: Optional[str] = ""
    match_score: float = 0.0
    source: str = "Manual"
    status: str = "Qualified"
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    region: Optional[str] = None
    ownership: Optional[str] = None

# --- Endpoints ---

@app.get("/")
async def root():
    return {
        "status": "Averroes Intelligence Platform Active",
        "version": "v1.4.0",
        "project": GCP_PROJECT,
        "bucket": GCS_BUCKET,
        "bq_dataset": BQ_DATASET,
        "gemini_enabled": bool(os.getenv("GEMINI_API_KEY")),
        "companies_house_enabled": bool(os.getenv("COMPANIES_HOUSE_API_KEY"))
    }

@app.post("/ingest/enrich-universe")
async def enrich_universe_contacts():
    """
    Background task to find contacts for all companies in the universe
    that are currently missing them.
    """
    to_enrich = bq_handler.get_unenriched_targets()
    if not to_enrich:
        return {"status": "Complete", "message": "No companies need contact enrichment currently."}
    
    count = 0
    for company in to_enrich:
        try:
            name = company['name']
            details = enrichment_agent.enrich_founder_details(name)
            
            # FOOL-PROOF LOGIC:
            if not details['contact_name'] and not details['contact_email']:
                # Tag as attempted so we don't waste retry energy
                details['contact_name'] = "[No Founder Found]"
                details['contact_email'] = "research@averroescapital.com" # Fallback to internal
                details['linkedin_url'] = "N/A"
            
            bq_handler.update_company_enrichment(name, details)
            count += 1
        except Exception as e:
            # On hard failure (API error), we leave it blank so it CAN be retried
            logger.warning(f"Technical failure enriching {company.get('name')}: {e}")
            continue
            
    return {
        "status": "Success",
        "message": f"Successfully retrieved and retrofilled {count} contacts.",
        "processed": len(to_enrich)
    }

@app.get("/pipeline")
async def get_pipeline():
    """
    Reads the active target pipeline from BigQuery.
    """
    try:
        return bq_handler.get_pipeline()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load pipeline: {str(e)}")


@app.get("/universe", response_model=List[dict])
async def get_universe():
    """
    Returns the complete Data Lake (Universe) from BigQuery.
    """
    try:
        return bq_handler.get_universe()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load universe: {str(e)}")

@app.post("/ingest/marketplace")
async def ingest_marketplace(marketplace_name: Optional[str] = Query(None, description="Name of the marketplace to scrape. If None, scrapes all.")):
    """
    Ingests deals from marketplaces (Acquire, Flippa, etc.)
    """
    if marketplace_name and marketplace_name not in market_scraper.get_supported_sources():
        raise HTTPException(status_code=404, detail="Marketplace not monitored.")
    
    if marketplace_name:
        raw_companies = market_scraper.scrape_source(marketplace_name)
        source_label = marketplace_name
    else:
        raw_companies = market_scraper.scrape_all()
        source_label = "All Marketplaces"

    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": f"No new companies found from {source_label}."}
    
    # Tag and save raw — no AI scoring, no enrichment
    for c in raw_companies:
        c["source"] = c.get("source", source_label)
        c["status"] = "Scraped"
        c["match_score"] = 0.0
    success = bq_handler.save_targets(raw_companies)
    if not success:
        raise HTTPException(status_code=500, detail="Database save failed.")
    gcs_filename = gcs_handler.save_companies(raw_companies, source_label.lower().replace(".", "").replace(" ", "_"))
    return {
        "status": "Success",
        "count": len(raw_companies),
        "source": source_label,
        "message": f"Scraped {len(raw_companies)} companies from {source_label}. Use SmartFill to score and enrich.",
        "gcs_path": gcs_filename
    }

@app.post("/ingest/conference")
async def ingest_conference(conference_name: str = Query(..., description="Name of the conference to scrape")):
    """Scrape conference → save raw to BQ. No AI. Use SmartFill per-company afterwards."""
    if conference_name not in conf_scraper.get_all_targets():
        raise HTTPException(status_code=404, detail="Conference not monitored.")
    raw_companies = conf_scraper.scrape_conference(conference_name)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new companies found."}
    for c in raw_companies:
        c["source"] = c.get("source", conference_name)
        c["status"] = "Scraped"
        c["match_score"] = 0.0
    success = bq_handler.save_targets(raw_companies)
    if not success:
        raise HTTPException(status_code=500, detail="Database save failed.")
    gcs_filename = gcs_handler.save_companies(raw_companies, conference_name.lower().replace(" ", "_"))
    return {
        "status": "Success",
        "count": len(raw_companies),
        "source": conference_name,
        "message": f"Scraped {len(raw_companies)} companies from {conference_name}. Use SmartFill to score and enrich.",
        "gcs_path": gcs_filename
    }

@app.post("/ingest/ranking")
async def ingest_ranking(list_name: str = Query(..., description="Name of the ranking list to ingest")):
    """Scrape ranking list → save raw to BQ. No AI. Use SmartFill per-company afterwards."""
    if list_name not in rank_scraper.get_supported_lists():
        raise HTTPException(status_code=404, detail=f"Ranking list '{list_name}' not supported.")
    raw_companies = rank_scraper.scrape_ranking(list_name)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new companies found."}
    for c in raw_companies:
        c["source"] = c.get("source", list_name)
        c["status"] = "Scraped"
        c["match_score"] = 0.0
    success = bq_handler.save_targets(raw_companies)
    if not success:
        raise HTTPException(status_code=500, detail="Database save failed.")
    gcs_filename = gcs_handler.save_companies(raw_companies, list_name.lower().replace(" ", "_"))
    return {
        "status": "Success",
        "count": len(raw_companies),
        "source": list_name,
        "message": f"Scraped {len(raw_companies)} companies from {list_name}. Use SmartFill to score and enrich.",
        "gcs_path": gcs_filename
    }

@app.post("/ingest/network")
async def ingest_network(source_name: str = Query(..., description="Network source: 'EF Alumni' or 'Tech Nation'")):
    """Scrape founder-network/alumni directory → save raw to BQ. No AI. Use SmartFill per-company afterwards."""
    if source_name not in network_scraper.get_supported_sources():
        raise HTTPException(status_code=404, detail=f"Network source '{source_name}' not supported. Options: {network_scraper.get_supported_sources()}")
    raw_companies = network_scraper.scrape_source(source_name)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": f"No companies found from {source_name}."}
    for c in raw_companies:
        c["source"] = c.get("source", source_name)
        c["status"] = "Scraped"
        c["match_score"] = 0.0
    success = bq_handler.save_targets(raw_companies)
    if not success:
        raise HTTPException(status_code=500, detail="Database save failed.")
    gcs_filename = gcs_handler.save_companies(raw_companies, source_name.lower().replace(" ", "_"))
    return {
        "status": "Success",
        "count": len(raw_companies),
        "source": source_name,
        "message": f"Scraped {len(raw_companies)} companies from {source_name}. Use SmartFill to score and enrich.",
        "gcs_path": gcs_filename,
    }


@app.post("/ingest/directory")
async def ingest_directory(source_name: str = Query("TheSaaSDirectory", description="Directory source to scrape"), max_pages: int = Query(20, description="Max pages to scrape")):
    """Scrape SaaS directory → save raw to BQ. No AI. Use SmartFill per-company afterwards."""
    if source_name not in directory_scraper.get_supported_sources():
        raise HTTPException(status_code=404, detail=f"Directory '{source_name}' not supported.")
    raw_companies = directory_scraper.scrape_source(source_name, max_pages)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": f"No companies found from {source_name}."}
    for c in raw_companies:
        c["source"] = c.get("source", source_name)
        c["status"] = "Scraped"
        c["match_score"] = 0.0
    success = bq_handler.save_targets(raw_companies)
    if not success:
        raise HTTPException(status_code=500, detail="Database save failed.")
    gcs_filename = gcs_handler.save_companies(raw_companies, source_name.lower().replace(" ", "_"))
    return {
        "status": "Success",
        "count": len(raw_companies),
        "source": source_name,
        "message": f"Scraped {len(raw_companies)} companies from {source_name}. Use SmartFill to score and enrich.",
        "gcs_path": gcs_filename
    }

@app.post("/ingest/upload")
async def upload_custom_file(file: UploadFile = File(...)):
    """Fast upload: Parse Excel -> deduplicate -> save to BigQuery. No Gemini calls."""
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Only Excel or CSV files are supported.")
    try:
        content = await file.read()
        logger.info(f"Received file for ingestion: {file.filename} ({len(content)} bytes)")
        try:
            gcs = GCSHandler()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"{timestamp}_{file.filename.replace(' ', '_')}"
            gcs.save_raw_file(content, safe_filename, file.content_type)
        except Exception as gcs_err:
            logger.warning(f"GCS Archival failed (continuing): {gcs_err}")
        is_pitchbook = "pitchbook" in file.filename.lower()
        try:
            if is_pitchbook:
                logger.info(f"PitchBook file detected: {file.filename}")
                targets = parse_pitchbook_excel(content)
            else:
                targets = parse_proprietary_excel(content)
            logger.info(f"Parsed {len(targets)} targets from {file.filename} ({'PitchBook' if is_pitchbook else 'Generic'})")
        except Exception as parse_err:
            raise HTTPException(status_code=422, detail=f"Parse failed: {str(parse_err)}")
        if not targets:
            return {"status": "Complete", "count": 0, "message": "No valid targets found."}
        source_label = f"Upload: {file.filename}"
        for t in targets:
            t["source"] = source_label
            t["status"] = "Uploaded"
        success = bq_handler.save_targets(targets)
        if not success:
            raise HTTPException(status_code=500, detail="Database save failed.")
        return {"status": "Success", "message": f"Uploaded {len(targets)} targets from {file.filename}. Use SmartFill to enrich.", "count": len(targets), "source": source_label}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── One-time data migration: revenue bands v3 (£15-40M cheque mandate) ──────
# Recomputes the stored revenue_band for EVERY row (including Qualified) under
# the new thresholds (Too Early <£5M, Target £5-40M, Too Large >£40M) and
# applies the new £40M size cap to currently-Qualified companies. Guarded by
# an activity-log marker so it runs exactly once per rule version.

BAND_RULES_VERSION = "band-rules-v3-mandate-15-40m"
_REV_EXPR = ("COALESCE(IF(revenue_y1 > 0, revenue_y1 / 1e6, NULL), "
             "IF(revenue_m > 0, revenue_m, NULL), "
             "IF(revenue_estimate_m > 0, revenue_estimate_m, NULL))")


def _migrate_band_rules():
    try:
        rows = list(bq_handler.client.query(
            f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                WHERE action_type = 'migration' AND note_text = '{BAND_RULES_VERSION}'""").result())
        if rows and int(rows[0].n) > 0:
            return  # already applied
        logger.info(f"[Migration] Applying {BAND_RULES_VERSION} to all rows...")

        # 1. Recompute revenue_band for every row from its best revenue figure
        bq_handler.client.query(f"""
            UPDATE `{bq_handler.table_id}` SET revenue_band = CASE
                WHEN {_REV_EXPR} IS NULL THEN revenue_band
                WHEN {_REV_EXPR} < 5 THEN 'Too Early'
                WHEN {_REV_EXPR} <= 40 THEN 'Target Band'
                ELSE 'Too Large' END
            WHERE TRUE""").result()

        # 2. New £40M cap: currently-Qualified companies above it become
        #    Not a Fit (test row exempt; later-stage deals left for human review)
        affected = [r.name for r in bq_handler.client.query(f"""
            SELECT name FROM `{bq_handler.table_id}`
            WHERE status = 'Qualified' AND IFNULL(source, '') != 'Internal Test'
              AND {_REV_EXPR} > 40""").result()]
        if affected:
            bq_handler.client.query(f"""
                UPDATE `{bq_handler.table_id}` SET
                    status = 'Not a Fit',
                    unfit_reason = CONCAT('Revenue £', CAST(ROUND({_REV_EXPR}, 1) AS STRING),
                                          'M exceeds the £40M cap (mandate recalibration, bands v3)'),
                    stage_entered_at = CURRENT_TIMESTAMP()
                WHERE status = 'Qualified' AND IFNULL(source, '') != 'Internal Test'
                  AND {_REV_EXPR} > 40""").result()
            for name in affected:
                bq_handler._log_activity(name, "status_change", "band-migration",
                                         old_status="Qualified", new_status="Not a Fit",
                                         note_text="Revenue above the new £40M cap (bands v3)")

        # 3. Stored criteria may carry an old size section that overrides code
        #    defaults — bring it in line with the £40M cap
        try:
            crit = bq_handler.get_criteria()
            if isinstance(crit, dict) and "size" in crit:
                from ai.criteria import SIZE_BUCKETS
                crit["size"]["buckets"] = SIZE_BUCKETS
                crit["size"]["max_revenue_m"] = 40
                crit["size"]["description"] = "Micro (<£5M), Small (£5-15M), Mid (£15-40M) qualify. Large (>£40M) rejected."
                bq_handler.save_criteria(crit, "band-migration (v3 mandate)")
                set_criteria_from_bq(crit)
        except Exception as e:
            logger.warning(f"[Migration] criteria size update skipped: {e}")

        bq_handler._log_activity("__system__", "migration", "system", note_text=BAND_RULES_VERSION)
        logger.info(f"[Migration] {BAND_RULES_VERSION} complete — bands recomputed"
                    + (f", {len(affected)} Qualified rows over £40M moved to Not a Fit" if affected else ""))
    except Exception as e:
        logger.error(f"[Migration] band rules migration failed (will retry next startup): {e}")


@app.on_event("startup")
async def _run_migrations():
    import threading
    threading.Thread(target=_migrate_band_rules, daemon=True).start()


# ── Cost protection: paid search-grounding is NEVER allowed ──────────────────
# Google gives 1,500 free grounded prompts/day. A shared weighted budget across
# ALL AI operations (SmartFill ×3 worst case, SmartEnrich ×2, InvestorFill ×1)
# is enforced before any AI run. Both knobs are env vars (change with
# --update-env-vars, never --set-env-vars):
DAILY_SMARTFILL_CAP = int(os.getenv("DAILY_SMARTFILL_CAP", "450"))      # SmartFill/Enrich runs per day

# All outreach for the Internal Test company goes to this inbox — always.
TEST_RECIPIENT = "admin@averroescapital.com"
DAILY_GROUNDING_BUDGET = int(os.getenv("DAILY_GROUNDING_BUDGET", "1400"))  # grounded calls, 100 safety buffer


def _enforce_grounding_budget(weight: int, operation: str):
    """Reject the run if it could push today's grounded calls past the free tier."""
    used = bq_handler.grounded_calls_used_today()
    if used + weight > DAILY_GROUNDING_BUDGET:
        raise HTTPException(
            status_code=429,
            detail=(f"Daily free-tier grounding budget protection: {used}/{DAILY_GROUNDING_BUDGET} "
                    f"grounded calls used today — {operation} would exceed it. "
                    f"Paid grounding is never used; resets at midnight UTC."),
        )


@app.post("/smartfill/{company_name}")
async def smartfill_company(company_name: str, bulk: bool = Query(False, description="Bulk mode: skips web-search scoring for Too Large companies (cost gate)")):
    """SmartFill: Qualify (UK/Ireland + Tech) + enrich founder/LinkedIn/website."""
    # ── Daily cost caps: run cap + shared grounding budget ──
    used_today = bq_handler.count_smartfills_today()
    if used_today >= DAILY_SMARTFILL_CAP:
        raise HTTPException(
            status_code=429,
            detail=f"Daily SmartFill limit reached ({DAILY_SMARTFILL_CAP}/day, keeps AI search calls in the free tier). Resets at midnight UTC — {used_today} used today.",
        )
    _enforce_grounding_budget(3, "SmartFill")
    logger.info(f"SmartFill triggered for: {company_name} ({used_today + 1}/{DAILY_SMARTFILL_CAP} today)")
    company_data = {"name": company_name}
    try:
        for c in bq_handler.get_universe():
            if c.get("name") == company_name:
                company_data = c
                break
    except Exception:
        pass

    # Step 1: Qualify via hard filters (Gemini if available, else keywords).
    # COST GATE: this runs FIRST, before any grounded search calls. If the
    # company fails the 3 hard filters (geography / industry / size), it is
    # marked Not a Fit with the reason stored, and ALL expensive work
    # (founder enrichment, Companies House extraction, fit scoring) is skipped.
    qual = qualify_company_with_gemini(company_data)
    new_status = qual["status"]

    # Extract size info
    size_bucket = qual.get("size_bucket", "")
    size_confidence = qual.get("size_confidence", "")
    size_reason = qual.get("size_reason", "")

    if not qual["qualified"]:
        # Cheap local revenue-band estimate (no AI spend) so the band column
        # still populates for rejected companies.
        gated_band, gated_est, gated_src, gated_conf = None, None, None, None
        try:
            est = estimate_revenue_m(dict(company_data), allow_gemini=False)
            if est:
                gated_band = compute_revenue_band(est["rev_m"])
                gated_src = est["source"]
                gated_conf = est["confidence"]
                if est["is_estimate"]:
                    gated_est = round(est["rev_m"], 2)
        except Exception as e:
            logger.warning(f"Gated revenue estimate failed for {company_name}: {e}")

        try:
            from google.cloud import bigquery as bq_lib
            gate_query = f"""UPDATE `{bq_handler.table_id}` SET
                last_smartfill_at = CURRENT_TIMESTAMP(),
                stage_entered_at = CASE WHEN IFNULL(status, '') != 'Not a Fit' THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                status = 'Not a Fit',
                unfit_reason = @reason,
                size_bucket = @size_bucket,
                revenue_band = @revenue_band,
                revenue_estimate_m = @revenue_estimate_m,
                revenue_source = @revenue_source,
                revenue_confidence = @revenue_confidence
                WHERE name = @name"""
            bq_handler.client.query(gate_query, job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("reason", "STRING", qual.get("reason", "Failed hard filters")),
                bq_lib.ScalarQueryParameter("size_bucket", "STRING", size_bucket or ""),
                bq_lib.ScalarQueryParameter("revenue_band", "STRING", gated_band or ""),
                bq_lib.ScalarQueryParameter("revenue_estimate_m", "FLOAT64", gated_est),
                bq_lib.ScalarQueryParameter("revenue_source", "STRING", gated_src or ""),
                bq_lib.ScalarQueryParameter("revenue_confidence", "STRING", gated_conf or ""),
                bq_lib.ScalarQueryParameter("name", "STRING", company_name),
            ])).result()
        except Exception as e:
            logger.error(f"SmartFill gate BQ update failed: {e}")
            raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")

        # Log as a gated run: counts toward the daily run cap but consumes
        # ZERO grounded-search budget (the qualification call is ungrounded).
        try:
            bq_handler.log_smartfill(company_name, kind="smartfill_gated")
        except Exception as e:
            logger.warning(f"Failed to log gated smartfill run: {e}")

        logger.info(f"SmartFill GATED for '{company_name}': {qual.get('reason')} — skipped enrichment/CH/scoring")
        return {
            "status": "Success",
            "company": company_name,
            "new_status": "Not a Fit",
            "gated": True,
            "is_uk_ireland": qual["is_uk_ireland"],
            "is_tech": qual["is_tech"],
            "size_bucket": size_bucket,
            "size_qualified": qual.get("size_qualified"),
            "size_confidence": size_confidence,
            "size_reason": size_reason,
            "reason": qual["reason"],
            "revenue_band": gated_band,
            "revenue_estimate_m": gated_est,
            "revenue_source": gated_src,
            "revenue_confidence": gated_conf,
        }

    # Step 2: Enrich with founder details + company description (grounded —
    # only reached when the company passed all hard filters)
    founder_info = enrichment_agent.enrich_founder_details(company_name)
    website = founder_info.get("website", "")
    description = founder_info.get("description", "")

    # Internal test row: enrichment must NEVER change its contact — the test
    # loop depends on it staying pinned to the test inbox.
    if company_data.get("source") == "Internal Test":
        founder_info["contact_email"] = TEST_RECIPIENT
        founder_info["contact_name"] = "Averroes Admin (Test)"

    # Step 3: Companies House financials (UK/Ireland only)
    ch_data = {}
    if qual.get("is_uk_ireland"):
        logger.info(f"UK/Ireland company — extracting Companies House financials for '{company_name}'")
        try:
            ch_data = extract_ch_financials(
                company_name,
                sector=company_data.get("sector", ""),
                region=company_data.get("region", ""),
                description=description or company_data.get("description", ""),
                gcs_handler=gcs_handler,
            )
            if ch_data.get("error"):
                logger.warning(f"CH extraction returned error for {company_name}: {ch_data['error']}")
                ch_data = {}  # Clear so we don't save error data
            else:
                logger.info(f"CH financials found for '{company_name}': {ch_data.get('ch_official_name')} "
                            f"(revenue_y1={ch_data.get('revenue_y1')})")
        except Exception as e:
            logger.error(f"CH extraction failed for {company_name}: {e}")

    # Step 4: Averroes Fit Scoring (only for qualified companies)
    scoring_result = {}
    if new_status == "Qualified":
        logger.info(f"Company qualified — running Averroes fit scoring for '{company_name}'...")
        # Build a merged company dict with all data for the scorer
        scoring_input = {**company_data, **ch_data}
        scoring_input["description"] = description or company_data.get("description", "")
        scoring_input["website"] = website or company_data.get("website", "")
        scoring_input["size_bucket"] = size_bucket
        try:
            scoring_result = score_company(scoring_input, skip_qualitative_if_too_large=bulk)
            logger.info(f"[SmartFill] Scoring result for '{company_name}': "
                        f"fit_score={scoring_result.get('averroes_fit_score')}, "
                        f"metrics_available={scoring_result.get('metrics_available')}, "
                        f"error={scoring_result.get('error')}")
            if scoring_result.get("error"):
                logger.warning(f"Scoring incomplete for {company_name}: {scoring_result['error']}")
        except Exception as e:
            logger.error(f"Scoring failed for {company_name}: {e}", exc_info=True)

    # Revenue band + estimate — from scoring if it ran, else estimated here from
    # local proxies (no Gemini spend for unscored/Not-a-Fit companies).
    revenue_band = scoring_result.get("revenue_band")
    revenue_estimate_m = scoring_result.get("revenue_estimate_m")
    revenue_source = scoring_result.get("revenue_source")
    revenue_confidence = scoring_result.get("revenue_confidence")
    if not revenue_band:
        est_input = {**company_data, **ch_data}
        est = estimate_revenue_m(est_input, allow_gemini=False)
        if est:
            revenue_band = compute_revenue_band(est["rev_m"])
            revenue_source = est["source"]
            revenue_confidence = est["confidence"]
            if est["is_estimate"]:
                revenue_estimate_m = round(est["rev_m"], 2)

    # Step 5: Update BQ (size + CH financials + scoring)
    try:
        from google.cloud import bigquery as bq_lib
        query = f"""UPDATE `{bq_handler.table_id}` SET
            last_smartfill_at = CURRENT_TIMESTAMP(),
            stage_entered_at = CASE WHEN IFNULL(status, '') != @status THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
            qualified_at = CASE WHEN @status = 'Qualified' THEN IFNULL(qualified_at, CURRENT_TIMESTAMP()) ELSE qualified_at END,
            status = @status,
            unfit_reason = '',
            website = @website,
            contact_name = @contact_name,
            contact_email = @contact_email,
            linkedin_url = @linkedin_url,
            size_bucket = @size_bucket,
            ch_company_number = @ch_company_number,
            ch_official_name = @ch_official_name,
            ch_status = @ch_status,
            ch_incorporated_date = @ch_incorporated_date,
            ch_sic_codes = @ch_sic_codes,
            revenue_y1 = @revenue_y1,
            revenue_y1_date = @revenue_y1_date,
            revenue_y2 = @revenue_y2,
            revenue_y2_date = @revenue_y2_date,
            revenue_y3 = @revenue_y3,
            revenue_y3_date = @revenue_y3_date,
            gross_profit_y1 = @gross_profit_y1,
            gross_profit_y2 = @gross_profit_y2,
            profit_y1 = @profit_y1,
            profit_y1_date = @profit_y1_date,
            profit_y2 = @profit_y2,
            profit_y3 = @profit_y3,
            total_assets_y1 = @total_assets_y1,
            net_assets_y1 = @net_assets_y1,
            cash_y1 = @cash_y1,
            employees_ch = @employees_ch,
            filing_type = @filing_type,
            ch_match_confidence = @ch_match_confidence,
            ch_notes = @ch_notes,
            ch_pdf_path = @ch_pdf_path,
            ch_psc_summary = @ch_psc_summary,
            ch_ownership_verified = @ch_ownership_verified,
            ch_charges_count = @ch_charges_count,
            ch_charges_summary = @ch_charges_summary,
            ch_last_share_allotment = @ch_last_share_allotment,
            ch_accounts_next_due = @ch_accounts_next_due,
            averroes_fit_score = @averroes_fit_score,
            score_employee_growth = @score_employee_growth,
            score_revenue_growth = @score_revenue_growth,
            score_revenue_size = @score_revenue_size,
            score_business_fit = @score_business_fit,
            score_market_sentiment = @score_market_sentiment,
            score_details = @score_details,
            revenue_band = @revenue_band,
            revenue_estimate_m = @revenue_estimate_m,
            revenue_source = @revenue_source,
            revenue_confidence = @revenue_confidence,
            description = CASE WHEN (@desc != '' AND LENGTH(@desc) > LENGTH(IFNULL(description, ''))) THEN @desc ELSE description END
            WHERE name = @name"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("status", "STRING", new_status),
            bq_lib.ScalarQueryParameter("website", "STRING", website),
            bq_lib.ScalarQueryParameter("contact_name", "STRING", founder_info.get("contact_name", "")),
            bq_lib.ScalarQueryParameter("contact_email", "STRING", founder_info.get("contact_email", "")),
            bq_lib.ScalarQueryParameter("linkedin_url", "STRING", founder_info.get("linkedin_url", "")),
            bq_lib.ScalarQueryParameter("size_bucket", "STRING", size_bucket or ""),
            bq_lib.ScalarQueryParameter("ch_company_number", "STRING", ch_data.get("ch_company_number") or ""),
            bq_lib.ScalarQueryParameter("ch_official_name", "STRING", ch_data.get("ch_official_name") or ""),
            bq_lib.ScalarQueryParameter("ch_status", "STRING", ch_data.get("ch_status") or ""),
            bq_lib.ScalarQueryParameter("ch_incorporated_date", "STRING", ch_data.get("ch_incorporated_date") or ""),
            bq_lib.ScalarQueryParameter("ch_sic_codes", "STRING", ch_data.get("ch_sic_codes") or ""),
            bq_lib.ScalarQueryParameter("revenue_y1", "FLOAT64", ch_data.get("revenue_y1")),
            bq_lib.ScalarQueryParameter("revenue_y1_date", "STRING", ch_data.get("revenue_y1_date") or ""),
            bq_lib.ScalarQueryParameter("revenue_y2", "FLOAT64", ch_data.get("revenue_y2")),
            bq_lib.ScalarQueryParameter("revenue_y2_date", "STRING", ch_data.get("revenue_y2_date") or ""),
            bq_lib.ScalarQueryParameter("revenue_y3", "FLOAT64", ch_data.get("revenue_y3")),
            bq_lib.ScalarQueryParameter("revenue_y3_date", "STRING", ch_data.get("revenue_y3_date") or ""),
            bq_lib.ScalarQueryParameter("gross_profit_y1", "FLOAT64", ch_data.get("gross_profit_y1")),
            bq_lib.ScalarQueryParameter("gross_profit_y2", "FLOAT64", ch_data.get("gross_profit_y2")),
            bq_lib.ScalarQueryParameter("profit_y1", "FLOAT64", ch_data.get("profit_y1")),
            bq_lib.ScalarQueryParameter("profit_y1_date", "STRING", ch_data.get("profit_y1_date") or ""),
            bq_lib.ScalarQueryParameter("profit_y2", "FLOAT64", ch_data.get("profit_y2")),
            bq_lib.ScalarQueryParameter("profit_y3", "FLOAT64", ch_data.get("profit_y3")),
            bq_lib.ScalarQueryParameter("total_assets_y1", "FLOAT64", ch_data.get("total_assets_y1")),
            bq_lib.ScalarQueryParameter("net_assets_y1", "FLOAT64", ch_data.get("net_assets_y1")),
            bq_lib.ScalarQueryParameter("cash_y1", "FLOAT64", ch_data.get("cash_y1")),
            bq_lib.ScalarQueryParameter("employees_ch", "INT64", ch_data.get("employees_ch")),
            bq_lib.ScalarQueryParameter("filing_type", "STRING", ch_data.get("filing_type") or ""),
            bq_lib.ScalarQueryParameter("ch_match_confidence", "STRING", ch_data.get("ch_match_confidence") or ""),
            bq_lib.ScalarQueryParameter("ch_notes", "STRING", ch_data.get("notes") or ""),
            bq_lib.ScalarQueryParameter("ch_pdf_path", "STRING", ch_data.get("ch_pdf_path") or ""),
            bq_lib.ScalarQueryParameter("ch_psc_summary", "STRING", ch_data.get("ch_psc_summary") or ""),
            bq_lib.ScalarQueryParameter("ch_ownership_verified", "STRING", ch_data.get("ch_ownership_verified") or ""),
            bq_lib.ScalarQueryParameter("ch_charges_count", "INT64", ch_data.get("ch_charges_count")),
            bq_lib.ScalarQueryParameter("ch_charges_summary", "STRING", ch_data.get("ch_charges_summary") or ""),
            bq_lib.ScalarQueryParameter("ch_last_share_allotment", "STRING", ch_data.get("ch_last_share_allotment") or ""),
            bq_lib.ScalarQueryParameter("ch_accounts_next_due", "STRING", ch_data.get("ch_accounts_next_due") or ""),
            bq_lib.ScalarQueryParameter("averroes_fit_score", "FLOAT64", scoring_result.get("averroes_fit_score")),
            bq_lib.ScalarQueryParameter("score_employee_growth", "FLOAT64", scoring_result.get("score_employee_growth")),
            bq_lib.ScalarQueryParameter("score_revenue_growth", "FLOAT64", scoring_result.get("score_revenue_growth")),
            bq_lib.ScalarQueryParameter("score_revenue_size", "FLOAT64", scoring_result.get("score_revenue_size")),
            bq_lib.ScalarQueryParameter("score_business_fit", "FLOAT64", scoring_result.get("score_business_fit")),
            bq_lib.ScalarQueryParameter("score_market_sentiment", "FLOAT64", scoring_result.get("score_market_sentiment")),
            bq_lib.ScalarQueryParameter("score_details", "STRING", scoring_result.get("score_details") or ""),
            bq_lib.ScalarQueryParameter("revenue_band", "STRING", revenue_band or ""),
            bq_lib.ScalarQueryParameter("revenue_estimate_m", "FLOAT64", revenue_estimate_m),
            bq_lib.ScalarQueryParameter("revenue_source", "STRING", revenue_source or ""),
            bq_lib.ScalarQueryParameter("revenue_confidence", "STRING", revenue_confidence or ""),
            bq_lib.ScalarQueryParameter("desc", "STRING", description),
            bq_lib.ScalarQueryParameter("name", "STRING", company_name),
        ])
        bq_handler.client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.error(f"SmartFill BQ update failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")

    # Count this run against the daily cap (best-effort)
    try:
        bq_handler.log_smartfill(company_name)
    except Exception as e:
        logger.warning(f"Failed to log smartfill run: {e}")

    # Audit trail: where the contact email came from (verified-only policy)
    if founder_info.get("contact_email"):
        try:
            src = founder_info.get("email_source") or "source not stated by the model"
            bq_handler.add_activity_note(
                company_name,
                f"SmartFill contact email: {founder_info['contact_email']} (found at: {src})",
                "smartfill")
        except Exception:
            pass

    return {
        "status": "Success",
        "company": company_name,
        "new_status": new_status,
        "is_uk_ireland": qual["is_uk_ireland"],
        "is_tech": qual["is_tech"],
        "size_bucket": size_bucket,
        "size_qualified": qual.get("size_qualified"),
        "size_confidence": size_confidence,
        "size_reason": size_reason,
        "reason": qual["reason"],
        "website": website,
        "contact_name": founder_info.get("contact_name", ""),
        "contact_email": founder_info.get("contact_email", ""),
        "linkedin_url": founder_info.get("linkedin_url", ""),
        "description": description,
        # Companies House data
        "ch_company_number": ch_data.get("ch_company_number"),
        "ch_official_name": ch_data.get("ch_official_name"),
        "ch_status": ch_data.get("ch_status"),
        "ch_incorporated_date": ch_data.get("ch_incorporated_date"),
        "ch_sic_codes": ch_data.get("ch_sic_codes"),
        "revenue_y1": ch_data.get("revenue_y1"),
        "revenue_y1_date": ch_data.get("revenue_y1_date"),
        "revenue_y2": ch_data.get("revenue_y2"),
        "revenue_y2_date": ch_data.get("revenue_y2_date"),
        "revenue_y3": ch_data.get("revenue_y3"),
        "revenue_y3_date": ch_data.get("revenue_y3_date"),
        "gross_profit_y1": ch_data.get("gross_profit_y1"),
        "gross_profit_y2": ch_data.get("gross_profit_y2"),
        "profit_y1": ch_data.get("profit_y1"),
        "profit_y2": ch_data.get("profit_y2"),
        "profit_y3": ch_data.get("profit_y3"),
        "total_assets_y1": ch_data.get("total_assets_y1"),
        "net_assets_y1": ch_data.get("net_assets_y1"),
        "cash_y1": ch_data.get("cash_y1"),
        "employees_ch": ch_data.get("employees_ch"),
        "filing_type": ch_data.get("filing_type"),
        "ch_match_confidence": ch_data.get("ch_match_confidence"),
        "ch_notes": ch_data.get("notes"),
        "ch_pdf_path": ch_data.get("ch_pdf_path"),
        "ch_psc_summary": ch_data.get("ch_psc_summary"),
        "ch_ownership_verified": ch_data.get("ch_ownership_verified"),
        "ch_charges_count": ch_data.get("ch_charges_count"),
        "ch_charges_summary": ch_data.get("ch_charges_summary"),
        "ch_last_share_allotment": ch_data.get("ch_last_share_allotment"),
        "ch_accounts_next_due": ch_data.get("ch_accounts_next_due"),
        # Averroes Fit Scoring
        "averroes_fit_score": scoring_result.get("averroes_fit_score"),
        "score_employee_growth": scoring_result.get("score_employee_growth"),
        "score_revenue_growth": scoring_result.get("score_revenue_growth"),
        "score_revenue_size": scoring_result.get("score_revenue_size"),
        "score_business_fit": scoring_result.get("score_business_fit"),
        "score_market_sentiment": scoring_result.get("score_market_sentiment"),
        "score_details": scoring_result.get("score_details"),
        "revenue_band": revenue_band,
        "revenue_estimate_m": revenue_estimate_m,
        "revenue_source": revenue_source,
        "revenue_confidence": revenue_confidence,
        "metrics_available": scoring_result.get("metrics_available"),
    }


@app.get("/smartfill/eligible")
async def smartfill_eligible():
    """
    Pre-flight for bulk SmartFill. ZERO AI calls. Cost-optimised rules:
      - only companies NEVER SmartFilled before (last_smartfill_at is null)
      - must pass ALL THREE hard filters on stored data (geography + industry
        + size where determinable)
      - respects the daily cap: reports remaining quota and trims the list to it
    """
    universe = bq_handler.get_universe()
    total = len(universe)

    non_uk_ie = 0
    non_tech = 0
    too_large = 0
    already_filled = 0
    eligible = []

    for c in universe:
        if c.get("last_smartfill_at"):
            already_filled += 1
            continue
        qual = qualify_company(c)  # keyword + rule-based size, no AI
        if not qual["is_uk_ireland"]:
            non_uk_ie += 1
            continue
        if not qual["is_tech"]:
            non_tech += 1
            continue
        if qual.get("size_qualified") is False:
            too_large += 1
            continue
        eligible.append(c.get("name"))

    used_today = bq_handler.count_smartfills_today()
    remaining_today = max(0, DAILY_SMARTFILL_CAP - used_today)
    n = len(eligible)
    # Bulk runs process at most 25 companies per press — keeps runs short
    # (~15 min), reviewable, and safely within one session.
    BULK_BATCH_LIMIT = 25
    runnable = eligible[:min(BULK_BATCH_LIMIT, remaining_today)]

    est_n = len(runnable)
    est = {
        "gemini_calls_per_company": {"min": 3, "typical": 5, "max": 7},
        "grounded_calls_per_company": {"min": 2, "typical": 3},
        "total_gemini_calls": {"min": est_n * 3, "typical": est_n * 5, "max": est_n * 7},
        "total_grounded_calls_typical": est_n * 3,
        "token_cost_usd_typical": round(est_n * 0.015, 2),
        "grounding_note": f"Daily cap of {DAILY_SMARTFILL_CAP} keeps all runs inside the free search-grounding tier — bulk runs cost tokens only (~1p/company).",
    }

    return {
        "total_universe": total,
        "excluded_non_uk_ie": non_uk_ie,
        "excluded_non_tech": non_tech,
        "excluded_too_large": too_large,
        "skipped_already_smartfilled": already_filled,
        "eligible_count": n,
        "daily_cap": DAILY_SMARTFILL_CAP,
        "used_today": used_today,
        "remaining_today": remaining_today,
        "batch_limit": BULK_BATCH_LIMIT,
        "runnable_now": len(runnable),
        "eligible_names": runnable,
        "estimate": est,
    }


@app.post("/smartenrich/{company_name}")
async def smartenrich_company(company_name: str):
    """
    SmartEnrich: the CHEAP refresh for already-SmartFilled companies.
      - contacts: ALWAYS source-checks the stored email (1 grounded call) —
        confirms, replaces with a sourced address, or clears an unsourceable
        (likely AI-guessed) one; gaps in name/LinkedIn/website filled, never
        overwritten
      - CH registry intel always refreshed (free API calls)
      - CH PDFs re-parsed ONLY if a newer accounts filing exists
      - re-scores only if Qualified and (previously unscored or new financials)
    Typically 1-2 Gemini calls vs ~5 for a full SmartFill.
    """
    used_today = bq_handler.count_smartfills_today()
    if used_today >= DAILY_SMARTFILL_CAP:
        raise HTTPException(status_code=429, detail=f"Daily SmartFill limit reached ({DAILY_SMARTFILL_CAP}/day). Resets at midnight UTC.")
    _enforce_grounding_budget(2, "SmartEnrich")

    company = None
    for c in bq_handler.get_universe():
        if c.get("name") == company_name:
            company = c
            break
    if not company:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found")

    from google.cloud import bigquery as bq_lib
    set_clauses = ["last_smartfill_at = CURRENT_TIMESTAMP()"]
    params = [bq_lib.ScalarQueryParameter("name", "STRING", company_name)]
    actions = []

    # ── 1. Contacts: ALWAYS double-check the email against real sources ──
    # The stored address may predate the verified-only policy (i.e. it could be
    # an AI pattern guess). Every SmartEnrich re-runs sourced enrichment and:
    #   confirms the email if a source shows it, replaces it if a source shows
    #   a different one, or CLEARS it if no source anywhere publishes one.
    # The Internal Test row is exempt (contact pinned to the test inbox).
    if company.get("source") == "Internal Test":
        actions.append("test row: contact pinned, verification skipped")
    else:
        founder_info = enrichment_agent.enrich_founder_details(company_name)
        found_email = (founder_info.get("contact_email") or "").strip()
        email_src = founder_info.get("email_source") or "source not stated by the model"
        stored_email = (company.get("contact_email") or "").strip()

        # Fill gaps in name / LinkedIn / website (never overwrite existing)
        for col, key in [("contact_name", "contact_name"), ("linkedin_url", "linkedin_url"), ("website", "website")]:
            val = founder_info.get(key)
            if val:
                set_clauses.append(f"{col} = CASE WHEN IFNULL({col}, '') = '' THEN @{col} ELSE {col} END")
                params.append(bq_lib.ScalarQueryParameter(col, "STRING", val))

        note = ""
        if found_email and found_email.lower() != stored_email.lower():
            set_clauses.append("contact_email = @contact_email")
            params.append(bq_lib.ScalarQueryParameter("contact_email", "STRING", found_email))
            actions.append(f"email updated to sourced address ({email_src})")
            note = f"SmartEnrich replaced email '{stored_email or '(empty)'}' with sourced address '{found_email}' (found at: {email_src})"
        elif found_email:
            actions.append(f"email confirmed against source ({email_src})")
            note = f"SmartEnrich confirmed email '{found_email}' (found at: {email_src})"
        elif stored_email:
            set_clauses.append("contact_email = ''")
            actions.append("stored email has no source anywhere: cleared as a likely AI guess")
            note = f"SmartEnrich cleared email '{stored_email}': no published source found anywhere, likely a generated guess"
        else:
            actions.append("no published email found")
        if note:
            try:
                bq_handler.add_activity_note(company_name, note, "smartenrich")
            except Exception:
                pass

    new_financials = False
    if company.get("ch_company_number"):
        number = company["ch_company_number"]
        # ── 2. Registry intel: always refresh (free) ──
        from services.companies_house_service import (
            get_psc_summary, get_charges_summary, get_capital_events, _get_company_profile, _get_accounts_filings,
        )
        psc = get_psc_summary(number)
        charges = get_charges_summary(number)
        capital = get_capital_events(number)
        profile = _get_company_profile(number) or {}
        next_due = (profile.get("accounts", {}) or {}).get("next_due") or ""
        for col, val, typ in [
            ("ch_psc_summary", psc["psc_summary"], "STRING"),
            ("ch_ownership_verified", psc["ownership_verified"], "STRING"),
            ("ch_charges_count", charges["charges_count"], "INT64"),
            ("ch_charges_summary", charges["charges_summary"], "STRING"),
            ("ch_last_share_allotment", capital["last_share_allotment"], "STRING"),
            ("ch_accounts_next_due", next_due, "STRING"),
        ]:
            set_clauses.append(f"{col} = @{col}")
            params.append(bq_lib.ScalarQueryParameter(col, typ, val))
        actions.append("registry intel refreshed")

        # ── 3. Financials: re-parse ONLY if a newer filing exists ──
        filings = _get_accounts_filings(number, max_items=1)
        latest_filing_date = filings[0].get("date", "") if filings else ""
        known_date = company.get("revenue_y1_date") or ""
        if latest_filing_date and latest_filing_date > known_date:
            ch_data = extract_ch_financials(company_name, sector=company.get("sector", ""),
                                            region=company.get("region", ""),
                                            description=company.get("description", ""),
                                            gcs_handler=gcs_handler)
            if not ch_data.get("error"):
                new_financials = True
                for col in ["revenue_y1", "revenue_y2", "revenue_y3", "gross_profit_y1", "gross_profit_y2",
                            "profit_y1", "profit_y2", "profit_y3", "total_assets_y1", "net_assets_y1", "cash_y1"]:
                    if ch_data.get(col) is not None:
                        set_clauses.append(f"{col} = @{col}")
                        params.append(bq_lib.ScalarQueryParameter(col, "FLOAT64", ch_data.get(col)))
                for col in ["revenue_y1_date", "revenue_y2_date", "revenue_y3_date", "filing_type", "ch_pdf_path"]:
                    if ch_data.get(col):
                        set_clauses.append(f"{col} = @{col}")
                        params.append(bq_lib.ScalarQueryParameter(col, "STRING", str(ch_data.get(col))))
                actions.append(f"new accounts parsed (filed {latest_filing_date})")
        if not new_financials:
            actions.append("no new filing — PDF parse skipped")

    # ── 4. Re-score: only if Qualified and (unscored or fresh financials) ──
    if company.get("status") == "Qualified" and (company.get("averroes_fit_score") is None or new_financials):
        scoring_input = dict(company)
        scoring_result = score_company(scoring_input)
        for col in ["averroes_fit_score", "score_employee_growth", "score_revenue_growth",
                    "score_revenue_size", "score_business_fit", "score_market_sentiment", "revenue_estimate_m"]:
            set_clauses.append(f"{col} = @{col}")
            params.append(bq_lib.ScalarQueryParameter(col, "FLOAT64", scoring_result.get(col)))
        for col in ["score_details", "revenue_band", "revenue_source", "revenue_confidence"]:
            set_clauses.append(f"{col} = @{col}")
            params.append(bq_lib.ScalarQueryParameter(col, "STRING", scoring_result.get(col) or ""))
        actions.append("re-scored")
    else:
        actions.append("score kept")

    query = f"UPDATE `{bq_handler.table_id}` SET {', '.join(set_clauses)} WHERE name = @name"
    try:
        bq_handler.client.query(query, job_config=bq_lib.QueryJobConfig(query_parameters=params)).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update failed: {e}")

    bq_handler.log_smartfill(company_name, kind="smartenrich")
    return {"status": "Success", "company": company_name, "actions": actions}


@app.post("/smartfill-refresh-due")
async def smartfill_refresh_due(limit: int = Query(5, description="Max companies to refresh per invocation")):
    """
    Auto-refresh: re-SmartFill companies whose CH accounts-due date has passed
    since their last fill (fresh financials just landed). Designed to be hit by
    Cloud Scheduler; processes a few per call to stay inside request timeouts,
    and always respects the daily cap.
    """
    used_today = bq_handler.count_smartfills_today()
    remaining = max(0, DAILY_SMARTFILL_CAP - used_today)
    if remaining == 0:
        return {"status": "Skipped", "reason": "daily cap reached", "refreshed": []}

    from datetime import date
    today = date.today().isoformat()
    due = []
    for c in bq_handler.get_universe():
        next_due = c.get("ch_accounts_next_due") or ""
        last_fill = c.get("last_smartfill_at") or ""
        if next_due and next_due <= today and (not last_fill or str(last_fill)[:10] < next_due):
            due.append(c.get("name"))

    to_run = due[:min(limit, remaining)]
    results = []
    for name in to_run:
        try:
            await smartenrich_company(name)
            results.append({"company": name, "status": "refreshed"})
        except HTTPException as e:
            results.append({"company": name, "status": f"error: {e.detail}"})
            if e.status_code == 429:
                break
    return {"status": "Success", "due_total": len(due), "refreshed": results}


@app.get("/ch-pdf/{company_name}")
async def get_ch_pdf(company_name: str):
    """Serve the Companies House filing PDF from GCS for a given company."""
    from fastapi.responses import Response
    # Look up the company's ch_pdf_path from BQ
    try:
        universe = bq_handler.get_universe()
        company = None
        for c in universe:
            if c.get("name") == company_name:
                company = c
                break
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        pdf_path = company.get("ch_pdf_path")
        if not pdf_path:
            raise HTTPException(status_code=404, detail="No CH filing PDF available for this company")

        # Download from GCS
        if not gcs_handler.storage_client:
            raise HTTPException(status_code=500, detail="GCS not available")

        bucket = gcs_handler.storage_client.bucket(gcs_handler.bucket_name)
        blob = bucket.blob(pdf_path)
        if not blob.exists():
            raise HTTPException(status_code=404, detail="PDF file not found in storage")

        pdf_bytes = blob.download_as_bytes()
        safe_name = company_name.replace(" ", "_")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{safe_name}_CH_Filing.pdf"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve CH PDF for {company_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/enrich/{company_name}")
async def manual_enrich(company_name: str):
    """
    Manually triggers enrichment for a specific company in the pipeline.
    """
    details = enrichment_agent.enrich_founder_details(company_name)
    
    # Fool-proof tagging
    if not details['contact_name'] and not details['contact_email']:
        details['contact_name'] = "[Manual Research Required]"
        details['contact_email'] = "research@averroescapital.com"
        details['linkedin_url'] = "N/A"

    success = bq_handler.update_company_enrichment(company_name, details)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update database.")
    
    return {"status": "Success", "details": details}

class OutreachSendRequest(BaseModel):
    to: str
    subject: str
    body: str
    company_name: Optional[str] = None


def _stored_news_signal(company_data: dict) -> str:
    """
    Reuse what scoring already found: the market-sentiment (and employee-growth)
    evidence stored in score_details contains press/award/hiring specifics.
    Zero cost — this is the primary news source for outreach hooks.
    """
    import json as _json
    raw = company_data.get("score_details")
    if not raw:
        return ""
    try:
        details = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    parts = []
    for key in ("market_sentiment", "employee_growth"):
        metric = details.get(key) or {}
        val = (metric.get("value") or "").strip()
        # Only pass specifics, not generic assessments
        if val and len(val) > 15 and not val.lower().startswith(("no ", "none", "n/a", "minimal", "little")):
            parts.append(val)
    return "; ".join(parts[:2])


@app.post("/outreach/draft/{company_name}")
async def draft_outreach(company_name: str):
    """
    Personalised outreach draft. News hook priority:
      1. Signals already captured by scoring (score_details) — free
      2. One grounded news search — only if nothing stored, budget-enforced
    """
    logger.info(f"Outreach draft requested for: {company_name}")
    company_data = {"name": company_name}
    try:
        for c in bq_handler.get_universe():
            if c.get("name") == company_name:
                company_data = c
                break
    except Exception:
        pass

    is_test_company = company_data.get("source") == "Internal Test"

    news_hook = _stored_news_signal(company_data)
    hook_source = "scoring intelligence" if news_hook else ""
    if not news_hook:
        try:
            _enforce_grounding_budget(1, "Outreach news lookup")
            from services.outreach_service import find_news_hook
            news_hook = find_news_hook(company_name, company_data.get("website", ""))
            if news_hook:
                hook_source = "fresh web search"
                bq_handler.log_smartfill(company_name, kind="newslookup")
        except HTTPException:
            logger.info("News lookup skipped — grounding budget reached; drafting without a hook")

    result = draft_outreach_email(company_data, news_hook=news_hook)
    result["news_hook"] = news_hook
    result["news_hook_source"] = hook_source

    # Internal test company: recipient is ALWAYS the test inbox
    if is_test_company:
        result["to"] = TEST_RECIPIENT

    # Persist the draft so the UI can offer Review & Send without regenerating
    try:
        from google.cloud import bigquery as bq_lib
        q = f"""UPDATE `{bq_handler.table_id}` SET
                outreach_draft_subject = @s, outreach_draft_body = @b,
                outreach_draft_to = @t, outreach_drafted_at = CURRENT_TIMESTAMP()
                WHERE name = @name"""
        bq_handler.client.query(q, job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("s", "STRING", result.get("subject") or ""),
            bq_lib.ScalarQueryParameter("b", "STRING", result.get("body") or ""),
            bq_lib.ScalarQueryParameter("t", "STRING", result.get("to") or ""),
            bq_lib.ScalarQueryParameter("name", "STRING", company_name),
        ])).result()
    except Exception as e:
        logger.warning(f"Failed to persist outreach draft for {company_name}: {e}")

    # Activity log: the draft event, with its metadata
    try:
        bq_handler._log_activity(
            company_name, "note", "system",
            note_text=f"Outreach draft generated — to: {result.get('to') or 'no email on file'}, subject: \"{result.get('subject', '')}\"" +
                      (f", news hook: {hook_source}" if hook_source else ""))
    except Exception as e:
        logger.warning(f"Failed to log draft activity: {e}")

    return result


@app.post("/outreach/send")
async def send_outreach(req: OutreachSendRequest):
    """Send an outreach email via Gmail SMTP."""
    # Internal test company: force the recipient to the test inbox, even if
    # the To field was edited — a test email must never reach a real founder.
    to_addr = req.to
    if req.company_name:
        try:
            for c in bq_handler.get_universe():
                if c.get("name") == req.company_name:
                    if c.get("source") == "Internal Test" and to_addr != TEST_RECIPIENT:
                        logger.info(f"Test company send: recipient '{to_addr}' overridden to {TEST_RECIPIENT}")
                        to_addr = TEST_RECIPIENT
                    break
        except Exception:
            pass
    req.to = to_addr

    logger.info(f"Sending outreach to: {req.to} (company: {req.company_name})")
    result = send_email(req.to, req.subject, req.body)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["detail"])
    # Log the sent email in BQ (best-effort)
    try:
        from google.cloud import bigquery as bq_lib
        # The Internal Test row is exempt from the Not-a-Fit guard: sending a
        # test email always moves it to Engaged so the full loop can be tested
        # from any starting state.
        query = f"""UPDATE `{bq_handler.table_id}`
                    SET stage_entered_at = CASE WHEN IFNULL(status, '') != 'Engaged' THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                        contacted_at = IFNULL(contacted_at, CURRENT_TIMESTAMP()),
                        outreach_sent_at = CURRENT_TIMESTAMP(),
                        status = 'Engaged'
                    WHERE name = @name AND (status != 'Not a Fit' OR source = 'Internal Test')"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("name", "STRING", req.company_name or ""),
        ])
        bq_handler.client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.warning(f"Failed to update status after outreach: {e}")

    # Activity log: the send event, with its metadata
    if req.company_name:
        try:
            bq_handler._log_activity(
                req.company_name, "outreach_sent", "system",
                note_text=f"Outreach email sent to {req.to} — subject: \"{req.subject}\"")
        except Exception as e:
            logger.warning(f"Failed to log send activity: {e}")
    return result


# ── Temporary diagnostic: Internal Test row state (no secrets exposed) ──────

@app.get("/diag/test-loop")
async def diag_test_loop():
    """State of the Internal Test company + its email log. Unauthenticated but
    exposes ONLY the internal test row — remove once the loop is verified."""
    out = {"build_marker": "selfheal-2026-07-10-a"}
    row = None
    try:
        for c in bq_handler.get_universe():
            if c.get("source") == "Internal Test":
                row = c
                break
    except Exception as e:
        out["universe_error"] = str(e)
    if not row:
        out["test_row"] = None
        return out
    out["test_row"] = {k: (str(row.get(k)) if row.get(k) is not None else None) for k in (
        "name", "status", "source", "contact_email", "outreach_draft_to",
        "outreach_drafted_at", "outreach_sent_at", "last_reply_at",
        "reply_classification", "stage_entered_at", "qualified_at", "contacted_at")}
    try:
        from google.cloud import bigquery as bq_lib
        table_id = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"
        q = f"""SELECT direction, subject, counterparty_email, entity_name, classification,
                       CAST(sent_at AS STRING) AS sent_at
                FROM `{table_id}`
                WHERE entity_name = @name OR LOWER(counterparty_email) = @em
                ORDER BY sent_at DESC LIMIT 10"""
        rows = bq_handler.client.query(q, job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("name", "STRING", row.get("name") or ""),
            bq_lib.ScalarQueryParameter("em", "STRING", (row.get("contact_email") or "").lower()),
        ])).result()
        out["email_log"] = [dict(r) for r in rows]
    except Exception as e:
        out["email_log_error"] = str(e)
    return out


# ── Deal Intelligence Chat ───────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict]] = []
    web_search: Optional[bool] = False


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Chat over the database (companies + LPs). Data-only answers; never guesses.
    web_search=True (user explicitly pressed the button) runs ONE grounded
    Gemini search, enforced against the shared daily grounding budget.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message.")
    from services.chat_service import chat_answer, chat_web_search
    try:
        universe = bq_handler.get_universe()
    except Exception:
        universe = []
    try:
        investors = investor_handler.get_all()
    except Exception:
        investors = []

    if req.web_search:
        _enforce_grounding_budget(1, "Chat web search")
        result = chat_web_search(req.message, req.history or [], universe, investors)
        try:
            bq_handler.log_smartfill("chat", kind="newslookup")  # weight-1 grounded call
        except Exception:
            pass
        return result

    return chat_answer(req.message, req.history or [], universe, investors)


# ── Deal Lifecycle Endpoints ─────────────────────────────────────────────────

class StatusUpdateRequest(BaseModel):
    status: str
    created_by: Optional[str] = "Ishu Ratna"

class NoteRequest(BaseModel):
    note: str
    created_by: Optional[str] = "Ishu Ratna"


@app.put("/company/{company_name}/status")
async def update_company_status(company_name: str, req: StatusUpdateRequest):
    """Update a company's deal stage and log the change."""
    valid_stages = bq_handler.DEAL_STAGES
    if req.status not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_stages}")

    # Internal test row never exits the pipeline — it resets for the next cycle
    if req.status in ("Lost", "Not a Fit"):
        try:
            if _reset_test_company(company_name):
                bq_handler.add_activity_note(
                    company_name,
                    f"Internal test company: '{req.status}' intercepted — reset to a fresh Qualified state (outreach, replies and stage history cleared) for the next test cycle.",
                    req.created_by)
                return {"status": "Success", "company": company_name, "new_status": "Qualified", "test_reset": True}
        except Exception as e:
            logger.warning(f"Test-company reset check failed for {company_name}: {e}")

    success = bq_handler.update_company_status(company_name, req.status, req.created_by)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update status.")
    return {"status": "Success", "company": company_name, "new_status": req.status}


class RemoveRequest(BaseModel):
    created_by: Optional[str] = "Ishu Ratna"


# ── Internal test company: never leaves the pipeline ─────────────────────────
# The row with source='Internal Test' exists purely for end-to-end testing.
# Any action that would drop it out of the pipeline (Remove, Mark Lost,
# Not a Fit) instead resets it to a FRESH Qualified state: outreach draft,
# sent/reply stamps and per-stage timestamps are wiped so the next test
# cycle starts completely clean. Applies ONLY to Internal Test rows.

def _reset_test_company(company_name: str) -> bool:
    """Reset the internal test row to a clean Qualified state.
    Returns True if a reset happened (i.e. the row is the test company)."""
    from google.cloud import bigquery as bq_lib
    query = f"""UPDATE `{bq_handler.table_id}` SET
        status = 'Qualified',
        contact_email = '{TEST_RECIPIENT}',
        stage_entered_at = CURRENT_TIMESTAMP(),
        qualified_at = CURRENT_TIMESTAMP(),
        contacted_at = NULL, meeting_at = NULL, dd_at = NULL,
        offer_at = NULL, won_at = NULL, lost_at = NULL,
        outreach_draft_subject = NULL, outreach_draft_body = NULL,
        outreach_draft_to = NULL, outreach_drafted_at = NULL,
        outreach_sent_at = NULL,
        last_reply_at = NULL, reply_classification = NULL,
        unfit_reason = NULL
        WHERE name = @name AND source = 'Internal Test'"""
    job = bq_handler.client.query(query, job_config=bq_lib.QueryJobConfig(query_parameters=[
        bq_lib.ScalarQueryParameter("name", "STRING", company_name),
    ]))
    job.result()
    return bool(job.num_dml_affected_rows)


@app.post("/company/{company_name}/remove")
async def remove_from_pipeline(company_name: str, req: RemoveRequest):
    """Remove a company from the pipeline — sets status to 'Not a Fit' and score to 0."""
    logger.info(f"Removing '{company_name}' from pipeline by {req.created_by}")

    # Internal test row never exits the pipeline — it resets for the next cycle
    try:
        if _reset_test_company(company_name):
            bq_handler.add_activity_note(
                company_name,
                "Internal test company: removal intercepted — reset to a fresh Qualified state (outreach, replies and stage history cleared) for the next test cycle.",
                req.created_by)
            return {"status": "Success", "company": company_name, "new_status": "Qualified", "test_reset": True}
    except Exception as e:
        logger.warning(f"Test-company reset check failed for {company_name}: {e}")
    try:
        from google.cloud import bigquery as bq_lib
        query = f"""UPDATE `{bq_handler.table_id}` SET
                    stage_entered_at = CASE WHEN IFNULL(status, '') != 'Not a Fit' THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                    status = 'Not a Fit', match_score = 0.0,
                    unfit_reason = @reason WHERE name = @name"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("reason", "STRING", f"Manually removed from pipeline by {req.created_by}"),
            bq_lib.ScalarQueryParameter("name", "STRING", company_name),
        ])
        bq_handler.client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.error(f"Remove failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove: {str(e)}")

    # Log the removal as activity
    try:
        bq_handler.add_activity_note(company_name, f"Removed from pipeline by {req.created_by}", req.created_by)
    except Exception:
        pass

    return {"status": "Success", "company": company_name, "new_status": "Not a Fit"}


@app.post("/company/{company_name}/notes")
async def add_company_note(company_name: str, req: NoteRequest):
    """Add a note to a company's activity log."""
    if not req.note.strip():
        raise HTTPException(status_code=400, detail="Note cannot be empty.")

    success = bq_handler.add_activity_note(company_name, req.note, req.created_by)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save note.")
    return {"status": "Success", "company": company_name, "note": req.note}


@app.get("/company/{company_name}/activity")
async def get_company_activity(company_name: str, limit: int = Query(50, description="Max entries to return")):
    """Get the full activity timeline for a company."""
    activity = bq_handler.get_activity_log(company_name, limit)
    return {"company": company_name, "activity": activity, "count": len(activity)}


# ── Qualification Criteria Endpoints ───────────────────────────────────────────

class CriteriaChatRequest(BaseModel):
    message: str

class CriteriaApplyRequest(BaseModel):
    criteria: dict
    updated_by: Optional[str] = "Ishu Ratna"
    requalify: Optional[bool] = True


# ── Email communications log ─────────────────────────────────────────────────

@app.post("/email/sync")
async def sync_emails(days: int = Query(30, description="How many days back to scan")):
    """
    Sync Beatrice's Gmail (IMAP, same App Password as sending) against known
    contacts in companies + LPs. Logs exchanges, classifies replies with AI,
    stamps last_reply_at, and auto-advances Engaged → Contacted on reply.
    """
    from services.email_sync_service import sync_mailbox, classify_reply
    from google.cloud import bigquery as bq_lib

    # Known contacts: email → entity. A company is reachable via its contact
    # email AND any address we actually drafted/sent outreach to — if we
    # emailed an address, a reply from it must match the company even when
    # SmartFill later changed the contact on file.
    known = {}
    for c in bq_handler.get_universe():
        entry = {"type": "company", "name": c.get("name"), "status": c.get("status"),
                 "is_test": c.get("source") == "Internal Test",
                 "stored_cls": c.get("reply_classification") or ""}
        for em in {(c.get("contact_email") or "").strip().lower(),
                   (c.get("outreach_draft_to") or "").strip().lower()}:
            if em:
                known[em] = entry
    for inv in investor_handler.get_all():
        em = (inv.get("contact_email") or "").strip().lower()
        if em:
            known[em] = {"type": "investor", "name": inv.get("name"), "status": inv.get("status")}
    if not known:
        return {"status": "Complete", "message": "No known contact emails in the database yet."}

    try:
        entries = sync_mailbox(known, days=days)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mailbox sync failed: {e}")

    # Dedup against already-logged messages
    seen = bq_handler.get_logged_message_ids()
    new_entries = [e for e in entries if e.get("message_id") not in seen]

    replies = [e for e in new_entries if e["direction"] == "received"]
    advanced, classified = [], 0

    for r in replies[:25]:  # classification bounded per sync run
        result = classify_reply(r["subject"], r["snippet"], r["entity_name"])
        if result:
            r["classification"] = result.get("classification", "")
            r["summary"] = result.get("summary", "")
            classified += 1

    # Second chance: already-logged replies whose company still shows no
    # classification (empty snippet at the time, or the AI call failed).
    # Re-attempt within the same per-run budget of 25 classification calls.
    reclassified = []
    budget = 25 - len(replies[:25])
    if budget > 0:
        seen_received = [e for e in entries
                         if e["direction"] == "received" and e.get("message_id") in seen
                         and e["entity_type"] == "company"]
        for r in sorted(seen_received, key=lambda x: x.get("sent_at") or "", reverse=True):
            if budget <= 0:
                break
            sender = known.get(r["counterparty_email"], {})
            if sender.get("stored_cls", "") not in ("", "unclassified"):
                continue
            budget -= 1
            result = classify_reply(r["subject"], r["snippet"], r["entity_name"])
            if result and result.get("classification"):
                cls2 = result["classification"]
                try:
                    bq_handler.client.query(
                        f"""UPDATE `{bq_handler.table_id}` SET reply_classification = @cls WHERE name = @name""",
                        job_config=bq_lib.QueryJobConfig(query_parameters=[
                            bq_lib.ScalarQueryParameter("cls", "STRING", cls2),
                            bq_lib.ScalarQueryParameter("name", "STRING", r["entity_name"]),
                        ])).result()
                    bq_handler._log_activity(
                        r["entity_name"], "note", "email-sync",
                        note_text=f"Reply reclassified: \"{r['subject']}\" ({cls2})" + (f" — {result.get('summary')}" if result.get("summary") else ""),
                        event_time=r["sent_at"])
                    sender["stored_cls"] = cls2
                    reclassified.append(f"{r['entity_name']} ({cls2})")
                except Exception as e:
                    logger.warning(f"Reclassification update failed for {r['entity_name']}: {e}")

    inserted = bq_handler.save_email_log(new_entries)

    # Reply intelligence: stamp records, log activity, auto-advance companies
    for r in replies:
        ename, etype = r["entity_name"], r["entity_type"]
        cls = r.get("classification") or "unclassified"
        note = f"Reply received: \"{r['subject']}\" ({cls})" + (f" — {r.get('summary')}" if r.get("summary") else "")
        try:
            if etype == "company":
                q = f"""UPDATE `{bq_handler.table_id}` SET
                        last_reply_at = @ts, reply_classification = @cls WHERE name = @name"""
                bq_handler.client.query(q, job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("ts", "TIMESTAMP", r["sent_at"]),
                    bq_lib.ScalarQueryParameter("cls", "STRING", cls),
                    bq_lib.ScalarQueryParameter("name", "STRING", ename),
                ])).result()
                # Log with the email's ACTUAL received time, not the sync time
                bq_handler._log_activity(ename, "note", "email-sync",
                                         note_text=note, event_time=r["sent_at"])
                # Auto-advance: a reply means dialogue — Engaged → Contacted.
                # The Internal Test row advances from ANY pre-Contacted state so
                # the loop is testable regardless of where it started.
                sender = known.get(r["counterparty_email"], {})
                past_contact = {"Contacted", "Meeting", "DD", "Offer", "Won"}
                if sender.get("status") == "Engaged" or (sender.get("is_test") and sender.get("status") not in past_contact):
                    bq_handler.update_company_status(ename, "Contacted", created_by="email-sync")
                    advanced.append(ename)
            else:
                investor_handler.add_note(ename, note)
        except Exception as e:
            logger.warning(f"Reply processing failed for {ename}: {e}")

    # Self-healing pass: replies that were ALREADY logged in a previous sync
    # (deduped above) but whose company still sits in a pre-reply stage.
    # Happens when a reply was synced before the stage rules changed, or a
    # stage update failed mid-run. No new log entries or activity notes —
    # just the stage advance that should have happened.
    handled = {r["entity_name"] for r in replies}
    past_contact = {"Contacted", "Meeting", "DD", "Offer", "Won"}
    for r in sorted((e for e in entries if e["direction"] == "received"), key=lambda x: x.get("sent_at") or ""):
        ename = r["entity_name"]
        if r["entity_type"] != "company" or ename in handled:
            continue
        sender = known.get(r["counterparty_email"], {})
        if sender.get("status") == "Engaged" or (sender.get("is_test") and sender.get("status") not in past_contact):
            try:
                # Ensure the reply stamp exists (idempotent), then advance
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET last_reply_at = IFNULL(last_reply_at, @ts) WHERE name = @name""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("ts", "TIMESTAMP", r["sent_at"]),
                        bq_lib.ScalarQueryParameter("name", "STRING", ename),
                    ])).result()
                bq_handler.update_company_status(ename, "Contacted", created_by="email-sync")
                advanced.append(ename)
                handled.add(ename)
            except Exception as e:
                logger.warning(f"Self-heal advance failed for {ename}: {e}")

    return {
        "status": "Success",
        "scanned_days": days,
        "known_contacts": len(known),
        "messages_matched": len(entries),
        "new_logged": inserted,
        "replies_found": len(replies),
        "replies_classified": classified,
        "auto_advanced": advanced,
        "reclassified": reclassified,
        "message": f"Logged {inserted} new messages ({len(replies)} replies, {classified} classified"
                   + (f", {len(reclassified)} reclassified" if reclassified else "") + "). "
                   + (f"Advanced to Contacted: {', '.join(advanced)}." if advanced else "No stage changes."),
    }


# ── Investor (LP) Database Endpoints ─────────────────────────────────────────

class InvestorStatusRequest(BaseModel):
    status: str

class InvestorNoteRequest(BaseModel):
    note: str


@app.get("/investors")
async def get_investors():
    """All investors (LP universe), sorted by fit score."""
    return investor_handler.get_all()


@app.post("/investors/mine")
async def mine_investors(min_fit: float = Query(0.4, description="Minimum company fit score to mine investors from")):
    """
    Extract investors from high-fit companies' PitchBook data
    (active/former investors). Raw save — NO AI. Use InvestorFill per investor.
    """
    universe = bq_handler.get_universe()
    candidates = mine_investors_from_companies(universe, min_fit_score=min_fit)
    inserted = investor_handler.save_investors(candidates)
    return {
        "status": "Success",
        "found": len(candidates),
        "inserted_new": inserted,
        "message": f"Mined {len(candidates)} investors from high-fit companies ({inserted} new). Use InvestorFill to research and score each.",
    }


@app.post("/investors/scrape")
async def scrape_investors(source_name: str = Query(..., description="Investor scraper: 'Praxis Rock Directories' or 'Companies House Registry'")):
    """Scrape a public investor directory → upsert into the LP universe. No AI."""
    if source_name not in investor_scraper.get_supported_sources():
        raise HTTPException(status_code=404, detail=f"Source '{source_name}' not supported. Options: {investor_scraper.get_supported_sources()}")
    found = investor_scraper.scrape_source(source_name)
    if not found:
        return {"status": "Complete", "found": 0, "message": f"No investors found from {source_name}."}
    result = investor_handler.upsert_investors(found)
    return {
        "status": "Success",
        "found": len(found),
        "inserted_new": result["inserted"],
        "merged": result["merged"],
        "message": f"Scraped {len(found)} investors from {source_name}: {result['inserted']} new, {result['merged']} merged.",
    }


@app.post("/investors/upload")
async def upload_investor_file(file: UploadFile = File(...)):
    """
    Upload a PitchBook LP export (Excel/CSV) → parse (152-column 'All Columns'
    format supported) → insert new + merge-fill existing investors. No AI.
    Figures stored as exported (USD millions).
    """
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Only Excel or CSV files are supported.")
    if "pitchbook" not in file.filename.lower():
        raise HTTPException(status_code=400, detail="This uploader expects a PitchBook LP export — filename must contain 'PitchBook'.")
    content = await file.read()
    logger.info(f"Received investor file: {file.filename} ({len(content)} bytes)")

    # Archive raw file to GCS (best-effort)
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        gcs_handler.save_raw_file(content, f"investors/{timestamp}_{file.filename.replace(' ', '_')}", file.content_type)
    except Exception as gcs_err:
        logger.warning(f"GCS archival of investor file failed (continuing): {gcs_err}")

    try:
        investors = parse_investor_file(content, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse file: {e}")

    if not investors:
        raise HTTPException(status_code=422, detail="No investors found — expected a PitchBook LP export with a 'Limited Partners' column.")

    result = investor_handler.upsert_investors(investors)
    return {
        "status": "Success",
        "parsed": len(investors),
        "inserted_new": result["inserted"],
        "merged": result["merged"],
        "message": f"Parsed {len(investors)} investors from {file.filename}: {result['inserted']} new, {result['merged']} merged into existing records. Use InvestorFill to research and score.",
    }


@app.get("/investorfill/eligible")
async def investorfill_eligible(skip_researched: bool = Query(True, description="Skip investors already researched (have a fit score or moved past Identified)")):
    """
    Pre-flight for bulk InvestorFill. Zero AI: excludes only EXPLICIT negatives
    from PitchBook data (mandate outside UK/Europe/ME, or stated strategy
    preferences with none relevant). Unknowns pass — absence of data is not a no.
    """
    investors = investor_handler.get_all()
    total = len(investors)

    excluded_mandate = 0
    excluded_strategy = 0
    skipped_researched = 0
    eligible = []

    for inv in investors:
        if (inv.get("geo_preferences") or "") == "Outside mandate":
            excluded_mandate += 1
            continue
        if (inv.get("strategy_preferences") or "") == "None relevant":
            excluded_strategy += 1
            continue
        if skip_researched and (inv.get("lp_fit_score") is not None or (inv.get("status") or "Identified") != "Identified"):
            skipped_researched += 1
            continue
        eligible.append(inv.get("name"))

    n = len(eligible)
    # Trim to today's remaining free-tier grounding budget (1 grounded call each)
    grounding_used = bq_handler.grounded_calls_used_today()
    grounding_remaining = max(0, DAILY_GROUNDING_BUDGET - grounding_used)
    runnable = eligible[:grounding_remaining]
    return {
        "total_investors": total,
        "excluded_outside_mandate": excluded_mandate,
        "excluded_no_relevant_strategy": excluded_strategy,
        "skipped_already_researched": skipped_researched,
        "eligible_count": n,
        "grounding_budget": DAILY_GROUNDING_BUDGET,
        "grounding_used_today": grounding_used,
        "runnable_now": len(runnable),
        "eligible_names": runnable,
        "estimate": {
            "gemini_calls_per_investor": 1,
            "total_gemini_calls": len(runnable),
            "token_cost_usd_typical": round(len(runnable) * 0.006, 2),
            "grounding_note": "1 grounded call per investor, deducted from the shared daily free-tier budget — paid grounding is never used.",
        },
    }


@app.post("/investorfill/{investor_name}")
async def investorfill(investor_name: str):
    """
    InvestorFill: Gemini + Google Search researches the investor —
    type, AUM, ticket size, contacts + 4-criteria LP fit score.
    """
    _enforce_grounding_budget(1, "InvestorFill")

    # Pull existing context (portfolio overlap helps the search)
    context = {}
    try:
        for inv in investor_handler.get_all():
            if inv.get("name", "").lower() == investor_name.lower():
                context = inv
                break
    except Exception:
        pass

    result = investor_fill(investor_name, context)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])

    # Companies House enrichment for UK entities: PSC principal (UHNWI discovery),
    # officer contacts, and filed net assets as an AUM proxy.
    from ai.investor_fill import ch_enrich_investor
    is_uk = any("united kingdom" in (str(v) or "").lower() or (str(v) or "").strip().upper() == "UK"
                for v in [context.get("hq_country"), result.get("hq_country"), context.get("region"), result.get("region")])
    reg_no = (context.get("registration_number") or "").strip()
    if is_uk or reg_no:
        try:
            ch = ch_enrich_investor(investor_name, reg_no)
            result["psc_summary"] = ch["psc_summary"]
            result["officers_summary"] = ch["officers_summary"]
            result["net_assets_m"] = ch["net_assets_m"]
            # Fill gaps: principal as contact; net assets as AUM proxy
            if ch["principal_name"] and not result.get("contact_name"):
                result["contact_name"] = ch["principal_name"]
            if ch["net_assets_m"] is not None and result.get("aum_m") is None:
                result["aum_m"] = ch["net_assets_m"]
        except Exception as e:
            logger.warning(f"CH enrichment failed for investor '{investor_name}': {e}")

    if not investor_handler.update_enrichment(investor_name, result):
        raise HTTPException(status_code=500, detail="Database update failed")

    # Count this run against the shared grounding budget (best-effort)
    try:
        bq_handler.log_smartfill(investor_name, kind="investorfill")
    except Exception as e:
        logger.warning(f"Failed to log investorfill run: {e}")

    return {"status": "Success", "investor": investor_name, **result}


class InvestorOutreachSendRequest(BaseModel):
    to: str
    subject: str
    body: str
    investor_name: Optional[str] = None


@app.post("/investors/outreach/draft/{investor_name}")
async def draft_investor_outreach(investor_name: str):
    """Draft a personalised LP introduction email from stored data. No search calls."""
    investor = None
    for inv in investor_handler.get_all():
        if inv.get("name", "").lower() == investor_name.lower():
            investor = inv
            break
    if not investor:
        raise HTTPException(status_code=404, detail=f"Investor '{investor_name}' not found")
    from services.outreach_service import draft_lp_outreach_email
    return draft_lp_outreach_email(investor)


@app.post("/investors/outreach/send")
async def send_investor_outreach(req: InvestorOutreachSendRequest):
    """Send an LP outreach email via Gmail SMTP; bumps stage to Contacted on success."""
    logger.info(f"Sending LP outreach to: {req.to} (investor: {req.investor_name})")
    result = send_email(req.to, req.subject, req.body)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["detail"])
    if req.investor_name:
        try:
            investor_handler.update_status(req.investor_name, "Contacted")
        except Exception as e:
            logger.warning(f"Failed to bump investor status after outreach: {e}")
    return result


@app.put("/investors/{investor_name}/status")
async def update_investor_status(investor_name: str, req: InvestorStatusRequest):
    """Move an investor through the relationship pipeline."""
    if req.status not in INVESTOR_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {INVESTOR_STAGES}")
    if not investor_handler.update_status(investor_name, req.status):
        raise HTTPException(status_code=500, detail="Status update failed")
    return {"status": "Success", "investor": investor_name, "new_status": req.status}


@app.post("/investors/{investor_name}/notes")
async def add_investor_note(investor_name: str, req: InvestorNoteRequest):
    if not investor_handler.add_note(investor_name, req.note):
        raise HTTPException(status_code=500, detail="Note save failed")
    return {"status": "Success"}


@app.get("/criteria")
async def get_criteria():
    """Return current qualification criteria + metadata."""
    try:
        meta = bq_handler.get_criteria_meta()
        return meta
    except Exception as e:
        logger.error(f"Failed to load criteria: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/criteria/chat")
async def chat_criteria(req: CriteriaChatRequest):
    """
    Interpret a natural-language criteria change via Gemini.
    Returns proposed new criteria JSON + preview impact counts.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Gemini API key not configured.")

    try:
        import google.generativeai as genai
    except ImportError:
        raise HTTPException(status_code=503, detail="google-generativeai not installed.")

    # Get current criteria
    current = bq_handler.get_criteria()

    # Ask Gemini to interpret the change
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = f"""You are a Private Equity deal origination assistant for Averroes Capital.

CURRENT QUALIFICATION CRITERIA (JSON):
{json.dumps(current, indent=2)}

The user wants to modify these criteria. Their request:
"{req.message}"

Rules:
- The criteria has two main sections: "geography" and "industry"
- geography contains: label, description, regions (list of region/city names), country_codes (list of 2-letter codes)
- industry contains: label, description, keywords (list of keywords to match against company descriptions/sectors)
- There are also top-level fields: focus (string) and target_ebitda (string)
- Only modify what the user asks to change. Keep everything else exactly the same.
- Return the COMPLETE updated criteria object, not just the diff.

RETURN FORMAT — JSON only:
{{
    "proposed_criteria": {{ ... the full updated criteria object ... }},
    "change_summary": "One sentence describing what changed"
}}
"""

    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        result = json.loads(response.text)
    except Exception as e:
        logger.error(f"Gemini criteria chat failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI interpretation failed: {str(e)}")

    proposed = result.get("proposed_criteria", current)
    change_summary = result.get("change_summary", "Criteria updated.")

    # Run preview against universe
    try:
        universe = bq_handler.get_universe()
        preview = preview_criteria(universe, proposed)
    except Exception as e:
        logger.warning(f"Preview failed: {e}")
        preview = {"qualified": 0, "rejected": 0, "total": 0, "sample_qualified": [], "sample_rejected": []}

    return {
        "proposed_criteria": proposed,
        "change_summary": change_summary,
        "preview": preview,
        "current_criteria": current,
    }


@app.post("/criteria/apply")
async def apply_criteria(req: CriteriaApplyRequest):
    """Commit new criteria to BQ and optionally re-qualify the universe."""
    try:
        bq_handler.save_criteria(req.criteria, req.updated_by)
        # Update the in-memory cache
        set_criteria_from_bq(req.criteria)
        logger.info(f"Criteria updated by {req.updated_by}")
    except Exception as e:
        logger.error(f"Failed to save criteria: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save criteria: {str(e)}")

    requalify_result = None
    if req.requalify:
        # Re-qualify entire universe with new criteria
        try:
            universe = bq_handler.get_universe()
            qualified_names = []
            rejected_names = []
            for company in universe:
                qual = qualify_company(company, req.criteria)
                if qual["qualified"]:
                    qualified_names.append(company["name"])
                else:
                    rejected_names.append(company["name"])

            from google.cloud import bigquery as bq_lib

            if qualified_names:
                names_list = ", ".join([f"'{n}'" for n in qualified_names])
                query = f"""UPDATE `{bq_handler.table_id}` SET
                    stage_entered_at = CASE WHEN IFNULL(status, '') != 'Qualified' THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                    qualified_at = IFNULL(qualified_at, CURRENT_TIMESTAMP()),
                    status = 'Qualified' WHERE name IN ({names_list})"""
                bq_handler.client.query(query).result()

            if rejected_names:
                names_list = ", ".join([f"'{n}'" for n in rejected_names])
                query = f"""UPDATE `{bq_handler.table_id}` SET
                    stage_entered_at = CASE WHEN IFNULL(status, '') != 'Not a Fit' THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
                    status = 'Not a Fit' WHERE name IN ({names_list})"""
                bq_handler.client.query(query).result()

            requalify_result = {
                "qualified": len(qualified_names),
                "rejected": len(rejected_names),
                "total": len(universe),
            }
        except Exception as e:
            logger.error(f"Re-qualification failed: {e}")
            requalify_result = {"error": str(e)}

    return {
        "status": "Success",
        "message": "Criteria saved and applied.",
        "requalify_result": requalify_result,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
