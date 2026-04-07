import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from google.cloud import storage, bigquery
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Averroes Deal Origination API")

# --- Models ---
class TargetCriteria(BaseModel):
    sector: Optional[str] = None
    min_ebitda: Optional[float] = None
    min_revenue: Optional[float] = None
    region: Optional[str] = "UK/Europe"

class CompanyTarget(BaseModel):
    name: str
    website: str
    sector: str
    estimated_ebitda: float
    description: str
    match_score: float
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None

# --- Services ---
def get_gcs_client():
    # Credentials should be in environment or available via ADC
    return storage.Client()

def get_bq_client():
    return bigquery.Client()

# --- Endpoints ---

@app.get("/")
async def root():
    return {"status": "Averroes Intelligence Platform Active", "version": "v1.0.0"}

@app.post("/analyze-target", response_model=CompanyTarget)
async def analyze_target(url: str):
    """
    AI-powered analysis of a target URL.
    1. Scrapes the website.
    2. Uses Gemini to extract financials/sector.
    3. Matches against Averroes investment philosophy.
    """
    # Placeholder for the AI pipeline
    return CompanyTarget(
        name="Target Company X",
        website=url,
        sector="B2B SaaS",
        estimated_ebitda=5.5,
        description="A specialized provider of vertical software solutions.",
        match_score=0.92,
        contact_name="Executive Team",
        contact_email="team@targetx.com"
    )

@app.get("/pipeline", response_model=List[CompanyTarget])
async def get_pipeline():
    """
    Fetch the current target pipeline from BigQuery.
    """
    # Placeholder for BigQuery pull
    return [
        CompanyTarget(
            name="SaaS Synergy", 
            website="https://synergy.io", 
            sector="SaaS", 
            estimated_ebitda=8.2, 
            description="Infrastructure for hybrid work.",
            match_score=0.95
        )
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
