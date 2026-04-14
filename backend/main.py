import os
import json
import logging
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from scrapers.conference_scraper import ConferenceScraper
from scrapers.acquire_scraper import AcquireScraper
from scrapers.ranking_scraper import RankingListScraper
from storage.gcs_handler import GCSHandler
from storage.bq_handler import BigQueryHandler
from ai.criteria import AverroesPhilosophy, evaluate_target, generate_analysis_prompt
from ai.enrichment import EnrichmentAgent

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
acq_scraper = AcquireScraper()
rank_scraper = RankingListScraper()
enrichment_agent = EnrichmentAgent()
gcs_handler = GCSHandler(bucket_name=GCS_BUCKET)
bq_handler = BigQueryHandler(project_id=GCP_PROJECT, dataset_id=BQ_DATASET)

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
        
        if score >= 0.60:
            c["status"] = "Under Review"
            # Automated Enrichment for high-conviction targets
            founder_info = enrichment_agent.enrich_founder_details(c['name'])
            c.update(founder_info)
        else:
            c["status"] = "Qualified" if score >= 0.3 else "Not a Fit"
            
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
        "gemini_enabled": bool(os.getenv("GEMINI_API_KEY"))
    }

@app.get("/pipeline", response_model=List[dict])
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
async def ingest_marketplace():
    """
    Phase A: Trigger the Acquire loop.
    Phase B: Evaluate raw targets via Gemini prompt logic.
    Phase C: Update candidates.json
    """
    raw_companies = acq_scraper.scrape_marketplace()
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new marketplace companies found."}
    
    refined_companies, uni_count, cand_count = _process_and_refine(raw_companies)
    
    # Backup to GCS
    gcs_filename = gcs_handler.save_companies(refined_companies, "acquire_marketplace")
    
    return {
        "status": "Success",
        "count": len(refined_companies),
        "total_in_universe": uni_count,
        "total_in_candidates": cand_count,
        "source": "Acquire.com Pipeline",
        "gcs_path": gcs_filename
    }

@app.post("/ingest/conference")
async def ingest_conference(conference_name: str = Query(..., description="Name of the conference to scrape")):
    """
    1. Scrapes the specific conference.
    2. Runs AI filtering.
    3. Updates Universe (all) and Pipeline (filtered).
    """
    if conference_name not in conf_scraper.get_all_targets():
        raise HTTPException(status_code=404, detail="Conference not monitored.")
    
    raw_companies = conf_scraper.scrape_conference(conference_name)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new companies found."}
    
    refined_companies, uni_count, cand_count = _process_and_refine(raw_companies)
    
    # Backup to GCS
    gcs_filename = gcs_handler.save_companies(refined_companies, conference_name.lower().replace(" ", "_"))
    
    return {
        "status": "Success",
        "count": len(refined_companies),
        "total_in_universe": uni_count,
        "total_in_candidates": cand_count,
        "source": conference_name,
        "gcs_path": gcs_filename
    }

@app.post("/ingest/ranking")
async def ingest_ranking(list_name: str = Query(..., description="Name of the ranking list to ingest")):
    """
    Ingests high-growth companies from curated lists (FT 1000, etc.)
    """
    if list_name not in rank_scraper.get_supported_lists():
        raise HTTPException(status_code=404, detail=f"Ranking list '{list_name}' not supported.")
    
    raw_companies = rank_scraper.scrape_ranking(list_name)
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new companies found."}
    
    refined_companies, uni_count, cand_count = _process_and_refine(raw_companies)
    
    # Backup to GCS
    gcs_filename = gcs_handler.save_companies(refined_companies, list_name.lower().replace(" ", "_"))
    
    return {
        "status": "Success",
        "count": len(refined_companies),
        "total_in_universe": uni_count,
        "total_in_candidates": cand_count,
        "source": list_name,
        "gcs_path": gcs_filename
    }

@app.post("/enrich/{company_name}")
async def manual_enrich(company_name: str):
    """
    Manually triggers enrichment for a specific company in the pipeline.
    Writes contact updates directly back to BigQuery.
    """
    founder_info = enrichment_agent.enrich_founder_details(company_name)
    success = bq_handler.update_company_enrichment(company_name, founder_info)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update company in database.")
        
    return {"status": "Success", "company": company_name, "enriched_data": founder_info}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
