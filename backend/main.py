import os
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from google.cloud import storage, bigquery
from dotenv import load_dotenv

from scrapers.conference_scraper import ConferenceScraper
from storage.gcs_handler import GCSHandler
from ai.criteria import AverroesPhilosophy, evaluate_target

load_dotenv()

app = FastAPI(title="Averroes Deal Origination API")
scraper = ConferenceScraper()
gcs_handler = GCSHandler(bucket_name="averroes-deal-intelligence")

# --- Models ---
class CompanyTarget(BaseModel):
    name: str
    website: Optional[str] = None
    sector: Optional[str] = "Unknown"
    match_score: float = 0.0
    source: str = "Manual"
    status: str = "Qualified"

# --- Endpoints ---

@app.get("/")
async def root():
    return {"status": "Averroes Intelligence Platform Active", "version": "v1.1.0"}

@app.post("/ingest/conference")
async def ingest_conference(conference_name: str = Query(..., description="Name of the conference to scrape")):
    """
    1. Scrapes the specific conference website.
    2. Filters for B2B SaaS in UK/Europe (Placeholder AI check).
    3. Persists to GCS.
    """
    if conference_name not in scraper.get_all_targets():
        raise HTTPException(status_code=404, detail="Conference not monitored.")
    
    companies = scraper.scrape_conference(conference_name)
    
    if not companies:
        return {"status": "Complete", "count": 0, "message": "No new companies found."}
    
    # Simple initial filtering logic
    philosophy = AverroesPhilosophy()
    refined_companies = []
    
    for c in companies:
        # Initial score 
        score = evaluate_target({"sector": "SaaS", "region": "UK"}, philosophy)
        c["match_score"] = score
        refined_companies.append(c)
    
    # Save to GCS
    gcs_filename = gcs_handler.save_companies(refined_companies, conference_name.lower().replace(" ", "_"))
    
    return {
        "status": "Success",
        "count": len(refined_companies),
        "source": conference_name,
        "gcs_path": gcs_filename
    }

@app.get("/pipeline", response_model=List[CompanyTarget])
async def get_pipeline():
    """
    Mocked pipeline for now. Will eventually read from BigQuery/GCS.
    """
    return [
        CompanyTarget(
            name="SaaS Synergy", 
            website="https://synergy.io", 
            sector="B2B SaaS", 
            match_score=0.95,
            source="SaaSiest",
            status="Qualified"
        )
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
