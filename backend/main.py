import os
import json
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from scrapers.conference_scraper import ConferenceScraper
from scrapers.acquire_scraper import AcquireScraper
from scrapers.ranking_scraper import RankingListScraper
from storage.gcs_handler import GCSHandler
from ai.criteria import AverroesPhilosophy, evaluate_target, generate_analysis_prompt

load_dotenv()

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
gcs_handler = GCSHandler(bucket_name="averroes-deal-intelligence")

# --- Utilities ---

def _sync_to_databases(refined_companies: List[dict]):
    """
    Unified logic to update Universe and Candidates databases.
    """
    # 1. Update Universe (ALL data)
    universe_path = "data/universe.json"
    existing_universe = []
    if os.path.exists(universe_path):
        with open(universe_path, 'r') as f:
            existing_universe = json.load(f)
            
    existing_uni_names = {c['name'] for c in existing_universe}
    new_universe = existing_universe + [c for c in refined_companies if c['name'] not in existing_uni_names]
    
    with open(universe_path, 'w') as f:
        json.dump(new_universe, f, indent=2)

    # 2. Update Qualified Candidates (Only >= 0.4 match score)
    qualified_companies = [c for c in refined_companies if c.get("match_score", 0) >= 0.4]
    
    data_path = "data/candidates.json"
    existing_data = []
    if os.path.exists(data_path):
        with open(data_path, 'r') as f:
            existing_data = json.load(f)
            
    existing_names = {c['name'] for c in existing_data}
    new_data = existing_data + [c for c in qualified_companies if c['name'] not in existing_names]
    
    with open(data_path, 'w') as f:
        json.dump(new_data, f, indent=2)
    
    return len(new_universe), len(new_data)

# --- Models ---
class CompanyTarget(BaseModel):
    name: str
    website: Optional[str] = None
    sector: Optional[str] = "Unknown"
    description: Optional[str] = ""
    match_score: float = 0.0
    source: str = "Manual"
    status: str = "Qualified"

# --- Endpoints ---

@app.get("/")
async def root():
    return {"status": "Averroes Intelligence Platform Active - Linear Pipeline Mode", "version": "v1.3.0"}

@app.get("/pipeline", response_model=List[CompanyTarget])
async def get_pipeline():
    """
    Reads the active target pipeline from the JSON database.
    """
    data_path = "data/candidates.json"
    if not os.path.exists(data_path):
        return []
        
    try:
        with open(data_path, 'r') as f:
            candidates = json.load(f)
            return candidates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load pipeline: {str(e)}")

@app.get("/universe", response_model=List[CompanyTarget])
async def get_universe():
    """
    Returns the complete Data Lake / Universe of all companies ever scraped,
    regardless of their match score or qualification.
    """
    data_path = "data/universe.json"
    if not os.path.exists(data_path):
        return []
    try:
        with open(data_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load universe: {str(e)}")

@app.post("/ingest/marketplace")
async def ingest_marketplace():
    """
    Phase A: Trigger the Acquire loop.
    Phase B: Evaluate raw targets via Gemini prompt logic.
    Phase C: Update candidates.json
    """
    # Phase A: Discovery Trigger
    raw_companies = acq_scraper.scrape_marketplace()
    
    if not raw_companies:
        return {"status": "Complete", "count": 0, "message": "No new marketplace companies found."}
    
    philosophy = AverroesPhilosophy()
    refined_companies = []
    
    # Phase B: AI Evaluation
    for c in raw_companies:
        # In production, this would hit Gemini API: `response = gemini.generate_content(generate_analysis_prompt(c['name'], str(c), philosophy))`
        # Here we simulate the LLM effectively extracting and grading based on the prompt instructions:
        score = evaluate_target(c, philosophy)
        c["match_score"] = score
        
        # Only retain if it hits a threshold or let the dashboard filter it
        c["status"] = "Under Review" if score >= 0.85 else "Qualified" if score >= 0.4 else "Not a Fit"
        refined_companies.append(c)
        
    uni_count, cand_count = _sync_to_databases(refined_companies)
        
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
    
    philosophy = AverroesPhilosophy()
    refined_companies = []
    
    # Process through AI Matching
    for c in raw_companies:
        score = evaluate_target(c, philosophy)
        c["match_score"] = score
        c["status"] = "Under Review" if score >= 0.85 else "Qualified" if score >= 0.4 else "Not a Fit"
        refined_companies.append(c)
    
    uni_count, cand_count = _sync_to_databases(refined_companies)
    
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
    
    philosophy = AverroesPhilosophy()
    refined_companies = []
    
    for c in raw_companies:
        score = evaluate_target(c, philosophy)
        c["match_score"] = score
        c["status"] = "Under Review" if score >= 0.85 else "Qualified" if score >= 0.4 else "Not a Fit"
        refined_companies.append(c)
        
    uni_count, cand_count = _sync_to_databases(refined_companies)
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
