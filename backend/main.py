import os
import json
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv

from scrapers.conference_scraper import ConferenceScraper
from scrapers.acquire_scraper import AcquireScraper
from storage.gcs_handler import GCSHandler
from ai.criteria import AverroesPhilosophy, evaluate_target, generate_analysis_prompt

load_dotenv()

app = FastAPI(title="Averroes Deal Origination API")
conf_scraper = ConferenceScraper()
acq_scraper = AcquireScraper()
gcs_handler = GCSHandler(bucket_name="averroes-deal-intelligence")

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
    data_path = "backend/data/candidates.json"
    if not os.path.exists(data_path):
        return []
        
    try:
        with open(data_path, 'r') as f:
            candidates = json.load(f)
            return candidates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load pipeline: {str(e)}")

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
        
    # Phase C: Data Entry (Update JSON)
    data_path = "backend/data/candidates.json"
    existing_data = []
    if os.path.exists(data_path):
        with open(data_path, 'r') as f:
            existing_data = json.load(f)
            
    existing_names = {c['name'] for c in existing_data}
    new_data = existing_data + [c for c in refined_companies if c['name'] not in existing_names]
    
    # We could simulate Step 2 (Registry Check) here before saving, but for now we write directly
    with open(data_path, 'w') as f:
        json.dump(new_data, f, indent=2)
        
    # Backup to GCS
    gcs_filename = gcs_handler.save_companies(refined_companies, "acquire_marketplace")
    
    return {
        "status": "Success",
        "count": len(refined_companies),
        "total_in_db": len(new_data),
        "source": "Acquire.com Pipeline",
        "gcs_path": gcs_filename
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
