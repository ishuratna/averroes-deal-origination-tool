import os
import json
import logging
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, File, UploadFile
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from scrapers.conference_scraper import ConferenceScraper
from scrapers.marketplace_scraper import MarketplaceScraper
from scrapers.ranking_scraper import RankingListScraper
from scrapers.directory_scraper import DirectoryScraper
from scrapers.network_scraper import NetworkScraper
from storage.investor_handler import InvestorBQHandler, INVESTOR_STAGES
from ai.investor_fill import investor_fill, mine_investors_from_companies
from services.investor_upload_service import parse_investor_file
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

# Add CORS Middleware to allow the Next.js frontend to fetch data
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to the frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

conf_scraper = ConferenceScraper()
market_scraper = MarketplaceScraper()
rank_scraper = RankingListScraper()
directory_scraper = DirectoryScraper()
network_scraper = NetworkScraper()
enrichment_agent = EnrichmentAgent()
gcs_handler = GCSHandler(bucket_name=GCS_BUCKET)
bq_handler = BigQueryHandler(project_id=GCP_PROJECT, dataset_id=BQ_DATASET)
investor_handler = InvestorBQHandler(bq_handler.client, bq_handler.project_id, dataset_id=BQ_DATASET)

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


@app.post("/smartfill/{company_name}")
async def smartfill_company(company_name: str):
    """SmartFill: Qualify (UK/Ireland + Tech) + enrich founder/LinkedIn/website."""
    logger.info(f"SmartFill triggered for: {company_name}")
    company_data = {"name": company_name}
    try:
        for c in bq_handler.get_universe():
            if c.get("name") == company_name:
                company_data = c
                break
    except Exception:
        pass

    # Step 1: Qualify via hard filters (Gemini if available, else keywords)
    qual = qualify_company_with_gemini(company_data)
    new_status = qual["status"]

    # Step 2: Enrich with founder details + company description
    founder_info = enrichment_agent.enrich_founder_details(company_name)
    website = founder_info.get("website", "")
    description = founder_info.get("description", "")

    # Extract size info
    size_bucket = qual.get("size_bucket", "")
    size_confidence = qual.get("size_confidence", "")
    size_reason = qual.get("size_reason", "")

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
            scoring_result = score_company(scoring_input)
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
            status = @status,
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
async def smartfill_eligible(skip_processed: bool = Query(True, description="Skip companies already SmartFilled (status not Scraped/Uploaded)")):
    """
    Pre-flight for bulk SmartFill. Applies the two cheap keyword filters
    (UK/Ireland geography + tech/software industry) with ZERO AI calls,
    and returns the eligible company list plus a Gemini credit estimate.
    """
    universe = bq_handler.get_universe()
    total = len(universe)

    non_uk_ie = 0
    uk_ie_non_tech = 0
    already_processed = 0
    eligible = []

    for c in universe:
        qual = qualify_company(c)  # keyword-based, no AI
        if not qual["is_uk_ireland"]:
            non_uk_ie += 1
            continue
        if not qual["is_tech"]:
            uk_ie_non_tech += 1
            continue
        if skip_processed and c.get("status") not in ("Scraped", "Uploaded"):
            already_processed += 1
            continue
        eligible.append(c.get("name"))

    n = len(eligible)
    # Gemini calls per SmartFill (from code paths):
    #   qualification 1 + enrichment 1 (grounded) + CH PDF parse 0-3
    #   + scoring 1 (grounded, if qualified) + revenue estimate 0-1 (grounded)
    est = {
        "gemini_calls_per_company": {"min": 3, "typical": 5, "max": 7},
        "grounded_calls_per_company": {"min": 2, "typical": 3},
        "total_gemini_calls": {"min": n * 3, "typical": n * 5, "max": n * 7},
        "total_grounded_calls_typical": n * 3,
        "token_cost_usd_typical": round(n * 0.015, 2),
        "grounding_note": "First ~1,500 grounded prompts/day are free (≈500 companies/day). Beyond that $35/1K prompts adds ~$0.10/company.",
    }

    return {
        "total_universe": total,
        "excluded_non_uk_ie": non_uk_ie,
        "excluded_non_tech": uk_ie_non_tech,
        "skipped_already_processed": already_processed,
        "eligible_count": n,
        "eligible_names": eligible,
        "estimate": est,
    }


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


@app.post("/outreach/draft/{company_name}")
async def draft_outreach(company_name: str):
    """Generate a personalised outreach email draft using Gemini AI."""
    logger.info(f"Outreach draft requested for: {company_name}")
    # Fetch company data from BQ
    company_data = {"name": company_name}
    try:
        for c in bq_handler.get_universe():
            if c.get("name") == company_name:
                company_data = c
                break
    except Exception:
        pass
    result = draft_outreach_email(company_data)
    return result


@app.post("/outreach/send")
async def send_outreach(req: OutreachSendRequest):
    """Send an outreach email via Gmail SMTP."""
    logger.info(f"Sending outreach to: {req.to} (company: {req.company_name})")
    result = send_email(req.to, req.subject, req.body)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["detail"])
    # Log the sent email in BQ (best-effort)
    try:
        from google.cloud import bigquery as bq_lib
        query = f"""UPDATE `{bq_handler.table_id}`
                    SET status = 'Engaged'
                    WHERE name = @name AND status != 'Not a Fit'"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("name", "STRING", req.company_name or ""),
        ])
        bq_handler.client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.warning(f"Failed to update status after outreach: {e}")
    return result


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

    success = bq_handler.update_company_status(company_name, req.status, req.created_by)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update status.")
    return {"status": "Success", "company": company_name, "new_status": req.status}


class RemoveRequest(BaseModel):
    created_by: Optional[str] = "Ishu Ratna"


@app.post("/company/{company_name}/remove")
async def remove_from_pipeline(company_name: str, req: RemoveRequest):
    """Remove a company from the pipeline — sets status to 'Not a Fit' and score to 0."""
    logger.info(f"Removing '{company_name}' from pipeline by {req.created_by}")
    try:
        from google.cloud import bigquery as bq_lib
        query = f"""UPDATE `{bq_handler.table_id}` SET status = 'Not a Fit', match_score = 0.0 WHERE name = @name"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
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
    return {
        "total_investors": total,
        "excluded_outside_mandate": excluded_mandate,
        "excluded_no_relevant_strategy": excluded_strategy,
        "skipped_already_researched": skipped_researched,
        "eligible_count": n,
        "eligible_names": eligible,
        "estimate": {
            "gemini_calls_per_investor": 1,
            "total_gemini_calls": n,
            "token_cost_usd_typical": round(n * 0.006, 2),
            "grounding_note": "1 grounded search call per investor. First ~1,500/day free; beyond that $35/1K adds ~$0.035/investor.",
        },
    }


@app.post("/investorfill/{investor_name}")
async def investorfill(investor_name: str):
    """
    InvestorFill: Gemini + Google Search researches the investor —
    type, AUM, ticket size, contacts + 4-criteria LP fit score.
    """
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

    if not investor_handler.update_enrichment(investor_name, result):
        raise HTTPException(status_code=500, detail="Database update failed")

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
                query = f"""UPDATE `{bq_handler.table_id}` SET status = 'Qualified' WHERE name IN ({names_list})"""
                bq_handler.client.query(query).result()

            if rejected_names:
                names_list = ", ".join([f"'{n}'" for n in rejected_names])
                query = f"""UPDATE `{bq_handler.table_id}` SET status = 'Not a Fit' WHERE name IN ({names_list})"""
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
