import os
import json
import logging
import re
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, File, UploadFile, Request
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
from scrapers.ch_sic_scraper import CHSicScraper
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

# GZip: tabular JSON compresses ~10x. Registered FIRST so it wraps innermost
# (compresses actual responses); makes the 13k-row universe a few MB on the
# wire instead of tens.
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

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
ch_sic_scraper = CHSicScraper()
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
async def get_universe(include_hidden: int = Query(0, description="1 = also return soft-deleted rows")):
    """
    Returns the complete Data Lake (Universe) from BigQuery — SLIM columns.
    List views never show the heavy blob fields; at 13k rows SELECT * OOM-
    killed the container. Profiles fetch full depth via /company/{name}/full.
    """
    try:
        return bq_handler.get_universe_slim(include_hidden=bool(include_hidden))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load universe: {str(e)}")


class HideRequest(BaseModel):
    names: List[str]
    created_by: str = "user"


@app.post("/companies/hide")
async def companies_hide(req: HideRequest):
    """SOFT delete from the Master Universe VIEW. The rows stay in BigQuery
    with hidden_at/hidden_by stamped, so nothing is ever lost and everything
    is restorable via /companies/unhide."""
    if not req.names:
        raise HTTPException(status_code=400, detail="No companies given.")
    n = bq_handler.hide_companies(req.names, by=req.created_by)
    for name in req.names[:50]:
        try:
            bq_handler.add_activity_note(name, f"Removed from Master Universe view by {req.created_by} (soft delete: row retained in BigQuery)", req.created_by)
        except Exception:
            pass
    return {"status": "Success", "hidden": n, "requested": len(req.names),
            "note": "Rows remain in BigQuery; use /companies/unhide to restore."}


@app.post("/companies/unhide")
async def companies_unhide(req: HideRequest):
    """Restore soft-deleted companies to the Master Universe view."""
    if not req.names:
        raise HTTPException(status_code=400, detail="No companies given.")
    n = bq_handler.unhide_companies(req.names)
    return {"status": "Success", "restored": n}


@app.get("/companies/hidden")
async def companies_hidden():
    """Soft-deleted companies (for review/restore)."""
    rows = [c for c in bq_handler.get_universe_slim(include_hidden=True) if c.get("hidden_at")]
    return {"count": len(rows), "companies": [
        {"name": c.get("name"), "source": c.get("source"), "status": c.get("status"),
         "hidden_at": str(c.get("hidden_at") or "")[:19], "hidden_by": c.get("hidden_by")}
        for c in rows]}


@app.get("/company/{company_name}/full")
async def get_company_full(company_name: str):
    """Every stored column for one company (profile depth on demand)."""
    row = bq_handler.get_company_full(company_name)
    if not row:
        raise HTTPException(status_code=404, detail="Company not found.")
    return row

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

def _ingest_ch_sic() -> dict:
    """Companies House SIC-code registry search → raw ingest, no AI. This is
    the MANUAL full pull — all 16 codes, right now — triggered by the
    Refresh button. Streamed (unlike its siblings) because that is
    meaningfully slower than a single-page scrape. The Friday auto-refresh
    (_weekly_source_refresh) covers the same 16 codes too, but only a
    bounded slice per week via ch_sic_scraper.scrape_weekly_batch() — see
    scrapers/ch_sic_scraper.py for why the two paths deliberately differ."""
    rows, capped_codes = ch_sic_scraper.scrape_all_sic_codes_detailed()
    if not rows:
        return {"status": "Complete", "count": 0,
                "message": "No companies found — check COMPANIES_HOUSE_API_KEY is configured."}
    success = bq_handler.save_targets(rows)
    if not success:
        return {"status": "Error", "count": 0, "message": "Database save failed."}
    gcs_filename = gcs_handler.save_companies(rows, "companies_house_sic_search")
    msg = f"Pulled {len(rows)} active companies from Companies House across 16 SIC codes. Use SmartFill to score and enrich."
    if capped_codes:
        msg += (f" NOT exhaustive for {len(capped_codes)} code(s) — Companies House does not allow "
               f"paging past ~10,000 results per code: {'; '.join(capped_codes)}.")
    return {"status": "Success", "count": len(rows), "source": "Companies House SIC Search",
            "message": msg, "capped_codes": capped_codes, "gcs_path": gcs_filename}


@app.post("/ingest/ch-sic")
async def ingest_ch_sic():
    """Companies House SIC-code registry search — see _ingest_ch_sic()."""
    return _stream_json(_ingest_ch_sic)


@app.post("/ingest/upload")
async def upload_custom_file(file: UploadFile = File(...)):
    """Fast upload: Parse Excel -> deduplicate -> save to BigQuery. No Gemini
    calls. STREAMED with heartbeats: big files (Gain = 8,500+ rows) take
    minutes and silent connections get killed by hostile networks; the final
    line of the response is the JSON result."""
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(status_code=400, detail="Only Excel or CSV files are supported.")
    content = await file.read()
    filename = file.filename
    content_type = file.content_type
    logger.info(f"Received file for ingestion: {filename} ({len(content)} bytes)")

    def _work():
        try:
            try:
                gcs = GCSHandler()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_filename = f"{timestamp}_{filename.replace(' ', '_')}"
                gcs.save_raw_file(content, safe_filename, content_type)
            except Exception as gcs_err:
                logger.warning(f"GCS Archival failed (continuing): {gcs_err}")
            is_pitchbook = "pitchbook" in filename.lower()
            is_inven = "inven" in filename.lower()
            is_gain = "gain" in filename.lower()
            try:
                if is_pitchbook:
                    logger.info(f"PitchBook file detected: {filename}")
                    targets = parse_pitchbook_excel(content)
                elif is_inven:
                    logger.info(f"Inven file detected: {filename}")
                    from services.inven_service import parse_inven_csv
                    targets = parse_inven_csv(content)
                elif is_gain:
                    logger.info(f"Gain.pro file detected: {filename}")
                    from services.gain_service import parse_gain_excel
                    targets = parse_gain_excel(content)
                else:
                    targets = parse_proprietary_excel(content)
                logger.info(f"Parsed {len(targets)} targets from {filename} "
                            f"({'PitchBook' if is_pitchbook else 'Inven' if is_inven else 'Gain' if is_gain else 'Generic'})")
            except Exception as parse_err:
                return {"status": "Error", "detail": f"Parse failed: {parse_err}"}
            if not targets:
                return {"status": "Complete", "count": 0, "message": "No valid targets found."}
            source_label = f"Upload: {filename}"
            for t in targets:
                t["source"] = source_label
                t["status"] = "Uploaded"
            if not bq_handler.save_targets(targets):
                return {"status": "Error", "detail": "Database save failed."}

            # Data-rich uploads (Inven/Gain) get instant zero-AI hard-filter
            # triage. DORMANT until PREQUALIFY_ON_UPLOAD=1 (awaiting sign-off).
            preq = None
            if (is_inven or is_gain) and os.getenv("PREQUALIFY_ON_UPLOAD", "0") == "1":
                try:
                    preq = _prequalify_local(only_names={t["name"] for t in targets})
                except Exception as e:
                    logger.warning(f"Pre-qualification after upload failed (non-fatal): {e}")

            msg = f"Uploaded {len(targets)} targets from {filename}."
            if preq:
                msg += (f" Hard-filter triage (0 AI calls): {preq['not_a_fit']} Not a Fit, "
                        f"{preq['passed_awaiting_smartfill']} passed and awaiting SmartFill scoring.")
            else:
                msg += " Use SmartFill to enrich."
            return {"status": "Success", "message": msg, "count": len(targets),
                    "source": source_label, "prequalified": preq}
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return {"status": "Error", "detail": str(e)}

    return _stream_json(_work)


# ── One-time data migration: revenue bands v3 (£15-40M cheque mandate) ──────
# Recomputes the stored revenue_band for EVERY row (including Qualified) under
# the new thresholds (Too Early <£5M, Target £5-40M, Too Large >£40M) and
# applies the new £40M size cap to currently-Qualified companies. Guarded by
# an activity-log marker so it runs exactly once per rule version.

BAND_RULES_VERSION = "band-rules-v3b-2.5-40m"
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
                WHEN {_REV_EXPR} < 2.5 THEN 'Too Early'
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


# ── Retroactive contact re-resolution (contacts v2 waterfall) ───────────────
# Re-runs the contact waterfall over every already-SmartFilled company:
# site scrape + verification of the stored email + verified pattern fallback.
# NO AI calls (those companies already had their AI pass) — cost is site
# fetches plus verifier credits when a key is configured. The version marker
# includes verifier availability, so adding HUNTER_API_KEY later triggers a
# fresh, fully-verified pass on the next deploy automatically.

def _contacts_marker() -> str:
    # v3: new waterfall order (web → site → retry → patterns) with verification
    # only for guesswork. Bumping this version re-runs the retro pass.
    #
    # DELIBERATELY NOT bumped to v4. The v4 waterfall spends Hunter credits per
    # company (email-finder + up to 5 verifier calls), so re-running it across
    # every stored company must be a decision, not a side effect of a deploy.
    # New SmartFill runs use v4 immediately; re-run history on demand with
    # POST /contacts/reverify.
    verified = bool(os.getenv("HUNTER_API_KEY", "") or os.getenv("EMAIL_VERIFIER_API_KEY", ""))
    return f"contacts-v3-{'verified' if verified else 'scrape-only'}"


def _waterfall_provenance(res: dict) -> str:
    """One human-readable line recording how we arrived at this address, stored
    on the row so the company card can show it before anyone hits send."""
    if not res.get("email"):
        return res.get("verification", "")
    bits = [f"{res.get('step', '')}", res.get("source", ""), res.get("verification", "")]
    line = " | ".join([b for b in bits if b])
    if res.get("founder_guess") and res["founder_guess"] != res["email"]:
        line += f" | founder guess not used: {res['founder_guess']} ({res.get('founder_guess_status')})"
    return line[:900]


def _retro_resolve_contacts(force: bool = False) -> dict:
    from google.cloud import bigquery as bq_lib
    from services.contact_finder import resolve_contact_email
    marker = _contacts_marker()
    try:
        if not force:
            rows = list(bq_handler.client.query(
                f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                    WHERE action_type = 'migration' AND note_text = '{marker}'""").result())
            if rows and int(rows[0].n) > 0:
                return {"status": "skipped", "reason": "already applied", "marker": marker}
        logger.info(f"[Migration] {marker}: re-resolving contacts for SmartFilled companies...")

        updated, blanked, confirmed, processed = [], [], 0, 0
        for c in bq_handler.get_universe():
            if c.get("source") == "Internal Test" or not c.get("last_smartfill_at"):
                continue
            if not (c.get("website") or c.get("contact_email")):
                continue
            processed += 1
            if processed > 500:
                break
            try:
                res = resolve_contact_email(c.get("website", ""), c.get("contact_name", ""),
                                            c.get("contact_email", ""), "existing record")
                new_email = (res["email"] or "").lower()
                old_email = (c.get("contact_email") or "").strip().lower()
                if new_email == old_email:
                    confirmed += 1
                    continue
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET contact_email = @em WHERE name = @name""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("em", "STRING", res["email"]),
                        bq_lib.ScalarQueryParameter("name", "STRING", c["name"]),
                    ])).result()
                if res["email"]:
                    updated.append(c["name"])
                    note = f"Contact email updated by retro waterfall: '{old_email or '(empty)'}' -> '{res['email']}' ({res['source']}; {res['verification']})"
                else:
                    blanked.append(c["name"])
                    note = f"Contact email '{old_email}' cleared by retro waterfall: {res['verification']}"
                bq_handler.add_activity_note(c["name"], note, "contact-finder")
            except Exception as e:
                logger.warning(f"[Migration] contact re-resolve failed for {c.get('name')}: {e}")

        bq_handler._log_activity("__system__", "migration", "system", note_text=marker)
        summary = {"status": "complete", "marker": marker, "processed": processed,
                   "updated": len(updated), "blanked": len(blanked), "unchanged": confirmed}
        logger.info(f"[Migration] {marker} done: {summary}")
        return summary
    except Exception as e:
        logger.error(f"[Migration] contacts retro pass failed: {e}")
        return {"status": "error", "detail": str(e)}


def _retro_qualified_blank():
    """
    ONE-OFF: full waterfall (INCLUDING the AI retry ladder) for Qualified
    companies whose contact email is blank — exactly the rows blocking
    outreach. The retry ladder is budget-metered per call (weight 1, logged),
    and the run is capped, so the day's grounding budget cannot be drained.
    Also fills the To of an existing unsent draft so Review & Send is complete.
    """
    from google.cloud import bigquery as bq_lib
    from services.contact_finder import resolve_contact_email
    marker = "contacts-v3-qualified-blank-oneoff"
    try:
        rows = list(bq_handler.client.query(
            f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                WHERE action_type = 'migration' AND note_text = '{marker}'""").result())
        if rows and int(rows[0].n) > 0:
            return
        logger.info(f"[Migration] {marker}: waterfall for Qualified companies with blank emails...")

        def make_retry(comp):
            def _r():
                try:
                    if bq_handler.grounded_calls_used_today() + 1 > DAILY_GROUNDING_BUDGET:
                        return {}
                    bq_handler.log_smartfill(comp.get("name", ""), kind="newslookup")
                except Exception:
                    pass
                return enrichment_agent.retry_email_search(
                    comp.get("name", ""), comp.get("website", ""), comp.get("contact_name", ""))
            return _r

        found, blank, processed = [], 0, 0
        for c in bq_handler.get_universe():
            if c.get("status") != "Qualified" or c.get("source") == "Internal Test":
                continue
            if (c.get("contact_email") or "").strip():
                continue
            processed += 1
            if processed > 150:
                break
            try:
                res = resolve_contact_email(c.get("website", ""), c.get("contact_name", ""),
                                            "", "", retry_fn=make_retry(c))
                if not res["email"]:
                    blank += 1
                    continue
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET
                        contact_email = @em,
                        outreach_draft_to = CASE WHEN outreach_sent_at IS NULL
                            AND IFNULL(outreach_draft_to, '') = '' THEN @em ELSE outreach_draft_to END
                        WHERE name = @name""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("em", "STRING", res["email"]),
                        bq_lib.ScalarQueryParameter("name", "STRING", c["name"]),
                    ])).result()
                found.append(c["name"])
                bq_handler.add_activity_note(
                    c["name"],
                    f"Contact email found by one-off waterfall: {res['email']} ({res['source']}; {res['verification']})",
                    "contact-finder")
            except Exception as e:
                logger.warning(f"[Migration] qualified-blank waterfall failed for {c.get('name')}: {e}")

        bq_handler._log_activity("__system__", "migration", "system", note_text=marker)
        logger.info(f"[Migration] {marker} done: {processed} processed, {len(found)} found, {blank} still blank")
    except Exception as e:
        logger.error(f"[Migration] qualified-blank pass failed: {e}")


# ── CH Watch: streaming-aligned catch-up over pipeline companies ─────────────
# Cloud Scheduler hits this every 2 days with the shared token. For every
# company in an active stage with a CH number, new filings since the last
# check are classified into signals; accounts filings trigger a re-parse via
# the existing SmartEnrich logic on the next manual run (flagged in activity).

WATCH_STAGES = {"Qualified", "Contacted", "Responded", "Meeting", "DD", "Offer"}


def _ch_watch_sweep(limit: int = 80):
    """
    One bounded sweep: the `limit` least-recently-watched pipeline companies.
    Daily scheduled runs rotate through the whole pipeline (each company
    stores ch_watched_at), so every company is covered every few days while
    each individual run stays fast and well inside request timeouts.
    """
    from services.companies_house_service import (get_filings_since, get_company_health,
                                                  get_sh01_allottees, get_officer_network)
    from google.cloud import bigquery as bq_lib
    from datetime import datetime, timedelta, timezone

    default_since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    alerts, watched, stamped = [], 0, []
    officer_checks = 0  # bounded per sweep: CH rate limit is 600/5min
    eligible = [c for c in bq_handler.get_universe()
                if c.get("status") in WATCH_STAGES and c.get("ch_company_number")
                and c.get("source") != "Internal Test"]
    # Stalest first; never-watched companies lead the queue
    eligible.sort(key=lambda c: c.get("ch_watched_at") or "")
    for c in eligible:
        watched += 1
        if watched > limit:
            watched = limit
            break
        since = (c.get("ch_watched_at") or default_since)[:10]
        try:
            filings = get_filings_since(c["ch_company_number"], since)
            notes = []
            for f in filings:
                cat, desc = f.get("category", ""), f.get("description", "")
                if cat == "accounts":
                    notes.append(f"NEW ACCOUNTS filed {f['date']} — run SmartEnrich to pull fresh financials")
                elif cat == "capital" or "allotment" in desc:
                    notes.append(f"CAPITAL EVENT {f['date']}: {desc} — someone may be investing; check SH01")
                elif cat in ("insolvency", "gazette") or "strike" in desc or "dissolution" in desc:
                    notes.append(f"RED FLAG {f['date']}: {desc}")
                elif cat in ("resolution", "constitution") or "articles" in desc:
                    notes.append(f"RESOLUTION/ARTICLES {f['date']}: {desc} — possible funding round closing")
                elif cat == "officers":
                    notes.append(f"Officer change {f['date']}: {desc}")
            if notes:
                for n in notes[:4]:
                    bq_handler.add_activity_note(c["name"], f"CH Watch: {n}", "ch-watch")
                alerts.append(f"{c['name']}: {len(notes)} new filing(s)")
                # Red-flag companies also refresh their distress fields
                if any(n.startswith("RED FLAG") for n in notes):
                    health = get_company_health(c["ch_company_number"])
                    bq_handler.client.query(
                        f"""UPDATE `{bq_handler.table_id}` SET ch_insolvency_summary = @s,
                            ch_accounts_overdue = @o WHERE name = @name""",
                        job_config=bq_lib.QueryJobConfig(query_parameters=[
                            bq_lib.ScalarQueryParameter("s", "STRING", health["ch_insolvency_summary"]),
                            bq_lib.ScalarQueryParameter("o", "BOOL", health["ch_accounts_overdue"]),
                            bq_lib.ScalarQueryParameter("name", "STRING", c["name"]),
                        ])).result()
                # CAPITAL EVENT → parse the SH01 for allottee names (the
                # newest investors, fresher than the last CS01)
                if any("CAPITAL EVENT" in n for n in notes):
                    try:
                        stored = json.loads(c.get("ch_allottees") or "{}").get("date", "")
                    except Exception:
                        stored = ""
                    sh01 = get_sh01_allottees(c["ch_company_number"], c["name"], stored_date=stored)
                    if sh01.get("allottees"):
                        bq_handler.client.query(
                            f"""UPDATE `{bq_handler.table_id}` SET ch_allottees = @a WHERE name = @name""",
                            job_config=bq_lib.QueryJobConfig(query_parameters=[
                                bq_lib.ScalarQueryParameter("a", "STRING", json.dumps(sh01)),
                                bq_lib.ScalarQueryParameter("name", "STRING", c["name"]),
                            ])).result()
                        nm = ", ".join(a["name"] for a in sh01["allottees"][:4])
                        bq_handler.add_activity_note(
                            c["name"], f"CH Watch: SH01 {sh01['date']} names allottees: {nm}", "ch-watch")

            # Officer appointment network: fund partners / serial angels on the
            # board. Refreshed at most every 60 days, bounded per sweep.
            try:
                _net_checked = json.loads(c.get("ch_officer_network") or "{}").get("checked_at", "")
            except Exception:
                _net_checked = ""
            needs_network = _net_checked < (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
            if needs_network and officer_checks < 20:
                officer_checks += 1
                try:
                    net = get_officer_network(c["ch_company_number"], exclude_names=[c.get("contact_name") or ""])
                    bq_handler.client.query(
                        f"""UPDATE `{bq_handler.table_id}` SET ch_officer_network = @n WHERE name = @name""",
                        job_config=bq_lib.QueryJobConfig(query_parameters=[
                            bq_lib.ScalarQueryParameter("n", "STRING", json.dumps(net)),
                            bq_lib.ScalarQueryParameter("name", "STRING", c["name"]),
                        ])).result()
                    if net.get("officers"):
                        for o in net["officers"][:3]:
                            bq_handler.add_activity_note(
                                c["name"],
                                f"CH Watch: director {o['name']} holds {o['other_seats']} other active board seats "
                                f"(likely investor-affiliated): {', '.join(o['companies'][:4])}", "ch-watch")
                except Exception as e:
                    logger.warning(f"[CH Watch] officer network failed for {c.get('name')}: {e}")
            stamped.append(c["name"])
        except Exception as e:
            logger.warning(f"[CH Watch] failed for {c.get('name')}: {e}")

    # ONE batched stamp for the whole run — per-company BigQuery DML is
    # seconds each and was the reason sweeps took minutes.
    if stamped:
        try:
            bq_handler.client.query(
                f"""UPDATE `{bq_handler.table_id}` SET ch_watched_at = @d
                    WHERE name IN UNNEST(@names)""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("d", "STRING", today),
                    bq_lib.ArrayQueryParameter("names", "STRING", stamped),
                ])).result()
        except Exception as e:
            logger.warning(f"[CH Watch] batch stamp failed: {e}")

    logger.info(f"[CH Watch] {watched} companies checked, {len(alerts)} with new filings"
                + (f": {'; '.join(alerts[:10])}" if alerts else ""))
    try:
        bq_handler._log_activity("__system__", "note", "ch-watch",
                                 note_text=f"CH Watch sweep: {watched} companies checked, {len(alerts)} with new filings")
    except Exception:
        pass
    return {"status": "Success", "watched": watched, "eligible": len(eligible), "alerts": alerts}


@app.post("/ch-watch/run")
async def ch_watch_run(request: Request):
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid watch token.")
    # Synchronous but bounded (~80 companies ≈ 1-2 minutes worst case):
    # Cloud Run throttles CPU after the response, so background threads stall.
    result = _ch_watch_sweep()
    # Daily investor-mining refresh rides the same scheduled run, so newly
    # Qualified companies feed the LP database with zero extra setup.
    try:
        result = dict(result or {})
        result["investor_mining"] = _run_investor_mining()
    except Exception as e:
        logger.warning(f"[CH Watch] investor mining refresh failed (non-fatal): {e}")
    # Fridays: weekly source refresh — every built-in scraper + every saved
    # AI source re-runs; only NEW companies are ingested (merge-only).
    try:
        from datetime import datetime as _dt, timezone as _tz
        if _dt.now(_tz.utc).weekday() == 4:
            result["weekly_source_refresh"] = _weekly_source_refresh()
    except Exception as e:
        logger.warning(f"[CH Watch] weekly source refresh failed (non-fatal): {e}")
    # Daily analytics: sync the immutable stage ledger + write today's snapshot
    # so funnel trends keep recording even if nobody opens the Analytics page.
    try:
        from services import analytics_service
        analytics_service.ledger_sync(bq_handler)
        analytics_service.write_snapshot(bq_handler, analytics_service.compute_stats(bq_handler))
        result["analytics_snapshot"] = "written"
    except Exception as e:
        logger.warning(f"[CH Watch] analytics snapshot failed (non-fatal): {e}")
    return result or {"status": "Success"}


# ── Local pre-qualification: zero-AI hard-filter triage (REJECT-ONLY) ───────
# Rows that arrive with complete data (geography + industry + revenue — e.g.
# Inven uploads) don't need an AI call to run the 3 hard filters: the local
# rule-based qualifier rejects clear failures instantly (Not a Fit +
# unfit_reason). PASSERS ARE NOT MOVED — they stay Uploaded/Scraped until
# SmartFill computes the fit score, because nothing may enter the Qualified
# kanban unscored. Thin rows are skipped entirely.

def _prequalify_local(only_names: set = None) -> dict:
    from google.cloud import bigquery as bq_lib
    from ai.criteria import qualify_company

    def _complete(c) -> bool:
        has_geo = bool(c.get("hq_country") or c.get("region"))
        has_industry = bool(c.get("sector")) or len(c.get("description") or "") > 40
        has_revenue = bool(c.get("revenue_y1") or c.get("revenue_m"))
        return has_geo and has_industry and has_revenue

    qualified, rejected = [], {}  # rejected: reason -> [names]
    examined = 0
    for c in bq_handler.get_universe():
        if c.get("status") not in ("Uploaded", "Scraped") or c.get("source") == "Internal Test":
            continue
        if only_names is not None and c.get("name") not in only_names:
            continue
        if not _complete(c):
            continue
        examined += 1
        verdict = qualify_company(c)
        if verdict["qualified"]:
            qualified.append(c["name"])
        else:
            rejected.setdefault(verdict["reason"][:180], []).append(c["name"])

    # Reject-only: passers keep their current status — SmartFill must score
    # them before they can appear in the Qualified kanban.
    for reason, names in rejected.items():
        bq_handler.client.query(
            f"""UPDATE `{bq_handler.table_id}` SET
                status = 'Not a Fit', unfit_reason = @reason
                WHERE name IN UNNEST(@names) AND status IN ('Uploaded', 'Scraped')""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("reason", "STRING", f"Pre-qualification (local data): {reason}"),
                bq_lib.ArrayQueryParameter("names", "STRING", names)])).result()

    n_rejected = sum(len(v) for v in rejected.values())
    logger.info(f"[Prequalify] {examined} examined: {len(qualified)} passed (left for SmartFill), "
                f"{n_rejected} not a fit (zero AI calls)")
    return {"status": "Success", "examined": examined, "passed_awaiting_smartfill": len(qualified),
            "not_a_fit": n_rejected, "ai_calls": 0}


@app.get("/prequalify/run")
@app.post("/prequalify/run")
async def prequalify_run(request: Request):
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return _prequalify_local()


# ── Investor mining v2 + connection layer ───────────────────────────────────

def _run_investor_mining() -> dict:
    """
    Sweep Qualified-and-later companies, extract their investors from every
    stored source (CH cap tables, PitchBook, Inven), persist LP profiles
    (typed, deduped, provenance kept) and the investor_links edge table.
    Idempotent: safe to re-run daily; snapshot semantics per company.
    """
    from services.investor_miner import mine_companies, _canonical_key

    dialogue = ("Qualified", "Contacted", "Responded", "Meeting", "DD", "Offer", "Won")
    companies = [c for c in bq_handler.get_universe()
                 if c.get("status") in dialogue and c.get("source") != "Internal Test"]
    result = mine_companies(companies)

    links_saved, link_companies = 0, len(result["per_company"])
    try:
        links_saved = investor_handler.save_links_bulk(result["per_company"])
    except Exception as e:
        logger.warning(f"[InvestorMiner] bulk link save failed: {e}")

    # Upsert LP profiles: new ones inserted, existing ones get their
    # portfolio overlap (source_companies) merged — never overwritten.
    existing_map = {}
    for r in investor_handler.get_all():
        nm = (r.get("name") or "").strip()
        if nm:
            existing_map[nm.lower()] = r
            existing_map.setdefault(_canonical_key(nm), r)

    new_investors, merge_pairs = [], []
    for key, p in result["investors"].items():
        row = existing_map.get(p["name"].strip().lower()) or existing_map.get(key)
        joined = ", ".join(sorted(p["companies"]))
        if row:
            old = {x.strip() for x in (row.get("source_companies") or "").split(",") if x.strip()}
            merged = old | p["companies"]
            if merged != old:
                merge_pairs.append({"key": (row.get("name") or "").strip().lower(),
                                    "source_companies": ", ".join(sorted(merged))})
        else:
            new_investors.append({
                "name": p["name"], "investor_type": p["investor_type"],
                "website": p.get("website") or "", "source": "Portfolio mining v2",
                "source_companies": joined,
                "notes": f"Mined from universe portfolio overlap ({len(p['companies'])} company/ies).\n",
            })

    inserted = investor_handler.save_investors(new_investors)
    merged_n = investor_handler.merge_source_companies(merge_pairs) if merge_pairs else 0

    # Auto reverse-enrichment: newly discovered institutional investors get an
    # immediate CH lookup — officers (principals to contact), PSC (who is
    # behind the vehicle: UHNWI discovery), filed net assets (AUM proxy).
    # Bounded per run; the rest are picked up on subsequent daily runs.
    reverse_enriched = 0
    try:
        from ai.investor_fill import ch_enrich_investor
        from google.cloud import bigquery as bq_lib
        for inv in new_investors:
            if reverse_enriched >= 12:
                break
            if inv.get("investor_type") in ("Angel",):  # individuals: nothing to look up
                continue
            info = ch_enrich_investor(inv["name"])
            if not (info.get("psc_summary") or info.get("officers_summary") or info.get("net_assets_m") is not None):
                continue
            reverse_enriched += 1
            bq_handler.client.query(
                f"""UPDATE `{investor_handler.table_id}` SET
                    psc_summary = IFNULL(NULLIF(@psc, ''), psc_summary),
                    officers_summary = IFNULL(NULLIF(@off, ''), officers_summary),
                    net_assets_m = IFNULL(@na, net_assets_m),
                    contact_name = IFNULL(NULLIF(contact_name, ''), @principal),
                    updated_at = CURRENT_TIMESTAMP()
                    WHERE LOWER(name) = LOWER(@name)""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("psc", "STRING", info.get("psc_summary") or ""),
                    bq_lib.ScalarQueryParameter("off", "STRING", info.get("officers_summary") or ""),
                    bq_lib.ScalarQueryParameter("na", "FLOAT64", info.get("net_assets_m")),
                    bq_lib.ScalarQueryParameter("principal", "STRING", info.get("principal_name") or ""),
                    bq_lib.ScalarQueryParameter("name", "STRING", inv["name"]),
                ])).result()
    except Exception as e:
        logger.warning(f"[InvestorMiner] reverse enrichment failed (non-fatal): {e}")

    summary = {"status": "Success", "companies_scanned": len(companies),
               "companies_with_investors": link_companies, "links_saved": links_saved,
               "new_investors": inserted, "overlaps_merged": merged_n,
               "reverse_enriched": reverse_enriched}
    logger.info(f"[InvestorMiner] {summary}")
    return summary


@app.post("/investors/mine-all")
async def investors_mine_all():
    """Mine investors of all Qualified+ companies into the LP database +
    connection layer. Streams heartbeats while working (hostile networks kill
    silent connections); the final line is the JSON summary."""
    import asyncio as _asyncio
    import json as _json
    from fastapi.responses import StreamingResponse

    async def _gen():
        task = _asyncio.create_task(_asyncio.to_thread(_run_investor_mining))
        while not task.done():
            await _asyncio.sleep(10)
            yield " "
        try:
            res = task.result()
        except Exception as e:
            logger.error(f"[InvestorMiner] run failed: {e}")
            res = {"status": "Error", "detail": str(e)}
        yield "\n" + _json.dumps(res)

    return StreamingResponse(_gen(), media_type="text/plain")


@app.get("/investor-mine/run")
async def investor_mine_run(request: Request):
    """Token-gated trigger (scheduler / ops) for the daily investor mining refresh."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return _run_investor_mining()


@app.get("/analytics")
async def get_analytics(refresh: int = Query(0, description="1 = force ledger sync + fresh snapshot")):
    """Retention-proof pipeline analytics: ever vs current per stage (from the
    immutable analytics_ledger, survives deletions), response rate, weekly
    email volume and the daily snapshot trend series."""
    from services import analytics_service
    stats = analytics_service.refresh_and_stats(bq_handler, force=bool(refresh))
    if not stats or (not stats.get("stored_ever") and not stats.get("stored_current")):
        # Still return the shape; frontend shows an empty state
        stats.setdefault("funnel", [])
        stats.setdefault("snapshots", [])
    return stats


@app.post("/admin/analytics/ledger-rebuild")
async def analytics_ledger_rebuild(request: Request,
                                   dry_run: int = Query(1, description="1 = preview only (default), 0 = apply")):
    """Recompute every analytics fact from evidence.

    The ledger is a DERIVED CACHE of conclusions, not a store of primary data, and
    it is append-only — so a conclusion that turns out false becomes permanent.
    That happened: while the stage-rename migration wrongly held 18 companies in
    Responded, the ledger banked 'ever reached Responded' for each, and correcting
    the live rows could not correct the history.

    This deletes and re-derives facts ONLY for companies still present in targets.
    Facts for companies that have since been deleted or renamed away are preserved
    untouched, which is the entire reason the ledger exists. Primary sources
    (targets, activity_log, email_log) are never written to.

    Defaults to a PREVIEW reporting the delta per event. A negative delta is a
    fact the evidence does not support.
    """
    _require_token(request)
    from services import analytics_service
    return _stream_json(lambda: analytics_service.ledger_rebuild(bq_handler, bool(dry_run)))


@app.get("/connections/company/{company_name}")
async def company_connections(company_name: str):
    """Investors of a company + sibling companies sharing any investor."""
    return investor_handler.get_company_connections(company_name)


@app.get("/connections/investor/{investor_name}")
async def investor_connections(investor_name: str):
    """Portfolio companies of an investor + co-investors met through them."""
    return investor_handler.get_investor_connections(investor_name)


# ── AI Source Agent ──────────────────────────────────────────────────────────
# Paste a URL, the AI reads the page and extracts the company list; preview
# first, confirm to ingest. NOT code generation — one universal pipeline.

class SourcePreviewRequest(BaseModel):
    url: str
    kind: Optional[str] = "companies"  # or "investors" (LPs)

class SourceConfirmRequest(BaseModel):
    url: str
    label: str
    companies: List[Dict]
    kind: Optional[str] = "companies"


def _stream_json(work_fn):
    """Heartbeat-streamed response wrapper (hostile networks kill silent
    connections): spaces every 10s, final line = JSON result."""
    import asyncio as _asyncio
    import json as _json
    from fastapi.responses import StreamingResponse

    async def _gen():
        task = _asyncio.create_task(_asyncio.to_thread(work_fn))
        while not task.done():
            await _asyncio.sleep(10)
            yield " "
        try:
            res = task.result()
        except Exception as e:
            logger.error(f"[SourceAgent] streamed work failed: {e}")
            res = {"status": "Error", "detail": str(e)}
        yield "\n" + _json.dumps(res)
    return StreamingResponse(_gen(), media_type="text/plain")


@app.post("/sources/preview")
async def source_preview(req: SourcePreviewRequest):
    """Analyze a URL: fetch, AI-read, paginate (bounded). Persists NOTHING."""
    if not (req.url or "").strip():
        raise HTTPException(status_code=400, detail="Empty URL.")
    from services.source_agent import extract_source
    kind = req.kind or "companies"
    return _stream_json(lambda: extract_source(req.url, kind=kind))


def _ingest_source_companies(url: str, label: str, companies: list) -> dict:
    """Save reviewed companies (merge-never-overwrite) + register the source."""
    from google.cloud import bigquery as bq_lib
    rows = []
    for c in companies:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        rows.append({"name": name, "website": c.get("website") or "",
                     "description": c.get("description") or "",
                     "source": label, "status": "Scraped", "match_score": 0.0})
    if not rows:
        return {"status": "Success", "found": 0, "added": 0}
    # How many are genuinely new (save_targets merges existing silently)
    names = [r["name"] for r in rows]
    existing = {r.name for r in bq_handler.client.query(
        f"SELECT name FROM `{bq_handler.table_id}` WHERE name IN UNNEST(@names)",
        job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ArrayQueryParameter("names", "STRING", names)])).result()}
    added = len([n for n in names if n not in existing])
    bq_handler.save_targets(rows)
    bq_handler.upsert_ai_source(url, label)
    bq_handler.stamp_ai_source(url, found=len(rows), added=added)
    logger.info(f"[SourceAgent] '{label}': {len(rows)} found, {added} new ingested")
    return {"status": "Success", "found": len(rows), "added": added, "label": label}


def _ingest_source_investors(url: str, label: str, investors: list) -> dict:
    """Save reviewed INVESTORS into the LP database (dedup by name) + register the source."""
    rows = []
    for c in investors:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        desc = (c.get("description") or "")
        if c.get("hq_location"):
            desc = (desc + f" HQ: {c['hq_location']}.").strip()
        rows.append({"name": name,
                     "investor_type": c.get("investor_type") or "Unknown",
                     "website": c.get("website") or "",
                     "contact_name": c.get("contact_name") or "",
                     "description": desc[:1000],
                     "source": label, "status": "Identified"})
    if not rows:
        return {"status": "Success", "found": 0, "added": 0, "label": label}
    added = investor_handler.save_investors(rows)  # dedups by name internally
    bq_handler.upsert_ai_source(url, label, kind="investors")
    bq_handler.stamp_ai_source(url, found=len(rows), added=added)
    logger.info(f"[SourceAgent/LP] '{label}': {len(rows)} found, {added} new LPs ingested")
    return {"status": "Success", "found": len(rows), "added": added, "label": label}


@app.post("/sources/confirm")
async def source_confirm(req: SourceConfirmRequest):
    """Ingest the previewed (and possibly pruned) list — companies or investors."""
    url, label = req.url.strip(), (req.label or "AI Source").strip()
    if (req.kind or "companies") == "investors":
        return _ingest_source_investors(url, label, req.companies or [])
    return _ingest_source_companies(url, label, req.companies or [])


@app.get("/sources/list")
async def sources_list(kind: str = Query("", description="Filter: companies | investors")):
    return {"sources": bq_handler.list_ai_sources(kind=kind)}


def _refresh_ai_source(url: str, label: str = "", kind: str = "companies") -> dict:
    """Re-extract a saved source and auto-ingest NEW entities only."""
    from services.source_agent import extract_source
    result = extract_source(url, kind=kind)
    lbl = label or result.get("title") or "AI Source"
    if kind == "investors":
        out = _ingest_source_investors(url, lbl, result.get("companies") or [])
    else:
        out = _ingest_source_companies(url, lbl, result.get("companies") or [])
    out["warnings"] = result.get("warnings") or []
    return out


@app.post("/sources/refresh")
async def source_refresh(req: SourcePreviewRequest):
    src = next((s for s in bq_handler.list_ai_sources() if s["url"] == req.url.strip()), None)
    if not src:
        raise HTTPException(status_code=404, detail="Source not registered — add it first.")
    return _stream_json(lambda: _refresh_ai_source(src["url"], src["label"], kind=src.get("kind") or "companies"))


def _weekly_source_refresh() -> dict:
    """Friday job: re-run every built-in scraper AND every saved AI source.
    New companies auto-ingest (merge-only); everything else untouched.

    ch_sic_scraper is in this loop, but unlike its siblings its get_supported_
    sources()/scrape_source() runs a BOUNDED weekly batch (a few SIC codes,
    not all 16) — see ch_sic_scraper.py's _WEEKLY_BATCH_SIZE. This whole job
    runs synchronously inside /ch-watch/run, which is explicitly budgeted
    against Cloud Run's request timeout elsewhere in this file; a full
    16-code pull risked tipping that shared budget. The full pull is still
    one click away via the streamed /ingest/ch-sic endpoint, which has no
    such constraint."""
    summary = {"builtin": {}, "ai_sources": {}}
    for scraper in (market_scraper, conf_scraper, rank_scraper, directory_scraper, network_scraper, ch_sic_scraper):
        try:
            for src_name in scraper.get_supported_sources():
                try:
                    raw = scraper.scrape_source(src_name)
                    for c in raw or []:
                        c["source"] = c.get("source", src_name)
                        c["status"] = "Scraped"
                        c["match_score"] = 0.0
                    if raw:
                        bq_handler.save_targets(raw)
                    summary["builtin"][src_name] = len(raw or [])
                except Exception as e:
                    summary["builtin"][src_name] = f"failed: {e}"
        except Exception as e:
            logger.warning(f"[WeeklyRefresh] scraper enumeration failed: {e}")
    for s in bq_handler.list_ai_sources():
        if (s.get("status") or "active") != "active":
            continue
        try:
            r = _refresh_ai_source(s["url"], s["label"], kind=s.get("kind") or "companies")
            summary["ai_sources"][s["label"]] = f"{r.get('found', 0)} found, {r.get('added', 0)} new ({s.get('kind') or 'companies'})"
        except Exception as e:
            summary["ai_sources"][s["label"]] = f"failed: {e}"
    try:
        bq_handler._log_activity("__system__", "note", "weekly-refresh",
                                 note_text=f"Weekly source refresh: {json.dumps(summary)[:900]}")
    except Exception:
        pass
    logger.info(f"[WeeklyRefresh] {summary}")
    return summary


# ── Smart Upload (AI) — any CSV / Excel / PDF into the master universe ──────

class SmartUploadConfirmRequest(BaseModel):
    label: str
    companies: List[Dict]
    kind: Optional[str] = "companies"  # or "investors" (LPs)


@app.post("/upload/smart/preview")
async def smart_upload_preview(file: UploadFile = File(...),
                               kind: str = Query("companies", description="companies | investors")):
    """Analyze any CSV/XLSX/PDF: AI designs the column mapping (tabular) or
    extracts entities (PDF); code applies it. Persists NOTHING. Streamed."""
    from services.smart_upload import smart_parse
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25MB).")
    fname = file.filename or "upload"
    k = kind if kind in ("companies", "investors") else "companies"
    return _stream_json(lambda: smart_parse(data, fname, kind=k))


def _smart_confirm_investors(label: str, investors: list) -> dict:
    """LP flavour: previewed rows -> investors table (name-dedup insert),
    extra_data filled only where empty."""
    from google.cloud import bigquery as bq_lib
    rows, extras = [], []
    for c in investors:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        row = {k: v for k, v in c.items() if k != "extra_data" and v not in (None, "")}
        row["name"] = name
        row["source"] = label
        row["status"] = "Identified"
        rows.append(row)
        if c.get("extra_data"):
            extras.append({"k": name, "x": str(c["extra_data"])[:4000]})
    if not rows:
        return {"status": "Success", "found": 0, "added": 0, "label": label}
    added = investor_handler.save_investors(rows)
    if extras:
        try:
            bq_handler.client.query(
                f"""UPDATE `{investor_handler.table_id}` T SET extra_data = (
                        SELECT JSON_EXTRACT_SCALAR(j, '$.x')
                        FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j
                        WHERE JSON_EXTRACT_SCALAR(j, '$.k') = T.name LIMIT 1)
                    WHERE (T.extra_data IS NULL OR T.extra_data = '') AND T.name IN (
                        SELECT JSON_EXTRACT_SCALAR(j, '$.k')
                        FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j)""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("payload", "STRING", json.dumps(extras)),
                ])).result()
        except Exception as e:
            logger.warning(f"[SmartUpload/LP] extra_data update failed (non-fatal): {e}")
    logger.info(f"[SmartUpload/LP] '{label}': {len(rows)} rows, {added} new investors")
    return {"status": "Success", "found": len(rows), "added": added, "label": label,
            "message": f"Ingested {len(rows)} investors from '{label}' — {added} new "
                       f"(existing names untouched). Use InvestorFill to research and score."}


@app.post("/upload/smart/confirm")
async def smart_upload_confirm(req: SmartUploadConfirmRequest):
    """Ingest the previewed rows (merge-never-overwrite). extra_data is
    filled only where empty — uploads never overwrite stored extras."""
    from google.cloud import bigquery as bq_lib
    label = (req.label or "Smart Upload").strip()[:80]
    if (req.kind or "companies") == "investors":
        return _smart_confirm_investors(label, req.companies or [])
    rows, extras = [], []
    for c in req.companies or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        row = {k: v for k, v in c.items() if k != "extra_data" and v not in (None, "")}
        row["name"] = name
        row["source"] = label
        row["status"] = "Uploaded"
        row["match_score"] = 0.0
        rows.append(row)
        if c.get("extra_data"):
            extras.append({"k": name, "x": str(c["extra_data"])[:4000]})
    if not rows:
        return {"status": "Success", "found": 0, "added": 0}
    names = [r["name"] for r in rows]
    existing = {r.name for r in bq_handler.client.query(
        f"SELECT name FROM `{bq_handler.table_id}` WHERE name IN UNNEST(@names)",
        job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ArrayQueryParameter("names", "STRING", names)])).result()}
    added = len([n for n in names if n not in existing])
    bq_handler.save_targets(rows)
    if extras:
        try:
            bq_handler.client.query(
                f"""UPDATE `{bq_handler.table_id}` T SET extra_data = (
                        SELECT JSON_EXTRACT_SCALAR(j, '$.x')
                        FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j
                        WHERE JSON_EXTRACT_SCALAR(j, '$.k') = T.name LIMIT 1)
                    WHERE (T.extra_data IS NULL OR T.extra_data = '') AND T.name IN (
                        SELECT JSON_EXTRACT_SCALAR(j, '$.k')
                        FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j)""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("payload", "STRING", json.dumps(extras)),
                ])).result()
        except Exception as e:
            logger.warning(f"[SmartUpload] extra_data update failed (non-fatal): {e}")
    logger.info(f"[SmartUpload] '{label}': {len(rows)} rows, {added} new companies")
    return {"status": "Success", "found": len(rows), "added": added, "label": label,
            "message": f"Ingested {len(rows)} rows from '{label}' — {added} new companies "
                       f"(existing ones gap-filled only). Use SmartFill to qualify and enrich."}


# ── Follow-up reminders ──────────────────────────────────────────────────────

@app.get("/followups")
async def get_followups(days: int = Query(14, description="'Waiting on them' threshold (our last email unanswered)"),
                        reply_days: int = Query(7, description="'We owe a reply' threshold (their email unanswered by us)")):
    """
    The follow-up queue, both directions, from email_log (single source of truth).

    THE AGREED THRESHOLDS:
      - waiting_on_them: WE sent the last email and they have not answered for
        14+ days. If their autoresponder gave a return date more than 14 days out,
        the reminder moves to that date + 1 instead (see the due_at expression
        below); anything shorter, absent or already past keeps the 14 days.
      - we_owe_reply: THEY sent the last email and we have not replied for 7+
        days. This is the Responded-stage rule, and one condition deliberately
        covers both halves of it: a company that replied and has heard nothing
        from us since necessarily has their message as the last one. Companies
        deliberately parked (do-not-respond / declined action buckets) are
        excluded — intentional silence never nags.

    An out-of-office is never treated as their reply, so it cannot make it look
    like the ball is with us. Active outreach stages only; longest silence first;
    we-owe items lead.
    """
    from google.cloud import bigquery as bq_lib
    try:
        email_table = bq_handler._ensure_email_log_table()
        rows = bq_handler.client.query(f"""
            WITH msgs AS (
                SELECT entity_name, direction, subject, snippet, counterparty_email, sent_at,
                       IFNULL(classification, '') = 'out_of_office' AS is_ooo
                FROM `{email_table}`
                WHERE entity_type = 'company'
            ),
            -- Our last outbound. The follow-up clock runs from HERE, never from
            -- an autoresponder that happened to arrive afterwards.
            last_sent AS (
                SELECT * EXCEPT(rn) FROM (
                    SELECT entity_name, subject, snippet, counterparty_email, sent_at,
                           ROW_NUMBER() OVER (PARTITION BY entity_name ORDER BY sent_at DESC) AS rn
                    FROM msgs WHERE direction = 'sent'
                ) WHERE rn = 1
            ),
            -- Their last GENUINE inbound. Out-of-office replies are excluded:
            -- an autoresponder does not mean we owe anybody an answer.
            last_recv AS (
                SELECT * EXCEPT(rn) FROM (
                    SELECT entity_name, subject, snippet, counterparty_email, sent_at,
                           ROW_NUMBER() OVER (PARTITION BY entity_name ORDER BY sent_at DESC) AS rn
                    FROM msgs WHERE direction = 'received' AND NOT is_ooo
                ) WHERE rn = 1
            ),
            calc AS (
                SELECT t.name, t.status, t.contact_name, t.averroes_fit_score,
                       t.action_bucket, t.track, NULLIF(t.ooo_until, '') AS ooo_until, t.ooo_note,
                       s.sent_at AS last_sent_at, s.subject AS sent_subject,
                       s.snippet AS sent_snippet, s.counterparty_email AS sent_to,
                       r.sent_at AS last_recv_at, r.subject AS recv_subject,
                       r.snippet AS recv_snippet, r.counterparty_email AS recv_from,
                       -- The agreed rule, verbatim:
                       --   length = days from our email to their return date
                       --   if length > 14: remind at length + 1 days
                       --   else:           remind at 14 days
                       -- The comparison is STRICTLY greater than @days, so a
                       -- return date exactly 14 days out does NOT override the
                       -- base rule. (A plain GREATEST() would, and did — it
                       -- effectively triggers from length > 13.) Anything
                       -- shorter, absent or already past keeps the 14 days.
                       IF(SAFE.PARSE_DATE('%Y-%m-%d', NULLIF(t.ooo_until, '')) IS NOT NULL
                          AND DATE_DIFF(SAFE.PARSE_DATE('%Y-%m-%d', NULLIF(t.ooo_until, '')),
                                        DATE(s.sent_at), DAY) > @days,
                          TIMESTAMP(DATE_ADD(SAFE.PARSE_DATE('%Y-%m-%d', NULLIF(t.ooo_until, '')),
                                             INTERVAL 1 DAY)),
                          TIMESTAMP_ADD(s.sent_at, INTERVAL @days DAY)
                       ) AS due_at
                FROM `{bq_handler.table_id}` t
                JOIN last_sent s ON s.entity_name = t.name
                LEFT JOIN last_recv r ON r.entity_name = t.name
                WHERE t.status IN ('Contacted', 'Responded', 'Meeting', 'DD', 'Offer')
                  AND IFNULL(t.source, '') != 'Internal Test'
            )
            SELECT name, status, contact_name, averroes_fit_score, action_bucket,
                   ooo_until, ooo_note,
                   CAST(due_at AS STRING) AS due_at,
                   TIMESTAMP_DIFF(due_at, last_sent_at, DAY) AS threshold_days,
                   IF(owed, 'we_owe_reply', 'waiting_on_them') AS type,
                   IF(owed, recv_subject, sent_subject) AS subject,
                   IF(owed, recv_snippet, sent_snippet) AS snippet,
                   IF(owed, recv_from, sent_to) AS counterparty_email,
                   CAST(IF(owed, last_recv_at, last_sent_at) AS STRING) AS last_email_at,
                   TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), IF(owed, last_recv_at, last_sent_at), DAY) AS days_waiting
            FROM (SELECT *, last_recv_at IS NOT NULL AND last_recv_at > last_sent_at AS owed FROM calc)
            WHERE (
                -- THE BALL IS WITH US: they answered and we have gone quiet.
                -- In Responded this is the 7-day rule. It covers both of the
                -- cases that sound different but are not: "we never sent
                -- anything since they replied" and "they sent the last email and
                -- we have not answered". If they replied and we have not written
                -- since, their message IS the last one, so one condition catches
                -- both. Companies deliberately parked (declined / do-not-respond)
                -- are excluded: intentional silence must never nag.
                (owed
                 AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), last_recv_at, DAY) >= @reply_days
                 -- Deliberate silence never nags: parked action buckets, Not
                 -- interested (kill) and Talk later. All keep status Responded
                 -- (they did reply; parking is our decision), so without this
                 -- they would reappear here every 7 days forever. Talk-later
                 -- companies resurface on the Responded page after 6 months
                 -- instead, which is the agreed re-engagement route.
                 AND IFNULL(action_bucket, '') NOT IN ('not_fit_no_respond', 'declined_close')
                 AND IFNULL(track, '') NOT IN ('kill', 'later'))
                OR
                -- THE BALL IS WITH THEM: silence since our email, reminder due.
                -- Contacted = 14 days, overridden by the out-of-office rule above.
                (NOT owed AND CURRENT_TIMESTAMP() >= due_at)
            )
            ORDER BY owed DESC, days_waiting DESC""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("days", "INT64", max(1, min(days, 365))),
                bq_lib.ScalarQueryParameter("reply_days", "INT64", max(1, min(reply_days, 365))),
            ])).result()
        items = [dict(r) for r in rows]
        owe = sum(1 for i in items if i["type"] == "we_owe_reply")
        deferred = sum(1 for i in items if (i.get("threshold_days") or 0) > days)
        return {"days_threshold": days, "reply_days_threshold": reply_days,
                "count": len(items), "we_owe_count": owe,
                "ooo_deferred_count": deferred, "followups": items}
    except Exception as e:
        logger.warning(f"Follow-up query failed: {e}")
        return {"days_threshold": days, "count": 0, "followups": [], "error": str(e)}


# ── Deep diagnostic: everything we read for ONE company, step by step ───────
# Token-gated (NOT unauthenticated — the last "temporary" diag endpoint's
# removal accidentally took /chat with it; this one is permanent and safe).

@app.get("/diag/verify-email")
async def diag_verify_email(request: Request, emails: str = Query(..., description="comma-separated, max 10")):
    """Token-gated one-off email verification via the same Hunter.io verifier
    the contact finder uses. Ops tool: never stores anything."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    # Reports the FAILURE MODE, not just the verdict. A rejected or exhausted
    # key used to look identical to a genuinely unclear mailbox ('unknown'),
    # which hid a dead verifier and silently disabled the guessing rungs.
    from services.contact_finder import verify_email_detail
    out = {"results": {}, "verifier_working": None}
    statuses = []
    for e in [x.strip() for x in emails.split(",") if x.strip()][:10]:
        d = verify_email_detail(e)
        statuses.append(d["status"])
        out["results"][e] = d
    if statuses and all(s == "unavailable" for s in statuses):
        out["verifier_working"] = False
        out["note"] = "No verifier key configured on this service (set HUNTER_API_KEY with --update-env-vars)."
    elif any(s == "error" for s in statuses):
        out["verifier_working"] = False
        first = next(d for d in out["results"].values() if d["status"] == "error")
        out["note"] = (f"Hunter rejected the call (HTTP {first['http']}): {first['detail']}. "
                       "Until this is fixed, the pattern-guess rungs of the contact waterfall "
                       "cannot confirm anything and the ladder falls back to published addresses.")
    elif statuses:
        out["verifier_working"] = True
        out["note"] = "Verifier answered normally."
    return out


@app.get("/diag/source-counts")
async def diag_source_counts(request: Request, table: str = Query("targets", description="targets | investors")):
    """Token-gated: real GROUP BY source counts straight from BigQuery, for
    diagnosing the Sources overlay (which tallies client-side against a static
    registry — this is the ground truth to check it against)."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    tbl = investor_handler.table_id if table == "investors" else bq_handler.table_id
    rows = list(bq_handler.client.query(
        f"SELECT IFNULL(source, '(blank)') AS source, COUNT(*) AS n FROM `{tbl}` "
        f"GROUP BY source ORDER BY n DESC").result())
    return {"table": tbl, "counts": {r.source: r.n for r in rows}}


@app.get("/diag/ch-match-audit")
async def diag_ch_match_audit(request: Request):
    """Token-gated: re-run the (now-fixed) name gate against every already-
    matched company's stored (name, ch_official_name) pair — no CH/Gemini
    calls, just recomputing the same comparison with the tightened logic.
    Flags anything the new gate would refuse outright or accept only on a
    weaker tier than what's currently recorded, i.e. rows that may be
    carrying another company's financials/PSC/cap table. Read-only — nothing
    is changed here; use it to decide which rows are worth re-running
    SmartEnrich on."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")

    from services.companies_house_service import _core_name, _name_gate

    rows = list(bq_handler.client.query(
        f"""SELECT name, ch_official_name, ch_company_number, ch_match_confidence,
                   CAST(ch_incorporated_date AS STRING) AS ch_incorporated_date,
                   CAST(ingested_at AS STRING) AS ingested_at,
                   status, revenue_band, averroes_fit_score
            FROM `{bq_handler.table_id}`
            WHERE ch_company_number IS NOT NULL AND ch_company_number != ''""").result())

    flagged = []
    checked = 0
    for r in rows:
        if not r.ch_official_name:
            continue  # matched before ch_official_name was persisted — nothing to re-check
        checked += 1
        gate = _name_gate(r.name, r.ch_official_name)
        new_level = gate[0] if gate else "REJECTED"

        # Impossible: the register company was incorporated after we first knew
        # of this one. Strongest signal there is — it is not a judgement call.
        inc = (r.ch_incorporated_date or "")[:10]
        ing = (r.ingested_at or "")[:10]
        impossible = bool(inc and ing and inc > ing)

        # A one-word name sits inside hundreds of register names, so a match on
        # one alone is worth re-checking by hand even if the gate now allows it.
        one_word = len(_core_name(r.name).split()) < 2
        # Old confidence -> the loosest gate tier that could have produced it,
        # under the OLD scheme (fuzzy/partial were both accepted then).
        old_conf = r.ch_match_confidence or ""
        weak = new_level in ("REJECTED", "fuzzy", "partial", "core-ambiguous")
        risky_old = old_conf in ("low",) or (old_conf == "medium" and weak)

        if impossible:
            why, action = ("IMPOSSIBLE",
                           f"The register company was incorporated {inc}, AFTER we first saw this "
                           f"company on {ing}. It cannot be the same entity. Clear ch_company_number "
                           f"and re-run SmartEnrich.")
        elif new_level == "REJECTED":
            why, action = ("REJECTED",
                           "The tightened rules would not match these two names at all. Verify on "
                           "Companies House before trusting this row's financials, PSC or cap table.")
        elif weak:
            why, action = ("TOO WEAK",
                           "Now refused for financials: the names only sound similar, or match on a "
                           "single word. Verify by hand, or clear ch_company_number to force a fresh match.")
        elif one_word:
            why, action = ("ONE-WORD NAME",
                           "Matched on a single-word name. The gate accepts it, but a one-word name "
                           "sits inside many register names — worth a spot check.")
        elif risky_old:
            why, action = ("LOW CONFIDENCE", "Recorded at low confidence when it was matched.")
        else:
            continue

        flagged.append({
            "name": r.name,
            "ch_official_name": r.ch_official_name,
            "ch_company_number": r.ch_company_number,
            "stored_confidence": old_conf,
            "gate_under_new_rules": new_level,
            "ch_incorporated_date": inc,
            "first_seen": ing,
            "status": r.status,
            "revenue_band": r.revenue_band,
            "fit_score": r.averroes_fit_score,
            "flag": why,
            "suggested_action": action,
        })

    order = {"IMPOSSIBLE": 0, "REJECTED": 1, "TOO WEAK": 2, "ONE-WORD NAME": 3, "LOW CONFIDENCE": 4}
    flagged.sort(key=lambda f: (order.get(f["flag"], 9), f["name"]))
    return {
        "checked": checked,
        "total_with_ch_number": len(rows),
        "flagged_count": len(flagged),
        "by_flag": {k: sum(1 for f in flagged if f["flag"] == k) for k in order},
        "flagged": flagged,
    }


@app.get("/diag/deep/{company_name}")
async def diag_deep(company_name: str, request: Request,
                    step: str = Query("stored", description="stored|search|profile|psc|officers|network|charges|filings|captable|sh01|links")):
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")

    from services import companies_house_service as chs
    company = next((c for c in bq_handler.get_universe() if c.get("name", "").lower() == company_name.lower()), None)
    if not company:
        raise HTTPException(status_code=404, detail=f"'{company_name}' not in the universe")
    number = company.get("ch_company_number") or ""

    if step == "stored":
        keys = ["name", "status", "source", "sector", "website", "contact_name", "contact_email",
                "ch_company_number", "ch_official_name", "ch_status", "ch_incorporated_date", "ch_sic_codes",
                "ch_match_confidence", "revenue_y1", "revenue_y1_date", "revenue_y2", "revenue_y3",
                "gross_profit_y1", "profit_y1", "cash_y1", "net_assets_y1", "employees_ch",
                "ch_psc_summary", "ch_ownership_verified", "ch_founder_pct", "ch_cap_table", "ch_cap_table_date",
                "ch_charges_count", "ch_charges_summary", "ch_accounts_overdue", "ch_insolvency_summary",
                "ch_last_resolution", "ch_accounts_regime", "ch_last_share_allotment", "ch_accounts_next_due",
                "ch_history", "ch_allottees", "ch_officer_network", "ch_watched_at",
                "investors_raw", "current_owners", "active_investors",
                "averroes_fit_score", "revenue_band", "last_smartfill_at"]
        return {"step": "stored (BigQuery row)", "data": {k: company.get(k) for k in keys}}
    if step == "search":
        return {"step": "CH search (raw top matches)", "data": chs._search_company(company["name"])[:5]}
    if not number:
        return {"step": step, "error": "No CH company number stored — run SmartFill first"}
    if step == "profile":
        return {"step": "company health (profile-derived)", "data": chs.get_company_health(number)}
    if step == "psc":
        return {"step": "PSC register", "data": chs.get_psc_summary(number)}
    if step == "officers":
        return {"step": "active directors", "data": chs.get_officers_summary(number)}
    if step == "network":
        return {"step": "officer appointment network",
                "data": chs.get_officer_network(number, exclude_names=[company.get("contact_name") or ""])}
    if step == "charges":
        return {"step": "registered charges", "data": chs.get_charges_summary(number)}
    if step == "filings":
        return {"step": "filing intelligence", "data": {
            "filing_intel": chs.get_filing_intel(number),
            "capital_events": chs.get_capital_events(number),
            "filings_since_2024": chs.get_filings_since(number, "2024-01-01")[:12]}}
    if step == "captable":
        return {"step": "cap table v3 (fresh parse: walk-back + SH01 roll-forward + rights + PSC check)",
                "data": chs.get_cap_table(number, company["name"], stored_date="",
                                          psc_summary=company.get("ch_psc_summary") or "")}
    if step == "sh01":
        return {"step": "latest SH01 allottees", "data": chs.get_sh01_allottees(number, company["name"])}
    if step == "links":
        return {"step": "connection layer edges", "data": investor_handler.get_company_connections(company["name"])}
    raise HTTPException(status_code=400, detail=f"Unknown step '{step}'")


@app.get("/email/deep-sync/run")
async def deep_sync_run(request: Request):
    """One-off token-gated trigger: full-history email sync (per-contact IMAP
    search, 10-year window) so email_log captures every exchange from the
    start. Same pipeline as the UI button, deep mode."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return await sync_emails(days=3650, deep=True)


# ── One-off enrich sweep: Contacted/Responded companies + Qualified-without-email
# Runs the FULL current SmartEnrich (registry + distress + filing intel + cap
# table v2 + contact waterfall + draft refresh) over the target set, a batch
# per call. Companies enriched today are excluded, so repeated calls walk the
# queue to zero. Budget and caps enforced per company by SmartEnrich itself.

@app.get("/enrich-oneoff/run")   # GET alias: lets token-gated batch runs be driven without POST
@app.post("/enrich-oneoff/run")
async def enrich_oneoff_run(request: Request, limit: int = Query(12, description="Companies per call")):
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _target(c):
        if c.get("source") == "Internal Test":
            return False
        if str(c.get("last_smartfill_at") or "")[:10] >= today:
            return False  # already refreshed today
        # Everything Qualified or later gets the full current depth
        return c.get("status") in ("Qualified", "Contacted", "Responded", "Meeting", "DD", "Offer", "Won")

    eligible = [c["name"] for c in bq_handler.get_universe() if _target(c)]
    processed, failed = [], []
    for name in eligible[:max(1, min(limit, 20))]:
        try:
            await smartenrich_company(name)
            processed.append(name)
        except HTTPException as e:
            if e.status_code == 429:
                return {"status": "Paused", "detail": str(e.detail), "processed": processed,
                        "failed": failed, "remaining": len(eligible) - len(processed)}
            failed.append(f"{name}: {e.detail}")
        except Exception as e:
            failed.append(f"{name}: {e}")
    return {"status": "Success", "processed": processed, "failed": failed,
            "remaining": max(0, len(eligible) - len(processed) - len(failed))}


@app.post("/contacts/reverify")
async def contacts_reverify():
    """Manually re-run the retroactive contact waterfall (e.g. after adding a verifier key)."""
    return _retro_resolve_contacts(force=True)


PB_BACKFILL_VERSION = "pb-lp-aggregates-v1"


def _backfill_pb_lp_aggregates():
    """One-time internal backfill: the commitments-breakdown fields extracted
    from the ORIGINAL PitchBook LP upload (backend/data/pb_lp_backfill_v1.json,
    generated from the user's file — no re-upload needed). Fill-only: never
    overwrites a value that already exists. Marker-guarded, runs exactly once."""
    from google.cloud import bigquery as bq_lib
    try:
        rows = list(bq_handler.client.query(
            f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                WHERE action_type = 'migration' AND note_text = '{PB_BACKFILL_VERSION}'""").result())
        if rows and int(rows[0].n) > 0:
            return  # already applied
        path = os.path.join(os.path.dirname(__file__), "data", "pb_lp_backfill_v1.json")
        if not os.path.exists(path):
            logger.warning("[Migration] pb_lp_backfill_v1.json missing — skipping backfill")
            return
        payload = open(path).read()
        logger.info(f"[Migration] Applying {PB_BACKFILL_VERSION} ({len(json.loads(payload))} LPs)...")
        bq_handler.client.query(
            f"""MERGE `{investor_handler.table_id}` T
                USING (
                    SELECT * FROM (
                        SELECT LOWER(JSON_EXTRACT_SCALAR(j, '$.name')) AS lname,
                               SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.total_active_commitments_m') AS FLOAT64) AS tac,
                               SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.total_pe_commitments_m') AS FLOAT64) AS tpe,
                               SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.num_vc_commitments') AS INT64) AS nvc,
                               SAFE_CAST(JSON_EXTRACT_SCALAR(j, '$.total_vc_commitments_m') AS FLOAT64) AS tvc,
                               JSON_EXTRACT_SCALAR(j, '$.sold_secondaries') AS ss,
                               JSON_EXTRACT_SCALAR(j, '$.bought_secondaries') AS bs,
                               JSON_EXTRACT_SCALAR(j, '$.policy_description') AS pol,
                               JSON_EXTRACT_SCALAR(j, '$.global_region') AS gr,
                               JSON_EXTRACT_SCALAR(j, '$.open_to_first_time') AS oft,
                               ROW_NUMBER() OVER (PARTITION BY LOWER(JSON_EXTRACT_SCALAR(j, '$.name'))
                                                  ORDER BY JSON_EXTRACT_SCALAR(j, '$.pb_id')) AS rn
                        FROM UNNEST(JSON_EXTRACT_ARRAY(@payload)) j)
                    WHERE rn = 1
                ) S ON LOWER(T.name) = S.lname
                WHEN MATCHED THEN UPDATE SET
                    total_active_commitments_m = IFNULL(T.total_active_commitments_m, S.tac),
                    total_pe_commitments_m = IFNULL(T.total_pe_commitments_m, S.tpe),
                    num_vc_commitments = IFNULL(T.num_vc_commitments, S.nvc),
                    total_vc_commitments_m = IFNULL(T.total_vc_commitments_m, S.tvc),
                    sold_secondaries = IFNULL(NULLIF(T.sold_secondaries, ''), S.ss),
                    bought_secondaries = IFNULL(NULLIF(T.bought_secondaries, ''), S.bs),
                    policy_description = IFNULL(NULLIF(T.policy_description, ''), S.pol),
                    global_region = IFNULL(NULLIF(T.global_region, ''), S.gr),
                    open_to_first_time = IFNULL(NULLIF(T.open_to_first_time, ''), S.oft),
                    updated_at = CURRENT_TIMESTAMP()""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("payload", "STRING", payload),
            ])).result()
        bq_handler._log_activity("__system__", "migration", "migration", note_text=PB_BACKFILL_VERSION)
        logger.info(f"[Migration] {PB_BACKFILL_VERSION} applied")
    except Exception as e:
        logger.warning(f"[Migration] {PB_BACKFILL_VERSION} failed (will retry next boot): {e}")


DRAFT_CLEAR_VERSION = "clear-pre-v9-drafts-v2"


def _clear_v7_drafts():
    """One-time strategy migration: wipe every UNSENT outreach draft written
    under a pre-v9 structure (call ask / details ask). Sent emails and their
    history are untouched. Cleared companies simply regenerate a fresh v9 draft
    on the next Draft click. Covers both never-sent drafts and unsent redrafts
    made after a send (drafted_at > sent_at). Marker-guarded, runs exactly once.
    v2 marker: re-runs once even where the v1 clear already applied, so any
    drafts generated in the brief v8 window are wiped too."""
    try:
        rows = list(bq_handler.client.query(
            f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                WHERE action_type = 'migration' AND note_text = '{DRAFT_CLEAR_VERSION}'""").result())
        if rows and int(rows[0].n) > 0:
            return
        job = bq_handler.client.query(
            f"""UPDATE `{bq_handler.table_id}` SET
                    outreach_draft_subject = NULL, outreach_draft_body = NULL,
                    outreach_draft_to = NULL, outreach_drafted_at = NULL
                WHERE outreach_draft_body IS NOT NULL
                  AND (outreach_sent_at IS NULL OR outreach_drafted_at > outreach_sent_at)""")
        job.result()
        cleared = int(job.num_dml_affected_rows or 0)
        bq_handler._log_activity("__system__", "migration", "migration", note_text=DRAFT_CLEAR_VERSION)
        logger.info(f"[Migration] {DRAFT_CLEAR_VERSION} applied: {cleared} unsent v7 drafts cleared")
    except Exception as e:
        logger.warning(f"[Migration] {DRAFT_CLEAR_VERSION} failed (will retry next boot): {e}")


GCS_REFILL_VERSION = "lp-gcs-refill-v1"


def _refill_lp_from_gcs_uploads(force: bool = False):
    """Re-parse EVERY archived PitchBook LP upload from GCS with the CURRENT
    parser and fill-only merge into investors. Fixes uploads made before new
    columns existed (e.g. the commitments breakdown) without any re-upload:
    the raw files are archived at uploads/investors/ on every upload.
    Marker-guarded; only stamped once at least one file was processed."""
    try:
        if not force:
            rows = list(bq_handler.client.query(
                f"""SELECT COUNT(*) AS n FROM `{bq_handler.activity_table_id}`
                    WHERE action_type = 'migration' AND note_text = '{GCS_REFILL_VERSION}'""").result())
            if rows and int(rows[0].n) > 0:
                return {"status": "Already applied"}
        files = [f for f in gcs_handler.list_files(prefix="uploads/investors/")
                 if f.lower().endswith((".xlsx", ".xls", ".csv"))]
        if not files:
            logger.warning(f"[Migration] {GCS_REFILL_VERSION}: no archived LP uploads found in GCS — not stamping, will retry")
            return {"status": "No files", "detail": "No archived investor uploads at uploads/investors/"}
        results, total_new, total_merged = [], 0, 0
        for fname in files:
            try:
                content = gcs_handler.download_file(fname)
                if not content:
                    results.append(f"{fname}: download failed")
                    continue
                invs = parse_investor_file(content, os.path.basename(fname))
                r = investor_handler.upsert_investors(invs)
                total_new += r["inserted"]
                total_merged += r["merged"]
                results.append(f"{fname}: {len(invs)} parsed, {r['inserted']} new, {r['merged']} enriched")
            except Exception as fe:
                results.append(f"{fname}: FAILED — {fe}")
        bq_handler._log_activity("__system__", "migration", "migration", note_text=GCS_REFILL_VERSION)
        logger.info(f"[Migration] {GCS_REFILL_VERSION} applied: {results}")
        return {"status": "Success", "files": results, "inserted": total_new, "enriched": total_merged}
    except Exception as e:
        logger.warning(f"[Migration] {GCS_REFILL_VERSION} failed (will retry next boot): {e}")
        return {"status": "Failed", "detail": str(e)}


@app.post("/investors/gcs-refill/run")
async def investors_gcs_refill_run(request: Request):
    """Token-gated ops trigger: re-parse archived LP uploads from GCS and
    fill-only merge (same routine as the boot migration, force-run)."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return _refill_lp_from_gcs_uploads(force=True)


@app.on_event("startup")
async def _run_migrations():
    import threading

    def _sequence():
        _migrate_band_rules()
        _retro_resolve_contacts()
        _retro_qualified_blank()
        _backfill_pb_lp_aggregates()
        _refill_lp_from_gcs_uploads()
        _clear_v7_drafts()

    threading.Thread(target=_sequence, daemon=True).start()


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


class SmartFillBatchRequest(BaseModel):
    names: List[str]


@app.post("/smartfill/batch")
async def smartfill_batch(req: SmartFillBatchRequest):
    """
    Bulk SmartFill worker: processes as many companies from the list as fit
    in a ~3.5-minute window, then returns the truth per company plus the
    remainder. The frontend loops on the remainder. This replaces one fragile
    60-120s browser request PER company (connections died mid-flight, Chrome
    silently resent them — double AI spend — then reported phantom failures).

    Idempotency guard: a company SmartFilled in the last 10 minutes is
    reported as done without re-running, so a dropped batch response never
    causes double-processing when the frontend re-sends the same list.
    """
    # STREAMING with heartbeats: the user's network path kills connections
    # that stay silent for ~a minute (this murdered both per-company requests
    # and the first batch design — the server always finished, the browser
    # never heard back). A space char every 10s keeps every hop convinced the
    # connection is alive; the final line is the JSON result.
    import time as _time
    import asyncio as _asyncio
    import json as _json
    from fastapi.responses import StreamingResponse
    from google.cloud import bigquery as bq_lib

    def _fresh(name: str) -> bool:
        rows = bq_handler.client.query(
            f"""SELECT 1 FROM `{bq_handler.table_id}`
                WHERE name = @name AND last_smartfill_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("name", "STRING", name),
            ])).result()
        return bool(rows.total_rows)

    def _run_one(name: str) -> dict:
        # smartfill_company is a coroutine with blocking work inside; give it
        # its own loop in this worker thread so heartbeats keep flowing.
        return _asyncio.run(smartfill_company(name, bulk=True))

    async def _gen():
        started = _time.time()
        processed, stopped = [], None
        remaining = list(dict.fromkeys(req.names))  # dedupe, keep order
        # 150s window: worst case (last company ~2 min) stays under Cloud Run's timeout
        while remaining and (_time.time() - started) < 150 and not stopped:
            name = remaining[0]
            try:
                if await _asyncio.to_thread(_fresh, name):
                    remaining.pop(0)
                    processed.append({"name": name, "status": "already done (last 10 min)"})
                    yield " "
                    continue
                task = _asyncio.create_task(_asyncio.to_thread(_run_one, name))
                while not task.done():
                    await _asyncio.sleep(10)
                    yield " "  # heartbeat
                result = task.result()
                remaining.pop(0)
                processed.append({"name": name, "status": result.get("status") or "OK"})
            except HTTPException as he:
                if he.status_code == 429:  # daily cap — stop cleanly, keep remainder
                    stopped = str(he.detail)
                    break
                remaining.pop(0)
                processed.append({"name": name, "status": f"FAILED: {he.detail}"})
            except Exception as e:
                remaining.pop(0)
                processed.append({"name": name, "status": f"FAILED: {e}"})
            yield " "
        yield "\n" + _json.dumps({"processed": processed, "remaining": remaining, "stopped": stopped})

    return StreamingResponse(_gen(), media_type="text/plain")


# ── Quick Tools: Company Deep Research ──────────────────────────────────────
# Front door to the EXISTING SmartFill workflow for a company that is not in
# the universe yet. It identifies the company (typed name / pasted text /
# uploaded document), seeds ONE ordinary `targets` row (source = 'Quick
# Research', status 'Uploaded'), then calls smartfill_company() — the same
# function the Universe buttons call, untouched. Any future SmartFill change
# therefore applies here automatically, and this cannot alter SmartFill for
# existing flows.

class QuickResearchRequest(BaseModel):
    query: str                      # company name, or pasted text about it
    force: bool = False             # re-run SmartFill even if filled today


def _quick_research_seed(ident: dict, source_note: str) -> dict:
    """Identify -> seed ONE ordinary universe row. NO SmartFill here.

    Why split: identification is seconds, but a full SmartFill is minutes.
    Holding one request for both exceeded the Cloud Run request timeout and
    the stream was cut mid-flight ("Unexpected end of JSON input"). The
    frontend now seeds first, then calls the SAME SmartFill batch endpoint the
    Universe uses (proven, heartbeated, resumable). SmartFill itself is still
    untouched and still the only thing doing the research.
    """
    from services import quick_research as qr
    if ident.get("error"):
        return {"status": "Error", "detail": ident["error"]}
    name = (ident.get("name") or "").strip()
    if not name:
        return {"status": "Error",
                "detail": "Could not identify a single company. " + (ident.get("notes") or "")}

    existing = bq_handler.get_company_full(name)
    if existing:
        seeded = False          # merge-never-overwrite: never reseed a known row
    else:
        bq_handler.save_targets([qr.seed_row(ident, source_note)])
        seeded = True
    return {"status": "Success", "name": name, "seeded": seeded, "identification": ident}


@app.post("/quick-research/identify")
async def quick_research_identify(req: QuickResearchRequest):
    """Company name or pasted text -> identify + seed the row (fast)."""
    from services import quick_research as qr
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Provide a company name or some text about it.")
    # A short single-line input IS the company name: no AI call needed.
    if len(q) <= 80 and "\n" not in q:
        ident = {"name": q, "confidence": "high", "notes": "name supplied directly"}
    else:
        ident = qr.identify_from_text(q)
    return _quick_research_seed(ident, f"Quick Research from typed input: {q[:120]}")


@app.post("/quick-research/document")
async def quick_research_document(file: UploadFile = File(...)):
    """Document -> identify the company + seed the row (one AI call)."""
    from services import quick_research as qr
    content = await file.read()
    filename = file.filename or "upload"
    try:
        gcs_handler.save_raw_file(
            content,
            f"quick-research/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename.replace(' ', '_')}",
            file.content_type)
    except Exception as e:
        logger.warning(f"[QuickResearch] GCS archive failed (continuing): {e}")
    ident = qr.identify_from_document(content, filename)
    return _quick_research_seed(ident, f"Quick Research from document: {filename}")


@app.get("/smartfill/run-by-number")
async def smartfill_run_by_number(request: Request,
                                  numbers: str = Query(..., description="comma-separated CH registration numbers"),
                                  force: int = Query(0, description="1 = re-run even if SmartFilled today")):
    """Token-gated ops runner: find companies by Companies House registration
    number (registration_number OR ch_company_number), SmartFill each
    sequentially SERVER-SIDE, streamed with heartbeats. Idempotent: skips
    companies already SmartFilled today unless force=1. Survives client
    disconnects better than any UI loop (each fill commits as it finishes)."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    wanted = {n.strip().upper() for n in numbers.split(",") if n.strip()}

    async def _work_async():
        from datetime import datetime as _dt, timezone as _tz
        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        uni = bq_handler.get_universe()
        matched, done, skipped, failed = [], [], [], []
        seen_nums = set()
        for c in uni:
            nums = {str(c.get("registration_number") or "").upper(), str(c.get("ch_company_number") or "").upper()}
            hit = wanted & nums - {""}
            if not hit or hit & seen_nums:
                continue
            seen_nums |= hit
            matched.append({"name": c["name"], "number": sorted(hit)[0]})
            if not force and str(c.get("last_smartfill_at") or "")[:10] == today:
                skipped.append(c["name"])
                continue
            try:
                # bulk=False EXPLICITLY: omitted, the Query(False) marker object
                # is truthy and this run-by-number path (a deliberate, targeted
                # rerun) would silently get bulk semantics, skipping web-search
                # scoring for Too Large companies - the opposite of the point of
                # rerunning them by registration number.
                await smartfill_company(c["name"], bulk=False)
                done.append(c["name"])
            except HTTPException as e:
                if e.status_code == 429:
                    failed.append(f"{c['name']}: BUDGET - {e.detail}")
                    break
                failed.append(f"{c['name']}: {e.detail}")
            except Exception as e:
                failed.append(f"{c['name']}: {str(e)[:80]}")
        return {"status": "Success", "requested": len(wanted), "matched": len(matched),
                "not_found": sorted(wanted - seen_nums),
                "filled": done, "skipped_already_today": skipped, "failed": failed,
                "matches": matched}

    # Stream heartbeats around the async work (long run: ~1-3 min per company)
    import asyncio as _asyncio
    import json as _json
    from fastapi.responses import StreamingResponse

    async def _gen():
        task = _asyncio.create_task(_work_async())
        while not task.done():
            await _asyncio.sleep(10)
            yield " "
        try:
            res = task.result()
        except Exception as e:
            res = {"status": "Error", "detail": str(e)}
        yield "\n" + _json.dumps(res)
    return StreamingResponse(_gen(), media_type="text/plain")


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
    _enforce_grounding_budget(4, "SmartFill")
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
    #
    # QUICK RESEARCH BYPASSES THE GATE. Deep Research seeds a row that may hold
    # ONLY the name (typing a name deliberately skips AI identification), so
    # the gate sees no geography and no industry evidence and rejects it before
    # anything runs - which is how "deep research" returned a blank card. The
    # gate exists to protect BULK spend from junk; a person explicitly asking
    # about one company is the opposite case. Enrichment then finds the real
    # geography and sector, and the user judges fit with evidence in front of
    # them instead of the gate guessing from an empty row.
    if company_data.get("source") == "Quick Research":
        # A husk left by an earlier gated run sits at Not a Fit with the gate's
        # no-evidence reason. Re-running Quick Research must wipe that verdict,
        # or the bypass would enrich the row and then faithfully keep the wrong
        # status on top of the fresh data.
        prior = company_data.get("status") or "Uploaded"
        if prior == "Not a Fit":
            prior = "Uploaded"
            try:
                from google.cloud import bigquery as bq_lib
                bq_handler.client.query(
                    f"UPDATE `{bq_handler.table_id}` SET unfit_reason = NULL WHERE name = @n",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("n", "STRING", company_name)])).result()
                bq_handler.add_activity_note(
                    company_name,
                    "Quick Research re-run: cleared the earlier gate verdict "
                    "(it was made with no evidence on the row).",
                    created_by="quick-research")
            except Exception as e:
                logger.warning(f"Husk reset failed for {company_name} (non-fatal): {e}")
        qual = {"qualified": True, "status": prior,
                "reason": "Verdict deferred: research first, judge from evidence after",
                "is_uk_ireland": True, "is_tech": True, "size_qualified": None,
                "size_bucket": "", "size_confidence": "", "size_reason": ""}
    else:
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

    # Contact waterfall v4 (FOUNDER-FIRST): founder found in source → retry
    # search → Hunter email-finder → published ceo@ → verified pattern guess →
    # a colleague → the shared enquiries inbox. Finding a colleague never ends
    # the search; their address is what teaches us the company's email pattern.
    res = {}
    try:
        from services.contact_finder import resolve_contact_email
        _site = website or company_data.get("website", "")
        _cname = founder_info.get("contact_name") or company_data.get("contact_name", "")
        res = resolve_contact_email(_site, _cname,
                                    founder_info.get("contact_email", ""),
                                    founder_info.get("email_source", ""),
                                    retry_fn=lambda: enrichment_agent.retry_email_search(company_name, _site, _cname))
        founder_info["contact_email"] = res["email"]
        founder_info["email_source"] = f"{res['source']} — {res['verification']}" if res["email"] else res["verification"]
        founder_info["contact_email_kind"] = res.get("kind", "")
        founder_info["contact_email_name"] = res.get("recipient_name", "")
        founder_info["contact_email_source"] = _waterfall_provenance(res)
    except Exception as e:
        logger.warning(f"[SmartFill] contact waterfall failed for {company_name} (non-fatal): {e}")

    # Internal test row: enrichment must NEVER change its contact — the test
    # loop depends on it staying pinned to the test inbox.
    if company_data.get("source") == "Internal Test":
        founder_info["contact_email"] = TEST_RECIPIENT
        founder_info["contact_name"] = "Averroes Admin (Test)"
        founder_info["contact_email_kind"] = "founder"
        founder_info["contact_email_name"] = "Averroes Admin (Test)"
        founder_info["contact_email_source"] = "test row: recipient pinned to the admin inbox"

    # Step 3: Companies House financials (UK/Ireland only)
    ch_data = {}
    if qual.get("is_uk_ireland"):
        logger.info(f"UK/Ireland company — extracting Companies House financials for '{company_name}'")
        # Prefer a number we already KNOW over a fresh name search, in order:
        # 1. already stored on the row (CH SIC ingest, CH registry scraper,
        #    or a previous successful match) — fully trusted, no re-check.
        # 2. found moments ago on the company's own website by the contact
        #    waterfall above (Step 2) — real signal, cross-checked against
        #    the name before use.
        # Only when neither exists do we fall back to matching by name.
        _known_number = company_data.get("ch_company_number") or company_data.get("registration_number") or ""
        _trust_known = True
        if not _known_number:
            _known_number = res.get("site_company_number") or ""
            _trust_known = False
        try:
            ch_data = extract_ch_financials(
                company_name,
                sector=company_data.get("sector", ""),
                region=company_data.get("region", ""),
                description=description or company_data.get("description", ""),
                gcs_handler=gcs_handler,
                known_company_number=_known_number,
                trust_known_number=_trust_known,
                hq_city=company_data.get("hq_city", ""),
                known_since=str(company_data.get("ingested_at") or ""),
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

    # Investor names found by the grounded enrichment (source-stated only)
    # are MERGED into investors_raw: union with whatever Inven/PitchBook
    # already provided, existing names never dropped. The LP miner picks
    # them up on its next run like any other source.
    merged_investors = ""
    try:
        web_inv = founder_info.get("investors") or []
        existing_inv = [s.strip() for s in (company_data.get("investors_raw") or "").split(";") if s.strip()]
        if web_inv:
            low = {s.lower() for s in existing_inv}
            for n in web_inv:
                if n.lower() not in low:
                    existing_inv.append(n)
                    low.add(n.lower())
        merged_investors = "; ".join(existing_inv)[:2000]
    except Exception as e:
        logger.warning(f"[SmartFill] investor merge skipped for {company_name}: {e}")

    # Step 5: Update BQ (size + CH financials + scoring)
    #
    # DATA PRECEDENCE (doctrine: never overwrite good data with nothing):
    # 1. Financial numbers: a value from CH filings wins (more verifiable than
    #    any upload), but NULL from a failed/absent parse NEVER wipes stored
    #    values (Inven/PitchBook revenue etc.). Dates follow their number.
    # 2. Contact/web identity: non-blank wins; a blank finding keeps stored.
    # 3. CH registry fields: written only when CH actually MATCHED this run
    #    (@ch_company_number non-blank) — a failed lookup wipes nothing, a
    #    successful one may legitimately refresh or clear.
    # 4. Description: longer wins (existing rule). Scores: always fresh.
    try:
        from google.cloud import bigquery as bq_lib
        query = f"""UPDATE `{bq_handler.table_id}` SET
            last_smartfill_at = CURRENT_TIMESTAMP(),
            stage_entered_at = CASE WHEN IFNULL(status, '') != @status THEN CURRENT_TIMESTAMP() ELSE stage_entered_at END,
            qualified_at = CASE WHEN @status = 'Qualified' THEN IFNULL(qualified_at, CURRENT_TIMESTAMP()) ELSE qualified_at END,
            status = @status,
            unfit_reason = '',
            website = IFNULL(NULLIF(@website, ''), website),
            investors_raw = IFNULL(NULLIF(@investors_raw, ''), investors_raw),
            contact_name = IFNULL(NULLIF(@contact_name, ''), contact_name),
            contact_email = IFNULL(NULLIF(@contact_email, ''), contact_email),
            -- Who the address belongs to travels WITH the address: only write
            -- these when this run actually produced an email, so a failed
            -- waterfall never mislabels the email already stored.
            contact_email_kind = CASE WHEN @contact_email != '' THEN @contact_email_kind ELSE contact_email_kind END,
            contact_email_name = CASE WHEN @contact_email != '' THEN @contact_email_name ELSE contact_email_name END,
            contact_email_source = CASE WHEN @contact_email_source != '' THEN @contact_email_source ELSE contact_email_source END,
            linkedin_url = IFNULL(NULLIF(@linkedin_url, ''), linkedin_url),
            size_bucket = IFNULL(NULLIF(@size_bucket, ''), size_bucket),
            ch_company_number = IFNULL(NULLIF(@ch_company_number, ''), ch_company_number),
            ch_official_name = IFNULL(NULLIF(@ch_official_name, ''), ch_official_name),
            ch_status = CASE WHEN @ch_company_number != '' THEN @ch_status ELSE ch_status END,
            ch_incorporated_date = CASE WHEN @ch_company_number != '' THEN @ch_incorporated_date ELSE ch_incorporated_date END,
            ch_sic_codes = CASE WHEN @ch_company_number != '' THEN @ch_sic_codes ELSE ch_sic_codes END,
            revenue_y1 = IFNULL(@revenue_y1, revenue_y1),
            revenue_y1_date = CASE WHEN @revenue_y1 IS NOT NULL THEN @revenue_y1_date ELSE revenue_y1_date END,
            revenue_y2 = IFNULL(@revenue_y2, revenue_y2),
            revenue_y2_date = CASE WHEN @revenue_y2 IS NOT NULL THEN @revenue_y2_date ELSE revenue_y2_date END,
            revenue_y3 = IFNULL(@revenue_y3, revenue_y3),
            revenue_y3_date = CASE WHEN @revenue_y3 IS NOT NULL THEN @revenue_y3_date ELSE revenue_y3_date END,
            gross_profit_y1 = IFNULL(@gross_profit_y1, gross_profit_y1),
            gross_profit_y2 = IFNULL(@gross_profit_y2, gross_profit_y2),
            profit_y1 = IFNULL(@profit_y1, profit_y1),
            profit_y1_date = CASE WHEN @profit_y1 IS NOT NULL THEN @profit_y1_date ELSE profit_y1_date END,
            profit_y2 = IFNULL(@profit_y2, profit_y2),
            profit_y3 = IFNULL(@profit_y3, profit_y3),
            total_assets_y1 = IFNULL(@total_assets_y1, total_assets_y1),
            net_assets_y1 = IFNULL(@net_assets_y1, net_assets_y1),
            cash_y1 = IFNULL(@cash_y1, cash_y1),
            employees_ch = IFNULL(@employees_ch, employees_ch),
            filing_type = IFNULL(NULLIF(@filing_type, ''), filing_type),
            ch_match_confidence = CASE WHEN @ch_company_number != '' THEN @ch_match_confidence ELSE ch_match_confidence END,
            ch_notes = CASE WHEN @ch_company_number != '' THEN @ch_notes ELSE ch_notes END,
            ch_pdf_path = IFNULL(NULLIF(@ch_pdf_path, ''), ch_pdf_path),
            ch_psc_summary = CASE WHEN @ch_company_number != '' THEN @ch_psc_summary ELSE ch_psc_summary END,
            ch_ownership_verified = CASE WHEN @ch_company_number != '' THEN @ch_ownership_verified ELSE ch_ownership_verified END,
            ch_charges_count = CASE WHEN @ch_company_number != '' THEN IFNULL(@ch_charges_count, ch_charges_count) ELSE ch_charges_count END,
            ch_charges_summary = CASE WHEN @ch_company_number != '' THEN @ch_charges_summary ELSE ch_charges_summary END,
            ch_last_share_allotment = CASE WHEN @ch_company_number != '' THEN @ch_last_share_allotment ELSE ch_last_share_allotment END,
            ch_accounts_next_due = CASE WHEN @ch_company_number != '' THEN @ch_accounts_next_due ELSE ch_accounts_next_due END,
            ch_accounts_overdue = CASE WHEN @ch_company_number != '' THEN @ch_accounts_overdue ELSE ch_accounts_overdue END,
            ch_insolvency_summary = CASE WHEN @ch_company_number != '' THEN @ch_insolvency_summary ELSE ch_insolvency_summary END,
            ch_last_resolution = CASE WHEN @ch_company_number != '' THEN @ch_last_resolution ELSE ch_last_resolution END,
            ch_accounts_regime = CASE WHEN @ch_company_number != '' THEN @ch_accounts_regime ELSE ch_accounts_regime END,
            ch_cap_table = CASE WHEN @ch_cap_table != '' THEN @ch_cap_table ELSE ch_cap_table END,
            ch_cap_table_date = CASE WHEN @ch_cap_table_date != '' THEN @ch_cap_table_date ELSE ch_cap_table_date END,
            ch_founder_pct = IFNULL(@ch_founder_pct, ch_founder_pct),
            ch_history = CASE WHEN @ch_history != '' THEN @ch_history ELSE ch_history END,
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
            bq_lib.ScalarQueryParameter("investors_raw", "STRING", merged_investors),
            bq_lib.ScalarQueryParameter("contact_name", "STRING", founder_info.get("contact_name", "")),
            bq_lib.ScalarQueryParameter("contact_email", "STRING", founder_info.get("contact_email", "")),
            bq_lib.ScalarQueryParameter("contact_email_kind", "STRING", founder_info.get("contact_email_kind", "")),
            bq_lib.ScalarQueryParameter("contact_email_name", "STRING", founder_info.get("contact_email_name", "")),
            bq_lib.ScalarQueryParameter("contact_email_source", "STRING", founder_info.get("contact_email_source", "")),
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
            bq_lib.ScalarQueryParameter("ch_accounts_overdue", "BOOL", bool(ch_data.get("ch_accounts_overdue"))),
            bq_lib.ScalarQueryParameter("ch_insolvency_summary", "STRING", ch_data.get("ch_insolvency_summary") or ""),
            bq_lib.ScalarQueryParameter("ch_last_resolution", "STRING", ch_data.get("ch_last_resolution") or ""),
            bq_lib.ScalarQueryParameter("ch_accounts_regime", "STRING", ch_data.get("ch_accounts_regime") or ""),
            bq_lib.ScalarQueryParameter("ch_cap_table", "STRING", ch_data.get("ch_cap_table") or ""),
            bq_lib.ScalarQueryParameter("ch_cap_table_date", "STRING", ch_data.get("ch_cap_table_date") or ""),
            bq_lib.ScalarQueryParameter("ch_founder_pct", "FLOAT64", ch_data.get("ch_founder_pct")),
            bq_lib.ScalarQueryParameter("ch_history", "STRING", ch_data.get("ch_history") or ""),
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

    # Auto-draft: a freshly Qualified company gets its outreach email drafted
    # immediately, so the button already reads "Review & Send" the moment it
    # lands in the pipeline. Cost-safe: ONE ungrounded Gemini call — the news
    # hook comes only from signals scoring already stored (no grounded search).
    # Never overwrites an existing draft or a sent email.
    auto_draft = None
    if (new_status == "Qualified"
            and not company_data.get("outreach_sent_at")
            and not company_data.get("outreach_draft_body")):
        try:
            draft_input = {**company_data, **ch_data}
            draft_input["description"] = description or company_data.get("description", "")
            draft_input["website"] = website or company_data.get("website", "")
            # The greeting depends on WHO the To: belongs to, so the recipient
            # kind/name must travel into the draft input alongside the address.
            for k in ("contact_name", "contact_email", "linkedin_url",
                      "contact_email_kind", "contact_email_name"):
                if founder_info.get(k):
                    draft_input[k] = founder_info[k]
            if scoring_result.get("score_details"):
                draft_input["score_details"] = scoring_result["score_details"]
            hook = _stored_news_signal(draft_input)
            draft = draft_outreach_email(draft_input, news_hook=hook)
            if draft.get("is_fallback"):
                # Never persist the emergency template — leave the button as
                # "Outreach" so a real generation happens on click.
                raise RuntimeError("draft generation fell back to template; skipping auto-draft persist")
            if company_data.get("source") == "Internal Test":
                draft["to"] = TEST_RECIPIENT
            bq_handler.client.query(f"""UPDATE `{bq_handler.table_id}` SET
                    outreach_draft_subject = @s, outreach_draft_body = @b,
                    outreach_draft_to = @t, outreach_drafted_at = CURRENT_TIMESTAMP()
                    WHERE name = @name""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("s", "STRING", draft.get("subject") or ""),
                    bq_lib.ScalarQueryParameter("b", "STRING", draft.get("body") or ""),
                    bq_lib.ScalarQueryParameter("t", "STRING", draft.get("to") or ""),
                    bq_lib.ScalarQueryParameter("name", "STRING", company_name),
                ])).result()
            bq_handler.add_activity_note(
                company_name,
                f"Outreach draft auto-generated on qualification — to: {draft.get('to') or '(no email found)'}, subject: \"{draft.get('subject')}\"",
                "smartfill")
            auto_draft = {"subject": draft.get("subject"), "to": draft.get("to")}
            logger.info(f"[SmartFill] Auto-drafted outreach for '{company_name}'")
        except Exception as e:
            logger.warning(f"[SmartFill] auto-draft failed for {company_name} (non-fatal): {e}")

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

    # ── Quick Research: the VERDICT COMES AFTER THE RESEARCH (per Ishu) ──────
    # Deep Research must always output findings, so the gate was deferred at
    # the top. Now, with the row fully enriched, the SAME hard filters run on
    # actual evidence. Not a Fit stays Not a Fit - but as a researched verdict
    # sitting on a populated row in the Master Universe, not a guess stamped on
    # an empty one. Data is never removed by the verdict.
    if company_data.get("source") == "Quick Research":
        try:
            fresh = bq_handler.get_company_full(company_name) or {}
            verdict = qualify_company_with_gemini(fresh)
            if not verdict.get("qualified"):
                from google.cloud import bigquery as bq_lib
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET
                            status = 'Not a Fit', unfit_reason = @r,
                            stage_entered_at = CURRENT_TIMESTAMP()
                        WHERE name = @n""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("r", "STRING", verdict.get("reason") or "Failed hard filters"),
                        bq_lib.ScalarQueryParameter("n", "STRING", company_name),
                    ])).result()
                bq_handler._log_activity(company_name, "status_change", "quick-research",
                                         old_status=new_status, new_status="Not a Fit")
                bq_handler.add_activity_note(
                    company_name,
                    f"Deep Research verdict: Not a Fit ({verdict.get('reason')}). "
                    f"Full findings retained on the record.",
                    created_by="quick-research")
                new_status = "Not a Fit"
                qual = {**qual, "is_uk_ireland": verdict.get("is_uk_ireland"),
                        "is_tech": verdict.get("is_tech"), "reason": verdict.get("reason")}
        except Exception as e:
            logger.warning(f"Post-research verdict failed for {company_name} (non-fatal): {e}")

    return {
        "status": "Success",
        "company": company_name,
        "new_status": new_status,
        "auto_draft": auto_draft,
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
        if c.get("hidden_at"):
            continue  # soft-deleted from the view: never spend AI on it
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
    # Bulk runs process at most 100 companies per press (raised from 25,
    # Jul 2026 — worst case 400 grounding weight of the 1,400 daily budget)
    # (~15 min), reviewable, and safely within one session.
    BULK_BATCH_LIMIT = 100
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


# ── Auto bulk SmartFill (the 8 PM job) ───────────────────────────────────────
# Enriches the backlog unattended: Cloud Scheduler ticks every 12 minutes from
# 20:00 Europe/London, each tick processes one small batch, and every tick asks
# "how many SmartFills have run today, manual included?" and stops at the target.
# So a heavy manual day means a light night, and the total can never exceed the
# target. 250/day = ~1,000 grounded requests, well inside the 1,500/day free
# search-grounding allowance with headroom left for daytime memos and lookups.
#
# Batches are deliberately small (15): a tick finishes in ~6 minutes, safely
# inside the 12-minute spacing, so ticks never overlap and no company is
# processed twice. The backlog (~11k companies) clears in ~6-7 weeks, after
# which each night finds only newly ingested companies and costs pennies.

# 60/day per Ishu (21 Aug 2026): the first full night proved the system at 250
# (~£15/day), and he chose a quarter of the pace - ~£4/day, gentler burn. The
# target is shared with manual daytime runs, so a heavy manual day still means
# a light night. One env var turns it back up when wanted.
AUTO_SMARTFILL_TARGET = int(os.getenv("AUTO_SMARTFILL_TARGET", "60"))
# ONE WAVE PER TICK. Batch was 15 with 5 workers = three ~6-minute waves =
# ~18-22 minutes (measured: a manual tick ran 21m50s), which overruns the
# scheduler's 11-minute deadline every time - the cut kills whatever wave is
# mid-flight and the response is lost. Completed companies persist (each
# commits as it finishes), but partially-processed ones are wasted work. A
# batch equal to the worker count finishes in one wave, comfortably inside the
# deadline. Throughput comes from tick COUNT, not tick size.
AUTO_SMARTFILL_BATCH = int(os.getenv("AUTO_SMARTFILL_BATCH", "5"))
# MEASURED, not assumed: the first night (17 Aug) produced ~1 company per tick,
# and the day's manual run did 11 in an hour — a real SmartFill takes 5-6
# MINUTES (grounded enrichment + CH cap tables + contact waterfall + draft),
# not the ~25 seconds the original batch sizing assumed. Sequential processing
# therefore finishes 1-2 per 11-minute tick and the deadline cuts the rest.
# Fix: run the batch with bounded concurrency. 5 workers x ~2 rounds inside the
# deadline = ~8-10 companies per tick; 30 ticks (20:00-01:48) = 240-300/night.
# The concurrency is deliberately modest: each worker holds Gemini + Companies
# House + Hunter connections, and the point is to fill the tick, not to spike
# rate limits.
AUTO_SMARTFILL_CONCURRENCY = int(os.getenv("AUTO_SMARTFILL_CONCURRENCY", "5"))


def _auto_smartfill_rank(c: dict) -> tuple:
    """Best prospects first. Pure and import-safe, so it is testable directly.

    Unenriched rows have no fit score yet (SmartFill is what computes it), so
    the ranking uses what IS already stored as a proxy for prospect quality:
    real financials from an upload beat a bare scraped name, and a row with a
    website and a substantive description is more likely to be a live company
    whose enrichment money is well spent. The thin rows still get done — last,
    by which point the hard-filter gate has binned much of the junk for free.

    Returns a sort key (higher = sooner). Ties break alphabetically so the
    order is stable across ticks and nothing is skipped or repeated.
    """
    score = 0
    if c.get("revenue_y1") or c.get("revenue_estimate_m"):
        score += 3          # real financials on file (usually an Inven upload)
    if c.get("employees") or c.get("employees_ch"):
        score += 2
    if (c.get("website") or "").strip():
        score += 2          # a live site is both a signal and what SmartFill reads
    if len((c.get("description") or "").strip()) >= 200:
        score += 1
    # Source tiers, per Ishu (17 Aug 2026): Gain first, then Inven, then the
    # rest. Detection matches the upload parser's own: the stored source is
    # "Upload: <filename>" and the filename names the provider.
    src = (c.get("source") or "").lower()
    if "gain" in src:
        score += 3          # Gain.pro export: highest-priority dataset
    elif "inven" in src:
        score += 2          # Inven export: curated, rich starting point
    elif any(k in src for k in ("conference", "saastock", "event")):
        score += 1          # someone met them or they showed up somewhere real
    return (-score, (c.get("name") or "").lower())


# PATH SHAPE MATTERS. This was "/smartfill/auto-run" — one segment under
# /smartfill/ — and FastAPI matches routes in REGISTRATION ORDER, so the
# earlier-defined /smartfill/{company_name} swallowed every call and SmartFilled
# a phantom company literally named "auto-run" (gated Not a Fit, one activity
# row per tick). The nightly job therefore NEVER ran: three consecutive nights
# of "~1 company per tick" were five scheduler ticks per hour each processing
# the phantom, while three successive concurrency fixes changed code that never
# executed. Two segments ("auto/run") can never match a single {company_name}
# parameter, so this route is now shadow-proof by construction, not by ordering.
@app.get("/smartfill/auto/run")   # GET alias so a scheduler URL is enough
@app.post("/smartfill/auto/run")
async def smartfill_auto_run(request: Request):
    """One tick of the nightly bulk SmartFill. Token-gated (Cloud Scheduler
    cannot hold a browser session).

    Idempotent by construction: the daily count INCLUDES manual runs, so
    target minus used-today can only shrink. Re-running a tick after the
    target is met does nothing and costs nothing.
    """
    _require_token(request)
    used = bq_handler.count_smartfills_today()
    remaining = AUTO_SMARTFILL_TARGET - used
    if remaining <= 0:
        return {"status": "Done", "used_today": used, "target": AUTO_SMARTFILL_TARGET,
                "message": f"Daily target of {AUTO_SMARTFILL_TARGET} already met ({used} run today). Nothing to do."}

    # Same eligibility as the manual bulk button: never-SmartFilled, passes all
    # three hard filters on stored data, not hidden. ZERO AI spent choosing.
    eligible = []
    for c in bq_handler.get_universe():
        if c.get("hidden_at") or c.get("last_smartfill_at"):
            continue
        if c.get("source") == "Internal Test":
            continue
        qual = qualify_company(c)
        if not qual["is_uk_ireland"] or not qual["is_tech"]:
            continue
        if qual.get("size_qualified") is False:
            continue
        eligible.append(c)
    eligible.sort(key=_auto_smartfill_rank)

    batch = [c["name"] for c in eligible[:min(AUTO_SMARTFILL_BATCH, remaining)]]
    processed, failed = [], []
    # THREADS, NOT ASYNC — measured, twice. The first version was sequential
    # (~1 company per 11-minute tick). The second used asyncio.gather with a
    # semaphore of 5 and produced EXACTLY THE SAME PACE, because SmartFill's
    # internals (the Gemini SDK, Companies House HTTP, BigQuery writes) are
    # blocking calls: async concurrency only parallelises work that yields, and
    # blocking calls hold the event loop, so five "concurrent" workers queued
    # behind each other single file. The semaphore was real, the parallelism
    # was theatre.
    #
    # asyncio.to_thread gives each company a real worker thread, which is what
    # actually overlaps blocking I/O. Each thread runs the async endpoint to
    # completion in its own event loop (asyncio.run), safe because
    # smartfill_company touches nothing loop-bound — its clients are the same
    # thread-safe synchronous ones the rest of the app shares.
    #
    # A 429 from the hard daily cap flips stop_now so no NEW company starts,
    # but workers already mid-company run to completion — a SmartFill is not
    # safely interruptible halfway through its writes.
    import asyncio
    import threading
    gate = threading.Semaphore(max(1, AUTO_SMARTFILL_CONCURRENCY))
    stop_now = threading.Event()

    def _one_blocking(name: str):
        with gate:
            if stop_now.is_set():
                return
            try:
                asyncio.run(smartfill_company(name, bulk=True))
                processed.append(name)
            except HTTPException as e:
                if e.status_code == 429:
                    stop_now.set()
                else:
                    failed.append(f"{name}: {e.detail}")
            except Exception as e:
                failed.append(f"{name}: {e}")

    await asyncio.gather(*[asyncio.to_thread(_one_blocking, n) for n in batch])

    return {
        "status": "Success",
        "used_today_before": used,
        "target": AUTO_SMARTFILL_TARGET,
        "backlog": len(eligible),
        "processed": processed,
        "failed": failed,
        "message": (f"Processed {len(processed)} of a {len(eligible)}-company backlog "
                    f"({used + len(processed)}/{AUTO_SMARTFILL_TARGET} today, manual runs included)."),
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
    _enforce_grounding_budget(3, "SmartEnrich")

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
        # The SAME founder-first waterfall v4 as SmartFill (doctrine: one
        # intent, one implementation — never fork the ladder per entry point).
        try:
            from services.contact_finder import resolve_contact_email
            _cname = founder_info.get("contact_name") or company.get("contact_name", "")
            res = resolve_contact_email(company.get("website", ""), _cname,
                                        founder_info.get("contact_email", ""),
                                        founder_info.get("email_source", ""),
                                        retry_fn=lambda: enrichment_agent.retry_email_search(
                                            company_name, company.get("website", ""), _cname))
            founder_info["contact_email"] = res["email"]
            founder_info["email_source"] = f"{res['source']} — {res['verification']}" if res["email"] else res["verification"]
            founder_info["contact_email_kind"] = res.get("kind", "")
            founder_info["contact_email_name"] = res.get("recipient_name", "")
            founder_info["contact_email_source"] = _waterfall_provenance(res)
        except Exception as e:
            logger.warning(f"[SmartEnrich] contact waterfall failed for {company_name} (non-fatal): {e}")
        found_email = (founder_info.get("contact_email") or "").strip()
        email_src = founder_info.get("email_source") or "source not stated by the model"
        stored_email = (company.get("contact_email") or "").strip()

        # Fill gaps in name / LinkedIn / website (never overwrite existing)
        for col, key in [("contact_name", "contact_name"), ("linkedin_url", "linkedin_url"), ("website", "website")]:
            val = founder_info.get(key)
            if val:
                set_clauses.append(f"{col} = CASE WHEN IFNULL({col}, '') = '' THEN @{col} ELSE {col} END")
                params.append(bq_lib.ScalarQueryParameter(col, "STRING", val))

        # Recipient kind/name follow the address whenever we have one, so the
        # greeting can never go stale against a newly resolved recipient.
        if found_email:
            for col, key in [("contact_email_kind", "contact_email_kind"),
                             ("contact_email_name", "contact_email_name"),
                             ("contact_email_source", "contact_email_source")]:
                set_clauses.append(f"{col} = @{col}")
                params.append(bq_lib.ScalarQueryParameter(col, "STRING", founder_info.get(key) or ""))

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
            # Clearing the address must clear its labels too, or the card would
            # keep describing a recipient that no longer exists.
            set_clauses.append("contact_email_kind = ''")
            set_clauses.append("contact_email_name = ''")
            set_clauses.append("contact_email_source = @cleared_src")
            params.append(bq_lib.ScalarQueryParameter("cleared_src", "STRING",
                                                      founder_info.get("contact_email_source") or "cleared: no published source found"))
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
            get_company_health, get_filing_intel, get_cap_table,
        )
        psc = get_psc_summary(number)
        charges = get_charges_summary(number)
        capital = get_capital_events(number)
        profile = _get_company_profile(number) or {}
        next_due = (profile.get("accounts", {}) or {}).get("next_due") or ""
        health = get_company_health(number)
        intel = get_filing_intel(number)
        for col, val, typ in [
            ("ch_psc_summary", psc["psc_summary"], "STRING"),
            ("ch_ownership_verified", psc["ownership_verified"], "STRING"),
            ("ch_charges_count", charges["charges_count"], "INT64"),
            ("ch_charges_summary", charges["charges_summary"], "STRING"),
            ("ch_last_share_allotment", capital["last_share_allotment"], "STRING"),
            ("ch_accounts_next_due", next_due, "STRING"),
            ("ch_accounts_overdue", health["ch_accounts_overdue"], "BOOL"),
            ("ch_insolvency_summary", health["ch_insolvency_summary"], "STRING"),
            ("ch_last_resolution", intel["ch_last_resolution"], "STRING"),
            ("ch_accounts_regime", intel["ch_accounts_regime"], "STRING"),
        ]:
            set_clauses.append(f"{col} = @{col}")
            params.append(bq_lib.ScalarQueryParameter(col, typ, val))
        actions.append("registry intel refreshed (incl. distress + filing signals)")

        # ── 2b. Cap table: parse CS01 ONLY if newer than what we hold.
        # v1-format tables (truncated, inconsistent percentages) are re-parsed
        # once regardless — detected by the missing v2 marker in the JSON.
        try:
            _stored_cap_date = company.get("ch_cap_table_date") or ""
            if _stored_cap_date and '"fp": 2' not in (company.get("ch_cap_table") or ""):
                _stored_cap_date = ""  # force one re-parse: v3 rules + founder-proxy v2 (dotted corp suffixes)
            cap = get_cap_table(number, company_name, stored_date=_stored_cap_date,
                                psc_summary=company.get("ch_psc_summary") or "")
            if cap:
                for col, val, typ in [
                    ("ch_cap_table", cap["ch_cap_table"], "STRING"),
                    ("ch_cap_table_date", cap["ch_cap_table_date"], "STRING"),
                    ("ch_founder_pct", cap.get("ch_founder_pct"), "FLOAT64"),
                ]:
                    set_clauses.append(f"{col} = @{col}")
                    params.append(bq_lib.ScalarQueryParameter(col, typ, val))
                actions.append("cap table extracted from CS01")
        except Exception as e:
            logger.warning(f"[SmartEnrich] cap table failed for {company_name} (non-fatal): {e}")

        # ── 3. Financials: re-parse if a newer filing exists, OR once to
        # backfill the multi-year history (ch_history) for rows parsed before
        # the depth upgrade — the profile charts need the full series.
        filings = _get_accounts_filings(number, max_items=1)
        latest_filing_date = filings[0].get("date", "") if filings else ""
        known_date = company.get("revenue_y1_date") or ""
        needs_history = not company.get("ch_history")
        if latest_filing_date and (latest_filing_date > known_date or needs_history):
            # `number` (above) is already the CH-verified identity for this
            # row — never re-derive it by name here. Re-searching on every
            # periodic re-parse is exactly how a re-enrichment run could
            # silently drift onto a different, similarly-named company.
            ch_data = extract_ch_financials(company_name, sector=company.get("sector", ""),
                                            region=company.get("region", ""),
                                            description=company.get("description", ""),
                                            gcs_handler=gcs_handler,
                                            known_company_number=number,
                                            trust_known_number=True,
                                            known_since=str(company.get("ingested_at") or ""))
            if not ch_data.get("error"):
                new_financials = True
                for col in ["revenue_y1", "revenue_y2", "revenue_y3", "gross_profit_y1", "gross_profit_y2",
                            "profit_y1", "profit_y2", "profit_y3", "total_assets_y1", "net_assets_y1", "cash_y1"]:
                    if ch_data.get(col) is not None:
                        set_clauses.append(f"{col} = @{col}")
                        params.append(bq_lib.ScalarQueryParameter(col, "FLOAT64", ch_data.get(col)))
                for col in ["revenue_y1_date", "revenue_y2_date", "revenue_y3_date", "filing_type", "ch_pdf_path", "ch_history"]:
                    if ch_data.get(col):
                        set_clauses.append(f"{col} = @{col}")
                        params.append(bq_lib.ScalarQueryParameter(col, "STRING", str(ch_data.get(col))))
                emp = ch_data.get("employees_ch")
                if emp is not None:
                    set_clauses.append("employees_ch = @employees_ch")
                    params.append(bq_lib.ScalarQueryParameter("employees_ch", "INT64", emp))
                actions.append(f"accounts parsed ({'new filing' if latest_filing_date > known_date else 'history backfill'}, filed {latest_filing_date})")
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

    # ── 5. Draft refresh: enriched data may change what the email should say
    # (new contact, fresh financials, better description). Regenerate the
    # draft with the updated picture — but NEVER touch anything already sent,
    # and never persist the emergency fallback template over a real draft.
    if not company.get("outreach_sent_at"):
        try:
            draft_input = dict(company)
            # The greeting depends on WHO the To: belongs to, so the recipient
            # kind/name must travel into the draft input alongside the address.
            for k in ("contact_name", "contact_email", "linkedin_url",
                      "contact_email_kind", "contact_email_name"):
                if founder_info.get(k):
                    draft_input[k] = founder_info[k]
            hook = _stored_news_signal(draft_input)
            draft = draft_outreach_email(draft_input, news_hook=hook)
            if not draft.get("is_fallback") and draft.get("body"):
                if company.get("source") == "Internal Test":
                    draft["to"] = TEST_RECIPIENT
                elif founder_info.get("contact_email"):
                    draft["to"] = founder_info["contact_email"]
                bq_handler.client.query(f"""UPDATE `{bq_handler.table_id}` SET
                        outreach_draft_subject = @s, outreach_draft_body = @b,
                        outreach_draft_to = @t, outreach_drafted_at = CURRENT_TIMESTAMP()
                        WHERE name = @name AND outreach_sent_at IS NULL""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("s", "STRING", draft.get("subject") or ""),
                        bq_lib.ScalarQueryParameter("b", "STRING", draft.get("body") or ""),
                        bq_lib.ScalarQueryParameter("t", "STRING", draft.get("to") or ""),
                        bq_lib.ScalarQueryParameter("name", "STRING", company_name),
                    ])).result()
                actions.append("outreach draft refreshed with enriched data")
        except Exception as e:
            logger.warning(f"[SmartEnrich] draft refresh failed for {company_name} (non-fatal): {e}")

    # Audit: a timestamped summary of everything this enrich did, on the
    # company's activity trail.
    try:
        bq_handler.add_activity_note(company_name, "SmartEnrich: " + " · ".join(actions), "smartenrich")
    except Exception:
        pass

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
        # str(): local-growth scoring stores numeric values here — a float
        # has no .strip() and killed the auto-draft for every Inven company
        val = str(metric.get("value") or "").strip()
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

    # Persist the draft so the UI can offer Review & Send without regenerating.
    # The emergency fallback template is NEVER persisted — the user sees it in
    # the modal (flagged) but the next open regenerates properly.
    if result.get("is_fallback"):
        result["news_hook"] = news_hook
        result["news_hook_source"] = hook_source
        return result
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


# ── The send is the truth about the contact ──────────────────────────────────
# Ishu double-checks every outreach before sending and sometimes corrects the
# recipient address or the name in the greeting. Those corrections are the most
# reliable contact data the system ever sees: a human verified them at the
# moment of sending. So the send ADOPTS them — the stored contact follows what
# was actually sent, rather than the correction living only in one email.

_GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))[\s,]+"
    r"([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)\s*[,!.—-]?\s*$",
    re.IGNORECASE)

# Greeting words that are not a person. "Hi there," must never become a contact.
_NOT_A_NAME = {"there", "team", "all", "both", "everyone", "folks", "guys",
               "sir", "madam", "friend", "friends", "hiring", "support", "sales"}


def greeting_name(body: str) -> str:
    """The person the email actually addresses, read from its first line.

    Pure and conservative: returns '' unless the first non-empty line is a
    recognisable greeting naming a plausible person. A wrong '' costs nothing
    (no update happens); a wrong NAME would corrupt a verified contact, so
    every doubtful shape returns ''.
    """
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _GREETING_RE.match(line)
        if not m:
            return ""            # first real line is not a greeting: stop, never scan deeper
        name = m.group(1).strip()
        # IGNORECASE makes the greeting words match in any case, but it also
        # makes [A-Z] match lowercase, so capitalisation must be re-checked
        # here: "hi again," is not addressed to a Mr Again.
        if not name[:1].isupper():
            return ""
        if name.lower() in _NOT_A_NAME:
            return ""
        return name
    return ""


def contact_adoption(stored_email: str, stored_name: str,
                     sent_to: str, body_name: str,
                     is_test: bool = False, bounced_email: str = "") -> dict:
    """What the send teaches us about the contact. Pure, so it is testable.

    Returns {"email": new_or_None, "name": new_or_None}.

      * EMAIL: the address actually sent to wins whenever it differs. Except the
        Internal Test row (its recipient is force-overridden to the test inbox,
        which says nothing about the real contact) and an address already known
        to bounce (a dead address must never be re-adopted).
      * NAME: the greeting wins only when it names a DIFFERENT person. A first
        name matching the stored contact's first name is the same person, and
        keeping the stored full name preserves the surname the greeting lacks.
    """
    out = {"email": None, "name": None}
    if is_test:
        return out
    to = (sent_to or "").strip().lower()
    if to and to != (stored_email or "").strip().lower() \
            and to != (bounced_email or "").strip().lower():
        out["email"] = to
    if body_name:
        stored_first = ((stored_name or "").strip().split() or [""])[0].lower()
        new_first = body_name.strip().split()[0].lower()
        if new_first and new_first != stored_first:
            out["name"] = body_name.strip()
    return out


@app.get("/outreach/followup-draft/{company_name}")
async def outreach_followup_draft(company_name: str):
    """The 14-day follow-up, pre-filled from the approved template. Zero AI.

    Not persisted to outreach_draft_*: those columns hold the FIRST email,
    whose subject this follow-up threads under. Overwriting them would break
    the threading of every later follow-up.
    """
    company_data = {"name": company_name}
    for c in bq_handler.get_universe():
        if c.get("name") == company_name:
            company_data = c
            break
    from services.outreach_service import draft_followup_email
    result = draft_followup_email(company_data)
    if company_data.get("source") == "Internal Test":
        result["to"] = TEST_RECIPIENT
    return result


@app.get("/outreach/compose-draft/{company_name}")
async def outreach_compose_draft(company_name: str):
    """Pre-fill for the blank compose (Responded and beyond): the CONVERSATION
    decides the recipient and the subject, not the stored contact.

    * To: the address their latest genuine reply came FROM. Founders often
      answer from a personal or direct address after we wrote to a shared
      inbox, and replying anywhere else forks the thread. Falls back to the
      stored contact_email when no reply is on record (reply-exempt companies).
    * Subject: Re: their latest message's subject, so the reply threads under
      what they actually said. Falls back to the original outreach subject.
    * Body: blank on purpose. Mid-conversation, the tool has no business
      guessing the words.
    """
    company_data = {"name": company_name}
    for c in bq_handler.get_universe():
        if c.get("name") == company_name:
            company_data = c
            break

    to_addr = company_data.get("contact_email") or ""
    subject = (company_data.get("outreach_draft_subject") or "").strip()
    try:
        from google.cloud import bigquery as bq_lib
        log = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"
        excluded = ", ".join(f"'{c}'" for c in bq_handler.NON_REPLY_CLASSES)
        rows = bq_handler._run_query(f"""
            SELECT counterparty_email, subject FROM `{log}`
            WHERE entity_type = 'company' AND entity_name = @name
              AND direction = 'received'
              AND IFNULL(classification, '') NOT IN ({excluded})
            ORDER BY sent_at DESC LIMIT 1
        """, params=[bq_lib.ScalarQueryParameter("name", "STRING", company_name)])
        if rows:
            to_addr = (rows[0].get("counterparty_email") or "").strip() or to_addr
            subject = (rows[0].get("subject") or "").strip() or subject
    except Exception as e:
        logger.warning(f"compose-draft log read failed for {company_name} (non-fatal): {e}")

    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    if company_data.get("source") == "Internal Test":
        to_addr = TEST_RECIPIENT
    return {"to": to_addr, "subject": subject, "body": "", "company": company_name}


@app.post("/outreach/send")
async def send_outreach(req: OutreachSendRequest):
    """Send an outreach email via Gmail SMTP."""
    # Internal test company: force the recipient to the test inbox, even if
    # the To field was edited — a test email must never reach a real founder.
    to_addr = req.to
    # Read the stage and the stored contact BEFORE the send, in one pass: the
    # status_change needs the old stage, and contact adoption below needs the
    # stored contact to compare the actually-sent details against.
    prev_status = ""
    stored = {}
    if req.company_name:
        try:
            for c in bq_handler.get_universe():
                if c.get("name") == req.company_name:
                    stored = c
                    prev_status = c.get("status") or ""
                    if c.get("source") == "Internal Test" and to_addr != TEST_RECIPIENT:
                        logger.info(f"Test company send: recipient '{to_addr}' overridden to {TEST_RECIPIENT}")
                        to_addr = TEST_RECIPIENT
                    break
        except Exception:
            pass
    req.to = to_addr

    # THREADING. A "Re:" subject signals this send continues the existing
    # conversation, so it must carry In-Reply-To/References with the thread's
    # real Message-IDs — Gmail threads on those headers, never on the subject.
    # This was learned the hard way: a follow-up to a founder landed as a
    # separate email because the subject said "Re:" and the headers said
    # nothing. Keying on the subject also leaves Ishu in control: keep "Re:"
    # to stay in the thread, change the subject to deliberately start afresh.
    in_reply_to, references = "", ""
    if req.company_name and (req.subject or "").strip().lower().startswith("re:"):
        try:
            thread = bq_handler.get_thread_ids(req.company_name)
            in_reply_to, references = thread["in_reply_to"], thread["references"]
            if not in_reply_to:
                logger.warning(f"[Send] '{req.subject}' looks like a reply but no thread "
                               f"Message-IDs exist for {req.company_name}; sending unthreaded.")
        except Exception as e:
            logger.warning(f"[Send] thread lookup failed for {req.company_name} (sending unthreaded): {e}")

    logger.info(f"Sending outreach to: {req.to} (company: {req.company_name}, threaded: {bool(in_reply_to)})")
    result = send_email(req.to, req.subject, req.body,
                        in_reply_to=in_reply_to, references=references)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["detail"])
    # Log the sent email in BQ (best-effort)
    try:
        from google.cloud import bigquery as bq_lib
        # STAGE RULE FOR SENDS. Sending an email only ever moves a company
        # FORWARD to Contacted, never backward: a follow-up to a Contacted
        # company keeps it Contacted, and an email to a Responded (or later)
        # company must NOT demote it. This mattered the moment sends stopped
        # being first-outreach-only: replying to a founder from the Responded
        # column would otherwise have yanked the company back to Contacted,
        # which the reply rule would then immediately dispute (a genuine reply
        # exists) and re-promote, thrashing the activity log.
        #
        # outreach_sent_at is refreshed on EVERY send: it is "our last outbound",
        # which is exactly what the 14-day follow-up clock runs from.
        #
        # The Internal Test row is exempt from all of it: a test send always
        # moves it to Contacted so the full loop is testable from any state.
        query = f"""UPDATE `{bq_handler.table_id}`
                    SET stage_entered_at = CASE
                            WHEN source = 'Internal Test' THEN CURRENT_TIMESTAMP()
                            WHEN status IN ('Responded', 'Meeting', 'DD', 'Offer', 'Won', 'Lost', 'Contacted') THEN stage_entered_at
                            ELSE CURRENT_TIMESTAMP() END,
                        contacted_at = IFNULL(contacted_at, CURRENT_TIMESTAMP()),
                        outreach_sent_at = CURRENT_TIMESTAMP(),
                        outreach_draft_to = @to_addr,
                        status = CASE
                            WHEN source = 'Internal Test' THEN 'Contacted'
                            WHEN status IN ('Responded', 'Meeting', 'DD', 'Offer', 'Won', 'Lost') THEN status
                            ELSE 'Contacted' END
                    WHERE name = @name AND (status != 'Not a Fit' OR source = 'Internal Test')"""
        job_config = bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("to_addr", "STRING", req.to or ""),
            bq_lib.ScalarQueryParameter("name", "STRING", req.company_name or ""),
        ])
        bq_handler.client.query(query, job_config=job_config).result()
    except Exception as e:
        logger.warning(f"Failed to update status after outreach: {e}")

    # Adopt Ishu's pre-send corrections as the stored contact. He verifies the
    # address and the greeting before every send, so what was ACTUALLY sent is
    # better contact data than what the waterfall guessed. Never for the test
    # row, and a known-bounced address is never re-adopted.
    if req.company_name and stored:
        try:
            adopt = contact_adoption(
                stored.get("contact_email") or "", stored.get("contact_name") or "",
                req.to or "", greeting_name(req.body or ""),
                is_test=stored.get("source") == "Internal Test",
                bounced_email=stored.get("bounced_email") or "")
            sets, params, changes = [], [], []
            if adopt["email"]:
                sets.append("contact_email = @ce")
                params.append(bq_lib.ScalarQueryParameter("ce", "STRING", adopt["email"]))
                changes.append(f"email {stored.get('contact_email') or '(none)'} -> {adopt['email']}")
            if adopt["name"]:
                sets.append("contact_name = @cn")
                params.append(bq_lib.ScalarQueryParameter("cn", "STRING", adopt["name"]))
                changes.append(f"name {stored.get('contact_name') or '(none)'} -> {adopt['name']}")
            if sets:
                params.append(bq_lib.ScalarQueryParameter("name", "STRING", req.company_name))
                bq_handler.client.query(
                    f"UPDATE `{bq_handler.table_id}` SET {', '.join(sets)} WHERE name = @name",
                    job_config=bq_lib.QueryJobConfig(query_parameters=params)).result()
                bq_handler.add_activity_note(
                    req.company_name,
                    f"Contact adopted from the send ({', '.join(changes)}): what was "
                    f"actually sent is verified contact data.",
                    created_by="outreach-send")
        except Exception as e:
            logger.warning(f"Contact adoption failed for {req.company_name} (non-fatal): {e}")

    # Activity log: BOTH rows, and both matter.
    #
    # The note is for a human reading the outreach tab. The status_change is for
    # reconciliation, which needs to know how a company reached Contacted. The
    # UPDATE above writes `status` directly, and for a long time nothing recorded
    # that as a stage move — the same omission as the raw-SQL stage rename. The
    # consequence was concrete: reconciliation joined on activity_log and silently
    # skipped every company whose move it could not see. Any code path that writes
    # `status` must also write a status_change row.
    if req.company_name:
        try:
            bq_handler._log_activity(
                req.company_name, "outreach_sent", "system",
                note_text=f"Outreach email sent to {req.to} — subject: \"{req.subject}\"")
            # A status_change row only when the status actually changed: sends
            # from Responded or later keep their stage (see the CASE above), so
            # logging a move would record something that did not happen.
            if (prev_status or "") not in ("Contacted", "Responded", "Meeting",
                                           "DD", "Offer", "Won", "Lost"):
                bq_handler._log_activity(
                    req.company_name, "status_change", "outreach-send",
                    old_status=prev_status or "", new_status="Contacted")
        except Exception as e:
            logger.warning(f"Failed to log send activity: {e}")
    return result


@app.post("/admin/rescore-fit")
async def admin_rescore_fit(request: Request,
                            dry_run: int = Query(1, description="1 = preview only (default), 0 = apply")):
    """Re-apply the current fit-score rules to every already-scored company.
    ZERO AI: recomputes from data stored on the rows (see
    scoring.rescore_company_local). Preview reports the score movement so a
    rule change can be sanity-checked before it rewrites the book.
    """
    _require_token(request)

    def _run():
        from ai.scoring import rescore_company_local
        from google.cloud import bigquery as bq_lib
        rows = bq_handler.get_universe()
        plan = []
        for c in rows:
            if c.get("source") == "Internal Test":
                continue
            upd = rescore_company_local(c)
            if not upd:
                continue
            old = c.get("averroes_fit_score")
            new = upd["averroes_fit_score"]
            if old is None and new is None:
                continue
            if old is not None and new is not None and abs(float(old) - new) < 0.0005 \
                    and (upd["score_details"] == (c.get("score_details") or "")):
                continue
            plan.append({"name": c["name"], "old": old, "new": new, "upd": upd})

        moved_up = sum(1 for p in plan if (p["old"] or 0) < (p["new"] or 0))
        moved_down = sum(1 for p in plan if (p["old"] or 0) > (p["new"] or 0))
        gained = sum(1 for p in plan if p["old"] is None and p["new"] is not None)
        lost = sum(1 for p in plan if p["old"] is not None and p["new"] is None)
        summary = {
            "scored_companies_checked": len(rows),
            "would_update": len(plan),
            "moved_up": moved_up, "moved_down": moved_down,
            "newly_scoreable": gained, "no_longer_scoreable": lost,
            "biggest_moves": sorted(
                [{"name": p["name"], "old": p["old"], "new": p["new"]} for p in plan
                 if p["old"] is not None and p["new"] is not None],
                key=lambda x: abs(x["new"] - x["old"]), reverse=True)[:15],
        }
        if dry_run:
            return {"status": "Preview", "dry_run": True, **summary,
                    "message": "Nothing was changed. Re-run with dry_run=0 to apply."}

        applied, failures = [], []
        for p in plan:
            u = p["upd"]
            try:
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET
                            averroes_fit_score = @fit,
                            score_revenue_size = @rs,
                            score_revenue_growth = @rg,
                            score_employee_growth = @eg,
                            score_details = @sd
                        WHERE name = @name""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("fit", "FLOAT64", u["averroes_fit_score"]),
                        bq_lib.ScalarQueryParameter("rs", "FLOAT64", u["score_revenue_size"]),
                        bq_lib.ScalarQueryParameter("rg", "FLOAT64", u["score_revenue_growth"]),
                        bq_lib.ScalarQueryParameter("eg", "FLOAT64", u["score_employee_growth"]),
                        bq_lib.ScalarQueryParameter("sd", "STRING", u["score_details"]),
                        bq_lib.ScalarQueryParameter("name", "STRING", p["name"]),
                    ])).result()
                applied.append(p["name"])
            except Exception as e:
                failures.append(f"{p['name']}: {e}")
        return {"status": "Success", "dry_run": False, **summary,
                "updated": len(applied), "failed": failures,
                "message": f"Rescored {len(applied)} companies under the v4 rules."
                           + (f" {len(failures)} FAILED." if failures else "")}

    return _stream_json(_run)


@app.get("/company/{company_name}/email-docs")
async def list_email_docs(company_name: str):
    """Every document this company has ever emailed us, newest first."""
    return {"company": company_name, "documents": bq_handler.get_email_docs(company_name)}


@app.get("/email-doc/download")
async def download_email_doc(path: str = Query(..., description="gcs_path from the documents list")):
    """Serve one filed email document. Session-gated (NOT exempt): these are
    founders' own files, the most confidential thing the tool holds. The
    frontend fetches with auth and opens a blob, same as CH filing PDFs."""
    from fastapi.responses import Response
    if not path.startswith("email-docs/") or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid document path.")
    if not gcs_handler.storage_client:
        raise HTTPException(status_code=500, detail="GCS not available")
    blob = gcs_handler.storage_client.bucket(gcs_handler.bucket_name).blob(path)
    if not blob.exists():
        raise HTTPException(status_code=404, detail="Document not found in storage.")
    data = blob.download_as_bytes()
    filename = path.split("/")[-1]
    return Response(content=data,
                    media_type=blob.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.post("/email/docs/backfill")
async def email_docs_backfill(request: Request,
                              days: int = Query(90, description="How far back to scan the mailbox")):
    """Retro pass: file attachments from emails ALREADY received.

    Re-reads the mailbox (address + domain + thread matching, same as the
    sync), extracts attachments from every inbound company message, and runs
    the same file-and-read pipeline. Fully idempotent - (message_id, filename)
    already stored is skipped - so it can be re-run any time.
    """
    _require_token(request)

    def _run():
        from services.email_sync_service import sync_mailbox
        from services.email_docs_service import process_email_documents
        known, known_domains, companies_by_name = {}, {}, {}
        for c in bq_handler.get_universe():
            companies_by_name[c.get("name")] = c
            entry = {"type": "company", "name": c.get("name")}
            for em in {(c.get("contact_email") or "").strip().lower(),
                       (c.get("outreach_draft_to") or "").strip().lower()}:
                if em:
                    known[em] = entry
                dom = em.split("@")[-1] if "@" in em else ""
                if dom:
                    known_domains.setdefault(dom, entry)
        thread_map = bq_handler.get_message_id_entity_map()
        entries = sync_mailbox(known, days=max(1, min(days, 3650)),
                               known_domains=known_domains, thread_map=thread_map)
        filed, scanned = [], 0
        backfill_ai_budget = [50]  # one-off pass, a larger but still bounded read budget
        for e in entries:
            if e.get("direction") != "received" or not e.get("attachments"):
                continue
            scanned += 1
            names = process_email_documents(
                bq_handler, gcs_handler, e, companies_by_name.get(e.get("entity_name")),
                ai_budget=backfill_ai_budget)
            filed += [f"{e['entity_name']}: {n}" for n in names]
        return {"status": "Success", "days_scanned": days,
                "emails_with_attachments": scanned, "documents_filed": filed,
                "message": f"Filed {len(filed)} new documents from {scanned} emails "
                           f"with attachments (already-filed ones skipped)."}

    return _stream_json(_run)


@app.post("/admin/contacts/sync-from-sends")
async def contacts_sync_from_sends(request: Request,
                                   dry_run: int = Query(1, description="1 = preview only (default), 0 = apply")):
    """Retro-apply the send-time contact rule to everything already sent.

    Same rule the send path now applies live (contact_adoption): the LATEST
    outbound email per company is a human-verified statement of who the contact
    is, so the stored contact_email follows its To address and contact_name
    follows its greeting. Corrections made by hand before this rule existed are
    recovered from email_log rather than lost.

    Skips the Internal Test row and never re-adopts a known-bounced address.
    Defaults to a PREVIEW listing every change it would make.
    """
    _require_token(request)

    def _run():
        log = f"{bq_handler.project_id}.{bq_handler.dataset_id}.email_log"
        rows = bq_handler._run_query(f"""
            WITH latest_sent AS (
                SELECT * EXCEPT(rn) FROM (
                    SELECT entity_name, counterparty_email, snippet, sent_at,
                           ROW_NUMBER() OVER (PARTITION BY entity_name
                                              ORDER BY sent_at DESC) AS rn
                    FROM `{log}`
                    WHERE entity_type = 'company' AND direction = 'sent'
                ) WHERE rn = 1
            )
            SELECT t.name, IFNULL(t.contact_email, '') AS contact_email,
                   IFNULL(t.contact_name, '') AS contact_name,
                   IFNULL(t.bounced_email, '') AS bounced_email,
                   IFNULL(t.source, '') AS source,
                   s.counterparty_email AS sent_to, s.snippet,
                   CAST(s.sent_at AS STRING) AS sent_at
            FROM latest_sent s
            JOIN `{bq_handler.table_id}` t ON t.name = s.entity_name
            WHERE t.hidden_at IS NULL
        """)

        plan = []
        for r in rows:
            adopt = contact_adoption(
                r["contact_email"], r["contact_name"],
                r["sent_to"] or "", greeting_name(r["snippet"] or ""),
                is_test=r["source"] == "Internal Test",
                bounced_email=r["bounced_email"])
            if adopt["email"] or adopt["name"]:
                plan.append({"name": r["name"], "sent_at": r["sent_at"],
                             "email_from": r["contact_email"], "email_to": adopt["email"],
                             "name_from": r["contact_name"], "name_to": adopt["name"]})

        if dry_run:
            return {"status": "Preview", "dry_run": True,
                    "companies_with_sends": len(rows), "would_update": len(plan),
                    "changes": sorted(plan, key=lambda p: p["name"]),
                    "message": "Nothing was changed. Re-run with dry_run=0 to apply."}

        from google.cloud import bigquery as bq_lib
        applied, failures = [], []
        for p in plan:
            try:
                sets, params, changes = [], [], []
                if p["email_to"]:
                    sets.append("contact_email = @ce")
                    params.append(bq_lib.ScalarQueryParameter("ce", "STRING", p["email_to"]))
                    changes.append(f"email {p['email_from'] or '(none)'} -> {p['email_to']}")
                if p["name_to"]:
                    sets.append("contact_name = @cn")
                    params.append(bq_lib.ScalarQueryParameter("cn", "STRING", p["name_to"]))
                    changes.append(f"name {p['name_from'] or '(none)'} -> {p['name_to']}")
                params.append(bq_lib.ScalarQueryParameter("name", "STRING", p["name"]))
                bq_handler.client.query(
                    f"UPDATE `{bq_handler.table_id}` SET {', '.join(sets)} WHERE name = @name",
                    job_config=bq_lib.QueryJobConfig(query_parameters=params)).result()
                bq_handler.add_activity_note(
                    p["name"],
                    f"Contact retro-synced from the last outreach actually sent "
                    f"({', '.join(changes)}).", created_by="contact-sync")
                applied.append(p["name"])
            except Exception as e:
                # In the RESPONSE, not only the server log. A company missing
                # from `updated` with no stated reason cost a debugging round
                # trip: it was unknowable from the outside whether it had failed
                # here or been updated by an earlier, interrupted run.
                logger.warning(f"[Contact sync] {p['name']} failed: {e}")
                failures.append(f"{p['name']}: {e}")
        return {"status": "Success", "dry_run": False,
                "companies_with_sends": len(rows), "updated": applied,
                "failed": failures,
                "message": (f"Updated {len(applied)} contacts from what was actually sent."
                            + (f" {len(failures)} FAILED, see 'failed'." if failures else ""))}

    return _stream_json(_run)


# ── Deal Intelligence Chat ───────────────────────────────────────────────────
# NOTE: restored — the whole endpoint was accidentally deleted in eba21c0
# ("remove temporary /diag/test-loop endpoint", 15 Jul) and chat has been
# silently 404ing since. Now also connection-aware (investor_links).

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict]] = []
    web_search: Optional[bool] = False


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Chat over the database (companies + LPs + investor connections).
    Data-only answers; never guesses. web_search=True (user explicitly
    pressed the button) runs ONE grounded search against the shared budget.
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
    try:
        links = investor_handler.get_all_links()
    except Exception:
        links = []

    if req.web_search:
        _enforce_grounding_budget(1, "Chat web search")
        result = chat_web_search(req.message, req.history or [], universe, investors, links=links)
        try:
            bq_handler.log_smartfill("chat", kind="newslookup")  # weight-1 grounded call
        except Exception:
            pass
        return result

    return chat_answer(req.message, req.history or [], universe, investors, links=links)


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


class TriageRequest(BaseModel):
    track: str                              # 'A' | 'B' | 'kill' | '' to clear
    owner: Optional[str] = None             # set in the same write when known
    created_by: Optional[str] = "Ishu Ratna"


class OwnerRequest(BaseModel):
    owner: str                              # Bea | Ishu | Issam | Marianna | '' to clear
    created_by: Optional[str] = "Ishu Ratna"


# ── Triage + ownership (the Responded page) ──────────────────────────────────
# Process reference: docs/Averroes_Deal_Pipeline_Process.pdf.
#   Track A = high fit, goes to Bea via the fortnightly Thursday session.
#   Track B = low/moderate fit or too early, associate call agreed on Wednesday.
#   kill    = closed out. Ishu can do this alone, no meeting required.
# Both endpoints write through bq_handler so the Responded page, the Universe
# table and the Pipeline board can never drift apart on who owns a company.

_TRACK_LABELS = {
    "A": "Passed to Bea (high fit, Thursday session)",
    "B": "Passed to Issam/Marianna (associate call, allocated Wednesday)",
    "kill": "Not interested (closed out)",
    "later": "Talk later (parked, resurfaces for a decision in 6 months)",
}


@app.put("/company/{company_name}/triage")
async def triage_company(company_name: str, req: TriageRequest):
    """Record the triage decision on an Email 2 reply."""
    track = (req.track or "").strip()
    if track and track not in bq_handler.TRACKS:
        raise HTTPException(status_code=400,
                            detail=f"Invalid track. Must be one of: {list(bq_handler.TRACKS)} (or blank to clear).")
    try:
        ok = bq_handler.set_track(company_name, track, owner=req.owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"'{company_name}' not found.")

    note = (f"Triaged: {_TRACK_LABELS.get(track, track)}" if track else "Triage cleared")
    if req.owner is not None:
        note += f" — owner set to {req.owner or 'unassigned'}"
    try:
        bq_handler.add_activity_note(company_name, note, req.created_by)
    except Exception as e:
        logger.warning(f"triage note failed for {company_name} (non-fatal): {e}")
    return {"status": "Success", "company": company_name, "track": track, "owner": req.owner}


@app.put("/company/{company_name}/owner")
async def set_company_owner(company_name: str, req: OwnerRequest):
    """Assign who is managing a company. One field: it changes hands from Ishu
    to the assigned associate on loop-in."""
    try:
        ok = bq_handler.set_owner(company_name, req.owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"'{company_name}' not found.")
    try:
        bq_handler.add_activity_note(
            company_name, f"Owner set to {req.owner or 'unassigned'}", req.created_by)
    except Exception as e:
        logger.warning(f"owner note failed for {company_name} (non-fatal): {e}")
    return {"status": "Success", "company": company_name, "owner": req.owner}


# Which queue a replied-to company sits in. Derived, never stored: the moment
# an email is sent or a track is set, the company moves group on its own.
_LIVE_CALL_STAGES = ("Responded", "Meeting", "DD", "Offer")


TALK_LATER_DAYS = int(os.getenv("TALK_LATER_DAYS", "180"))


def _responded_group(r: dict) -> str:
    track = (r.get("track") or "").strip()
    status = r.get("status") or ""
    owner = (r.get("owner") or "").strip()
    sent = int(r.get("sent_count") or 0)
    recv = int(r.get("recv_count") or 0)

    if status in ("Lost", "Not a Fit") or track == "kill":
        return "closed"
    if track == "later":
        # Asleep for 6 months from the decision, then RESURFACES for a fresh
        # decision. Derived from triaged_at at read time — the company simply
        # starts grouping as needs_triage again one morning, with no cron and
        # no stored flag to get stale.
        # Explicit falsy guard: _as_date falls back to today for unparseable
        # input, which would make a missing timestamp look freshly parked and
        # sleep the company forever.
        raw = r.get("triaged_at")
        t = _as_date(raw) if raw else None
        from datetime import date as _date
        if t and (_date.today() - t).days < TALK_LATER_DAYS:
            return "talk_later"
        return "needs_triage"
    if not track:
        # No reply in the log at all: the company is here because a person put it
        # in Responded and confirmed a reply exists off-record (a phone call).
        # Email counts cannot say what it needs, so a human decides.
        if recv == 0:
            return "needs_triage"
        # Not triaged yet. One email out means they answered Email 1 and are
        # owed Email 2; two or more means they answered the real ask.
        return "needs_email_2" if sent <= 1 else "needs_triage"
    if track == "A":
        return "track_a_call_done" if status in ("Meeting", "DD", "Offer") else "track_a_awaiting_thursday"
    # Track B
    if owner in ("Issam", "Marianna"):
        return "track_b_call_done" if status in ("Meeting", "DD", "Offer") else "track_b_assigned"
    return "track_b_awaiting_wednesday"


@app.get("/responded")
async def get_responded():
    """The Responded page: every company that has ever replied, grouped by what
    it needs next, plus the per-associate open-call counts that decide the
    Wednesday allocation."""
    rows = bq_handler.get_responded()
    for r in rows:
        r["queue"] = _responded_group(r)

    groups: Dict[str, List[dict]] = {}
    for r in rows:
        groups.setdefault(r["queue"], []).append(r)

    # Open founder conversations per associate: what "whoever has fewer live
    # conversations" actually means, counted rather than remembered.
    open_calls = {a: 0 for a in ("Bea", "Ishu", "Issam", "Marianna")}
    for r in rows:
        o = (r.get("owner") or "").strip()
        if o in open_calls and r["queue"] not in ("closed", "talk_later") and (r.get("status") or "") in _LIVE_CALL_STAGES:
            open_calls[o] += 1

    return {
        "total": len(rows),
        "counts": {k: len(v) for k, v in groups.items()},
        "open_calls": open_calls,
        "owners": list(bq_handler.OWNERS),
        "companies": rows,
    }


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
        action_bucket = NULL, action_rationale = NULL, action_follow_up_date = NULL,
        action_set_at = NULL, action_reply_subject = NULL, action_reply_body = NULL,
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


@app.get("/company/{company_name}/emails")
async def get_company_emails(company_name: str, limit: int = Query(30, description="Max messages")):
    """Email thread for one company from the email_log (newest first)."""
    from google.cloud import bigquery as bq_lib
    try:
        table_id = bq_handler._ensure_email_log_table()
        rows = bq_handler.client.query(
            f"""SELECT direction, counterparty_email, subject, snippet, classification, summary,
                       CAST(sent_at AS STRING) AS sent_at
                FROM `{table_id}` WHERE entity_name = @name
                ORDER BY sent_at DESC LIMIT {max(1, min(limit, 100))}""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("name", "STRING", company_name),
            ])).result()
        emails = [dict(r) for r in rows]
        return {"company": company_name, "emails": emails, "count": len(emails)}
    except Exception as e:
        logger.warning(f"Email thread fetch failed for {company_name}: {e}")
        return {"company": company_name, "emails": [], "count": 0}


# ── IC Memo (one-pager for Responded companies) ──────────────────────────────

@app.post("/company/{company_name}/ic-memo")
async def generate_ic_memo(company_name: str):
    """
    Generate the one-page IC memo: all numbers assembled in code from the
    stored record (source-labelled), narrative written by ONE grounded Gemini
    call (weight 1). Persisted to ic_memo/ic_memo_at; regenerating overwrites.
    """
    from services.ic_memo_service import build_ic_memo
    from google.cloud import bigquery as bq_lib

    _enforce_grounding_budget(1, "IC memo")
    company = next((c for c in bq_handler.get_universe() if c.get("name") == company_name), None)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found")

    emails = (await get_company_emails(company_name, limit=30)).get("emails", [])
    memo = build_ic_memo(company, emails)

    bq_handler.client.query(
        f"""UPDATE `{bq_handler.table_id}` SET ic_memo = @memo, ic_memo_at = CURRENT_TIMESTAMP()
            WHERE name = @name""",
        job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("memo", "STRING", json.dumps(memo)),
            bq_lib.ScalarQueryParameter("name", "STRING", company_name),
        ])).result()
    bq_handler.log_smartfill(company_name, kind="icmemo")
    bq_handler.add_activity_note(company_name, "IC memo generated (one-pager)", "icmemo")
    return {"status": "Success", "memo": memo}


@app.get("/company/{company_name}/ic-memo.pdf")
async def ic_memo_pdf(company_name: str):
    """Render the stored IC memo as a one-page PDF (generate it first)."""
    from fastapi.responses import Response
    from services.ic_memo_pdf import render_ic_memo_pdf

    company = next((c for c in bq_handler.get_universe() if c.get("name") == company_name), None)
    if not company or not company.get("ic_memo"):
        raise HTTPException(status_code=404, detail="No IC memo stored — generate it first.")
    memo = json.loads(company["ic_memo"]) if isinstance(company["ic_memo"], str) else company["ic_memo"]
    pdf_bytes = render_ic_memo_pdf(memo)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", company_name)
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="IC_Memo_{safe}.pdf"'})


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

def _as_date(ts) -> "date_cls":
    """Best-effort date from whatever the mailbox handed us (ISO string or
    datetime). Falls back to today rather than raising, since a missing
    timestamp must never stop a sync."""
    from datetime import date as date_cls, datetime as dt_cls
    if isinstance(ts, dt_cls):
        return ts.date()
    if isinstance(ts, date_cls):
        return ts
    s = str(ts or "")[:10]
    try:
        return dt_cls.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date_cls.today()


def _apply_ooo(entry: dict) -> dict:
    """Record an out-of-office autoresponder against its company.

    Three things happen, and NOT the fourth:
      * ooo_until / ooo_note are stamped so /followups can push the reminder
        out past their return date.
      * Any reply-derived state is cleared, because an autoresponder is not a
        reply and must not leave a reply chip or an action bucket behind.
      * If a previous sync had advanced this company to Responded off the back
        of this autoresponder, it is pulled straight back to Contacted.
      * We do NOT advance the stage and do NOT spend an action-bucket call.
    """
    from google.cloud import bigquery as bq_lib
    name = entry["entity_name"]
    got = entry.get("_ooo") or {}
    until = got.get("until")
    until_s = until.isoformat() if until else ""
    sent_on = _as_date(entry.get("sent_at"))

    if until:
        from services.ooo_detect import followup_days as _fd
        days = _fd(sent_on, until)
        note = (f"Out of office until {until_s} (read from their autoresponder "
                f"by {got.get('date_source') or 'pattern'}). Follow-up reminder moved to "
                f"{days} days after our email.")
    else:
        note = ("Out of office, no return date stated in the autoresponder. "
                "Follow-up reminder stays at 14 days.")

    bq_handler.client.query(
        f"""UPDATE `{bq_handler.table_id}` SET
                ooo_until = @until, ooo_note = @note,
                last_reply_at = NULL, reply_classification = NULL,
                action_bucket = NULL, action_rationale = NULL,
                action_follow_up_date = NULL, action_set_at = NULL,
                action_reply_subject = NULL, action_reply_body = NULL
            WHERE name = @name""",
        job_config=bq_lib.QueryJobConfig(query_parameters=[
            bq_lib.ScalarQueryParameter("until", "STRING", until_s),
            bq_lib.ScalarQueryParameter("note", "STRING", note),
            bq_lib.ScalarQueryParameter("name", "STRING", name),
        ])).result()

    # An autoresponder must never leave a company sitting in Responded. If an
    # earlier sync advanced it (before OOO was understood), pull it back now.
    pulled = False
    try:
        rows = list(bq_handler.client.query(
            f"SELECT status, outreach_sent_at FROM `{bq_handler.table_id}` WHERE name = @name LIMIT 1",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("name", "STRING", name)])).result())
        if rows and rows[0].status == "Responded":
            target = "Contacted" if rows[0].outreach_sent_at else "Qualified"
            bq_handler.update_company_status(name, target, created_by="email-sync")
            pulled = True
            note += f" Pulled back to {target}: an out-of-office is not a reply."
    except Exception as e:
        logger.warning(f"OOO pull-back check failed for {name}: {e}")

    bq_handler._log_activity(name, "note", "email-sync", note_text=note,
                             event_time=entry.get("sent_at"))
    logger.info(f"[OOO] {name}: until={until_s or 'unknown'} pulled_back={pulled}")
    return {"name": name, "until": until_s, "date_source": got.get("date_source", ""),
            "pulled_back": pulled}


# ── Delivery verification ────────────────────────────────────────────────────
# "Sent" from SMTP's point of view is not the same as "a human received it".
# Two independent failures, one shared consequence: back to Qualified.
#
#   BOUNCE     — a mailer-daemon report came back. The address is dead, so it is
#                cleared (kept in bounced_email) and the contact waterfall can
#                find a new one.
#   NEVER SENT — no outbound message for the company exists anywhere in the
#                mailbox, so nothing was actually delivered to anybody.
#
# Runs inside the email sync, which has already read the mailbox, so this costs
# no extra IMAP work and no AI.

def _verify_delivery(dry_run: bool = False, window_days: int = 30,
                     grace_hours: int = 12, limit: int = 5000) -> dict:
    from services.delivery_check import classify_delivery

    our_address = os.getenv("OUTREACH_EMAIL", "beatrice@averroescapital.com")
    rows = bq_handler.get_received_log(limit=limit)

    # Bounces first. A company is only pulled back if the bounce is its NEWEST
    # inbound message: an address can fail once and be fixed, and a genuine reply
    # arriving afterwards proves someone is reading. Same guard the OOO pass uses.
    newest: Dict[str, dict] = {}
    for r in rows:
        n = r.get("entity_name") or ""
        if n and (n not in newest or str(r.get("sent_at") or "") > str(newest[n].get("sent_at") or "")):
            newest[n] = r

    bounces, to_mark = [], []
    for r in rows:
        got = classify_delivery(r.get("subject", ""), r.get("snippet", ""),
                                from_addr=r.get("counterparty_email", ""),
                                our_address=our_address)
        if not got["is_bounce"]:
            continue
        # Always classify the message itself, even if the company is not pulled
        # back. That is a fact about the message, and it stops a mailer-daemon
        # report ever counting as a genuine reply.
        if r.get("classification") != "bounce":
            to_mark.append(r["message_id"])
        name = r.get("entity_name") or ""
        if newest.get(name, {}).get("message_id") != r.get("message_id"):
            continue  # something newer arrived; the mailbox is alive
        bounces.append({"name": name, "address": got["address"] or "",
                        "reason": got["reason"], "subject": r.get("subject", ""),
                        "at": r.get("sent_at", "")})

    # Sends with no trace in the mailbox at all.
    missing = bq_handler.unverified_sends(window_days=window_days, grace_hours=grace_hours)

    # Only companies still sitting in Contacted are pulled back. If one has since
    # reached Meeting or beyond, real work has happened and a stale bounce report
    # must not undo it.
    live = {}
    try:
        for c in bq_handler.get_universe():
            live[c.get("name")] = c
    except Exception as e:
        logger.warning(f"[Delivery] universe read failed: {e}")

    def _eligible(name: str) -> bool:
        c = live.get(name) or {}
        return (c.get("status") == "Contacted"
                and c.get("source") != "Internal Test"
                and not c.get("hidden_at"))

    plan = ([{**b, "kind": "bounced"} for b in bounces if _eligible(b["name"])]
            + [{"name": m["name"], "address": "", "kind": "not_sent",
                "reason": "no outbound message for this company exists in the mailbox",
                "subject": "", "at": m.get("outreach_sent_at", "")}
               for m in missing if _eligible(m["name"])])

    if dry_run:
        return {
            "status": "Preview", "dry_run": True,
            "scanned_messages": len(rows),
            "bounces_found": len(bounces),
            "messages_to_mark": len(to_mark),
            "sends_missing_from_mailbox": len(missing),
            "would_pull_back": len(plan),
            "companies": sorted(plan, key=lambda p: (p["kind"], p["name"])),
            "message": "Nothing was changed. Re-run with dry_run=0 to apply.",
        }

    marked = 0
    if to_mark:
        try:
            marked = bq_handler.mark_emails_classification(to_mark, "bounce")
        except Exception as e:
            logger.warning(f"[Delivery] marking bounces failed: {e}")

    applied = []
    for p in plan:
        if bq_handler.pull_back_undelivered(p["name"], p["reason"], p["address"], p["kind"]):
            applied.append(f"{p['name']} ({p['kind']})")

    # Positive evidence for everyone else, so the check is cheap next time.
    verified = [c["name"] for c in live.values()
                if c.get("status") in ("Contacted", "Responded", "Meeting", "DD", "Offer")
                and c.get("name") not in {p["name"] for p in plan}] if live else []
    try:
        bq_handler.mark_delivered(verified[:2000])
    except Exception as e:
        logger.warning(f"[Delivery] marking delivered failed: {e}")

    return {
        "status": "Success", "dry_run": False,
        "scanned_messages": len(rows),
        "bounces_found": len(bounces),
        "messages_marked": marked,
        "sends_missing_from_mailbox": len(missing),
        "pulled_back": applied,
        "message": (f"Scanned {len(rows)} inbound messages, found {len(bounces)} bounces, "
                    f"{len(missing)} sends with no trace in the mailbox. "
                    f"Pulled {len(applied)} back to Qualified."),
    }


def _ooo_backfill(dry_run: bool, ai_budget: int, limit: int) -> dict:
    """Retro pass: apply out-of-office handling to mail already in the log.

    Everything the live sync now does for a fresh autoresponder, applied to the
    history. Possible without touching the mailbox because email_log already
    stores each message's subject and snippet, so an OOO logged before
    detection existed can simply be re-read.

    Order matters:
      1. Re-scan every stored inbound and mark the autoresponders.
      2. Per company, take the LATEST OOO and stamp ooo_until, so the reminder
         reflects the most recent thing they told us.
      3. Clear reply-derived state and pull back to Contacted, but ONLY for
         companies with no genuine reply. A company that sent an OOO and later
         actually replied keeps its reply state and its stage.
      4. Run the general pull-back reconciliation to catch companies advanced
         off a reply that no longer exists at all.
    """
    from services.ooo_detect import (MAX_LEAVE_DAYS, detect as ooo_detect,
                                     followup_days as ooo_days, is_auto_reply,
                                     parse_return_date)

    rows = bq_handler.get_received_log(limit=limit)

    # Newest inbound per company (rows arrive newest first). An out-of-office
    # only describes the CURRENT state if nothing has come in since: a company
    # that sent an autoresponder in July and then actually replied in August is
    # not out of office, and stamping the stale return date would wrongly defer
    # their reminder.
    newest_inbound: dict = {}
    for r in rows:
        newest_inbound.setdefault(r.get("entity_name"), r.get("message_id"))

    # 1. Which stored messages are autoresponders?
    hits, ai_used = [], 0
    for r in rows:
        subj, snip = r.get("subject") or "", r.get("snippet") or ""
        if not is_auto_reply(subj, snip):
            continue
        on = _as_date(r.get("sent_at"))
        until = parse_return_date(subj, snip, on)
        # Same plausibility gate detect() applies: a date beyond the cap is a
        # misread, and an implausible date is worse than none because it mutes
        # follow-up indefinitely instead of keeping the honest 14-day rule.
        if until and (until - on).days > MAX_LEAVE_DAYS:
            until = None
        source = "pattern" if until else ""
        if not until and ai_used < ai_budget:
            ai_used += 1
            got = ooo_detect(subj, snip, received_on=on, allow_ai=True)
            until, source = got.get("until"), got.get("date_source", "")
        hits.append({"message_id": r.get("message_id"), "name": r.get("entity_name"),
                     "sent_at": r.get("sent_at"), "until": until, "date_source": source,
                     "already_marked": r.get("classification") == "out_of_office",
                     "subject": subj[:120]})

    # A company "genuinely replied" only if it has an inbound that is neither
    # already marked out_of_office NOR identified as one by THIS run. Reading
    # the log's current classification alone under-counts on the first pass:
    # an unmarked autoresponder looked like a real reply and suppressed its own
    # company's pull-back.
    ooo_ids = {h["message_id"] for h in hits if h.get("message_id")}
    genuine = {r.get("entity_name") for r in rows
               if r.get("classification") != "out_of_office"
               and r.get("message_id") not in ooo_ids}

    # 2. Per company, the OOO only counts if it is their newest inbound.
    # Autoresponders that were later superseded by a genuine reply are marked in
    # the log (so they never count as a reply) but do not set ooo_until.
    latest: dict = {}
    for h in hits:
        if h["message_id"] == newest_inbound.get(h["name"]):
            latest.setdefault(h["name"], h)

    to_mark = [h["message_id"] for h in hits if h["message_id"] and not h["already_marked"]]

    # 3. Who can be corrected: no genuine reply on record.
    universe = {c["name"]: c for c in bq_handler.get_universe_slim()}
    plan = []
    for name, h in latest.items():
        c = universe.get(name)
        if not c or c.get("source") == "Internal Test":
            continue
        has_genuine = name in genuine
        status = c.get("status") or ""
        target = ""
        if not has_genuine and status == "Responded":
            target = "Contacted" if c.get("outreach_sent_at") else "Qualified"
        until_s = h["until"].isoformat() if h["until"] else ""
        days = None
        if h["until"]:
            days = ooo_days(_as_date(h["sent_at"]), h["until"])
        plan.append({
            "name": name, "status": status, "ooo_until": until_s,
            "date_source": h["date_source"], "reminder_days": days,
            "has_genuine_reply": has_genuine,
            "pull_back_to": target, "subject": h["subject"],
        })

    if dry_run:
        return {
            "status": "Preview",
            "dry_run": True,
            "scanned_messages": len(rows),
            "autoresponders_found": len(hits),
            "messages_to_mark": len(to_mark),
            "companies_affected": len(plan),
            "with_return_date": sum(1 for p in plan if p["ooo_until"]),
            "reminder_deferred": sum(1 for p in plan if (p["reminder_days"] or 0) > 14),
            "would_pull_back": sum(1 for p in plan if p["pull_back_to"]),
            "ai_calls_used": ai_used,
            "companies": sorted(plan, key=lambda p: (not p["pull_back_to"], p["name"])),
            "reply_rule_preview": bq_handler.reconcile_reply_stages(dry_run=True),
            "message": ("Nothing was changed. Re-run with dry_run=0 to apply."),
        }

    # About to clear reply state and pull stages back on real rows.
    archive = _archive_before("out-of-office backfill")

    marked = bq_handler.mark_emails_classification(to_mark, "out_of_office") if to_mark else 0

    applied, pulled = [], []
    for p in plan:
        name = p["name"]
        try:
            note = (f"Out of office until {p['ooo_until']} (backfilled from the stored email log). "
                    f"Follow-up reminder set to {p['reminder_days']} days after our email."
                    if p["ooo_until"] else
                    "Out of office, no return date stated. Follow-up reminder stays at 14 days.")
            # Reply-derived fields are cleared ONLY where there is no genuine
            # reply, so a real conversation is never wiped by this pass.
            clear = ("" if p["has_genuine_reply"] else
                     ", last_reply_at = NULL, reply_classification = NULL,"
                     " action_bucket = NULL, action_rationale = NULL,"
                     " action_follow_up_date = NULL, action_set_at = NULL,"
                     " action_reply_subject = NULL, action_reply_body = NULL")
            bq_handler.client.query(
                f"""UPDATE `{bq_handler.table_id}`
                    SET ooo_until = @until, ooo_note = @note{clear}
                    WHERE name = @name""",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("until", "STRING", p["ooo_until"]),
                    bq_lib.ScalarQueryParameter("note", "STRING", note),
                    bq_lib.ScalarQueryParameter("name", "STRING", name),
                ])).result()
            if p["pull_back_to"]:
                bq_handler.update_company_status(name, p["pull_back_to"], created_by="email-sync")
                note += f" Pulled back to {p['pull_back_to']}: an out-of-office is not a reply."
                pulled.append(f"{name} -> {p['pull_back_to']}")
            bq_handler.add_activity_note(name, note, created_by="email-sync")
            applied.append(name)
        except Exception as e:
            logger.warning(f"[OOO backfill] {name} failed: {e}")

    stale = bq_handler.reconcile_reply_stages()

    return {
        "status": "Success",
        "dry_run": False,
        "archive_snapshot": archive,
        "scanned_messages": len(rows),
        "autoresponders_found": len(hits),
        "messages_marked": marked,
        "companies_updated": len(applied),
        "pulled_back": pulled,
        "reply_rule": stale,
        "ai_calls_used": ai_used,
        "message": (f"Scanned {len(rows)} logged inbound messages, found {len(hits)} autoresponders, "
                    f"marked {marked}, updated {len(applied)} companies, pulled {len(pulled)} back "
                    f"out of Responded. The reply rule then advanced {len(stale['promote'])}, moved "
                    f"{len(stale['demote'])} back, and left {len(stale['needs_confirmation'])} "
                    f"awaiting your confirmation."),
    }


# ── Append-only archive + off-site backup ────────────────────────────────────
# The live tables are mutable by design; these two are how the 12,000 companies
# and their enrichment survive a bad job or a lost dataset. See
# services/archive_service.py for the doctrine: the archive is WRITE-ONLY for
# the application and is never read to decide what is currently true.

BACKUP_BUCKET = os.getenv("BACKUP_BUCKET", "averroes-deal-archive")


@app.post("/admin/archive/run")
async def archive_run(request: Request,
                      dry_run: int = Query(0, description="1 = report what would be appended, write nothing"),
                      force: int = Query(0, description="1 = re-archive every row, not just changed ones"),
                      note: str = Query("", description="Why this snapshot was taken")):
    """Append the current state of every changed company to targets_archive.

    Appends only. Nothing existing is ever modified or removed. Run it before
    anything destructive, and on a daily schedule.
    """
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    from services.archive_service import archive_targets
    return _stream_json(lambda: archive_targets(
        bq_handler, change_note=note or "manual run",
        force=bool(force), dry_run=bool(dry_run)))


@app.post("/admin/backup/export")
async def backup_export(request: Request,
                        bucket: str = Query("", description="Override the backup bucket"),
                        prefix: str = Query("bigquery", description="Path prefix inside the bucket")):
    """Export every table to the backup bucket as gzipped newline JSON.

    Protects against losing BigQuery itself, which the archive table cannot.
    Dated, timestamped paths mean an export can never overwrite an earlier one.
    """
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    from services.archive_service import export_tables_to_gcs
    target = bucket or BACKUP_BUCKET
    return _stream_json(lambda: export_tables_to_gcs(bq_handler, target, prefix))


@app.get("/admin/archive/history/{company_name}")
async def archive_history(company_name: str, request: Request,
                          limit: int = Query(50, description="Max versions to return")):
    """Every archived version of one company, newest first, with a field-level
    diff against the version before it."""
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    from services.archive_service import company_history
    versions = company_history(bq_handler, company_name, limit=limit)
    return {"company": company_name, "versions_held": len(versions), "history": versions}


def _archive_before(note: str) -> dict:
    """Snapshot before a destructive rewrite. Best-effort: a failure here must
    not block the migration, but it IS reported so a migration that ran without
    a restore point behind it is never silent."""
    try:
        from services.archive_service import archive_targets
        res = archive_targets(bq_handler, change_note=f"pre-migration: {note}", force=True)
        return {"ok": True, "appended": res.get("appended"), "note": res.get("message")}
    except Exception as e:
        logger.error(f"[Archive] pre-migration snapshot FAILED for '{note}': {e}")
        return {"ok": False, "error": str(e),
                "warning": "This migration ran WITHOUT a fresh archive snapshot behind it."}


def _stage_rename(dry_run: bool) -> dict:
    """One-off migration: retire 'Engaged' and align stored stages with the UI.

        old 'Engaged'   -> 'Contacted'   (we emailed them)
        old 'Contacted' -> 'Responded'   (they replied)

    'Contacted' previously meant "they replied" and was DISPLAYED as Responded,
    while the send step wrote 'Engaged'. Both stored values now say exactly what
    they mean, so the display translation is gone.

    ORDER IS CRITICAL AND NOT INTERCHANGEABLE. Contacted -> Responded must run
    FIRST. Doing Engaged -> Contacted first would rename the old Engaged rows to
    Contacted, and the second statement would then sweep them straight on to
    Responded, silently promoting every company we had merely emailed into
    looking like it had replied.

    Covers targets.status, the activity_log history (old_status and new_status,
    so past stage changes still read correctly), and backfills responded_at from
    last_reply_at for rows entering Responded.

    The analytics ledger is deliberately NOT rewritten: its outreach counts come
    from email evidence ('emailed' / 'replied' events), not stage names, so it
    is already immune to this rename.
    """
    targets = bq_handler.table_id
    activity = bq_handler.activity_table_id

    def count(sql: str) -> int:
        rows = list(bq_handler.client.query(sql).result())
        return int(rows[0].n) if rows else 0

    before = {
        "targets_engaged": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Engaged'"),
        "targets_contacted": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Contacted'"),
        "targets_responded": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Responded'"),
        "activity_new_engaged": count(f"SELECT COUNT(*) AS n FROM `{activity}` WHERE new_status = 'Engaged'"),
        "activity_new_contacted": count(f"SELECT COUNT(*) AS n FROM `{activity}` WHERE new_status = 'Contacted'"),
        "activity_old_engaged": count(f"SELECT COUNT(*) AS n FROM `{activity}` WHERE old_status = 'Engaged'"),
        "activity_old_contacted": count(f"SELECT COUNT(*) AS n FROM `{activity}` WHERE old_status = 'Contacted'"),
    }

    if dry_run:
        return {
            "status": "Preview",
            "dry_run": True,
            "before": before,
            "would_become": {
                "Contacted (we emailed them)": before["targets_engaged"],
                "Responded (they replied)": before["targets_contacted"],
                "already Responded (left alone)": before["targets_responded"],
            },
            "activity_rows_to_rewrite":
                before["activity_new_engaged"] + before["activity_new_contacted"]
                + before["activity_old_engaged"] + before["activity_old_contacted"],
            "order": ["1. Contacted -> Responded", "2. Engaged -> Contacted"],
            "message": ("Nothing was changed. Re-run with dry_run=0 to apply. "
                        "Step 1 must precede step 2 or every emailed company would be "
                        "promoted to Responded."),
        }

    # Every stage value is about to be rewritten. Snapshot first so the
    # previous state of all 12,000 rows is preserved before anything moves.
    archive = _archive_before("stage rename (Engaged -> Contacted -> Responded)")

    steps = []
    # ── STEP 1: they-replied moves out of the way FIRST ──
    for label, sql in [
        ("targets: Contacted -> Responded",
         f"UPDATE `{targets}` SET status = 'Responded' WHERE status = 'Contacted'"),
        ("activity_log new_status: Contacted -> Responded",
         f"UPDATE `{activity}` SET new_status = 'Responded' WHERE new_status = 'Contacted'"),
        ("activity_log old_status: Contacted -> Responded",
         f"UPDATE `{activity}` SET old_status = 'Responded' WHERE old_status = 'Contacted'"),
        # ── STEP 2: only now may Engaged take the freed name ──
        ("targets: Engaged -> Contacted",
         f"UPDATE `{targets}` SET status = 'Contacted' WHERE status = 'Engaged'"),
        ("activity_log new_status: Engaged -> Contacted",
         f"UPDATE `{activity}` SET new_status = 'Contacted' WHERE new_status = 'Engaged'"),
        ("activity_log old_status: Engaged -> Contacted",
         f"UPDATE `{activity}` SET old_status = 'Contacted' WHERE old_status = 'Engaged'"),
        # responded_at: first entry into Responded. last_reply_at is when they
        # actually replied, which is precisely what this stage records.
        ("backfill responded_at from last_reply_at",
         f"""UPDATE `{targets}` SET responded_at = last_reply_at
             WHERE responded_at IS NULL AND last_reply_at IS NOT NULL"""),
    ]:
        job = bq_handler.client.query(sql)
        job.result()
        n = int(job.num_dml_affected_rows or 0)
        steps.append({"step": label, "rows": n})
        logger.info(f"[stage-rename] {label}: {n} rows")

    after = {
        "targets_engaged": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Engaged'"),
        "targets_contacted": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Contacted'"),
        "targets_responded": count(f"SELECT COUNT(*) AS n FROM `{targets}` WHERE status = 'Responded'"),
    }
    return {
        "status": "Success",
        "dry_run": False,
        "archive_snapshot": archive,
        "before": before,
        "steps": steps,
        "after": after,
        "engaged_remaining": after["targets_engaged"],
        "message": (f"Renamed {before['targets_contacted']} companies to Responded and "
                    f"{before['targets_engaged']} to Contacted. "
                    f"'Engaged' rows remaining: {after['targets_engaged']} (should be 0)."),
    }


@app.post("/admin/stage-rename")
async def stage_rename(request: Request,
                       dry_run: int = Query(1, description="1 = preview only (default), 0 = apply")):
    """Retire 'Engaged'. Renames stored stages so they match the board:
    Engaged -> Contacted (we emailed them), Contacted -> Responded (they replied).

    Defaults to a PREVIEW. Idempotent: re-running after it has been applied
    finds nothing left to change.
    """
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return _stream_json(lambda: _stage_rename(bool(dry_run)))


@app.post("/email/ooo-backfill")
async def ooo_backfill(request: Request,
                       dry_run: int = Query(1, description="1 = preview only (default), 0 = apply"),
                       ai_budget: int = Query(0, description="Max AI calls for unparseable dates"),
                       limit: int = Query(5000, description="Max logged inbound messages to scan")):
    """Retro-apply out-of-office handling to companies already in dialogue.

    Defaults to a PREVIEW: it reports exactly what it would change and writes
    nothing. Pass dry_run=0 to apply. Pattern matching only unless ai_budget is
    raised, so a preview costs nothing.
    """
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    return _stream_json(lambda: _ooo_backfill(bool(dry_run),
                                              max(0, min(ai_budget, 200)),
                                              max(1, min(limit, 20000))))


# ── The reply rule ───────────────────────────────────────────────────────────
# Qualified = not yet emailed. Contacted = emailed, no genuine reply yet.
# Responded = emailed and they replied. An out-of-office is not a reply.
#
# This endpoint is the only way to bring status back in line with that rule, and
# the nightly email sync calls the same handler method, so there is exactly one
# implementation. Meeting / DD / Offer / Won / Lost are never touched.

def _require_token(request: Request) -> None:
    """Guard for endpoints listed in auth.py EXEMPT_PATHS.

    Exempting a path skips Google sign-in, so WITHOUT this the endpoint is open to
    the internet. Every exempt path must call this. The two lists are a matched
    pair: add to EXEMPT_PATHS, add this call, or the endpoint is either
    unreachable from a terminal or unprotected. Getting that pairing wrong is the
    single easiest way to expose a data-mutating endpoint in this codebase.
    """
    token = request.headers.get("X-Watch-Token", "") or request.query_params.get("token", "")
    expected = os.getenv("WATCH_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")


@app.post("/delivery/verify")
async def delivery_verify(
        request: Request,
        dry_run: int = Query(1, description="1 = preview only (default), 0 = apply"),
        window_days: int = Query(30, description="Only judge sends inside the period the sync has scanned"),
        grace_hours: int = Query(12, description="Ignore sends newer than this — Gmail files to Sent with a lag")):
    """Did the outreach actually reach a human?

    Pulls a company back to Qualified when the email bounced (address dead, so it
    is cleared) or when no outbound message for it exists in the mailbox at all
    (SMTP said fine, nothing was filed). Defaults to a PREVIEW.

    Also classifies bounce messages in email_log as 'bounce', so a mailer-daemon
    report can never be counted as a genuine reply.
    """
    _require_token(request)
    return _stream_json(lambda: _verify_delivery(
        bool(dry_run), max(1, min(window_days, 3650)), max(0, min(grace_hours, 240))))


@app.post("/reply-rule/reconcile")          # browser: Google sign-in (the UI button)
@app.post("/admin/reply-rule/reconcile")    # terminal: WATCH_TOKEN (ops, sign-in exempt)
async def reply_rule_reconcile(
        request: Request,
        dry_run: int = Query(1, description="1 = preview only (default), 0 = apply"),
        confirm: str = Query("", description="Comma-separated company names the user has agreed to move back")):
    """Reconcile Contacted/Responded against the email log.

    Defaults to a PREVIEW. Returns three lists:

      promote            - a real reply exists, status had not caught up. Applied
                           automatically: the evidence is there.
      demote             - no reply on record and email-sync made the move.
                           Applied automatically: the machine corrects itself.
      needs_confirmation - no reply on record but a person moved it, or nothing
                           records how it got there. NEVER moved silently. The UI
                           asks; a yes comes back through `confirm`, a no calls
                           /company/{name}/reply-exempt to pin it instead.

    TWO PATHS, ONE HANDLER. The browser hits /reply-rule/reconcile and is covered
    by Google sign-in. A terminal cannot hold a session, so the /admin/ alias is
    exempt from sign-in and requires WATCH_TOKEN instead. Same function either
    way — an ops copy of this logic would be a second implementation of the reply
    rule and would eventually disagree with the button.
    """
    if request.url.path.startswith("/admin/"):
        _require_token(request)
    names = [n.strip() for n in (confirm or "").split(",") if n.strip()]
    out = bq_handler.reconcile_reply_stages(dry_run=bool(dry_run), confirm_names=names)
    return {
        "status": "Success",
        "dry_run": bool(dry_run),
        "counts": {k: len(v) for k, v in out.items()},
        **out,
        "message": ("Nothing was changed. Re-run with dry_run=0 to apply."
                    if dry_run else
                    f"Advanced {len(out['promote'])}, moved {len(out['demote'])} back, "
                    f"{len(out['needs_confirmation'])} still need your confirmation."),
    }


@app.put("/company/{company_name}/reply-exempt")
async def set_reply_exempt(company_name: str,
                           on: int = Query(1, description="1 = keep in Responded regardless of the email log, 0 = clear"),
                           by: str = Query("Ishu Ratna", description="Who confirmed it")):
    """Answer 'keep it' to the confirmation prompt.

    Records that a genuine reply exists even though the mailbox has no record of
    one (a phone call, or a reply from an address we do not track). The rule then
    skips this company permanently, so the same question is never asked twice.
    """
    ok = bq_handler.set_reply_exempt(company_name, by=by, on=bool(on))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found.")
    return {"status": "Success", "company": company_name, "reply_exempt": bool(on)}


@app.get("/email/sync/run")   # GET alias so a scheduler URL is enough
@app.post("/email/sync/run")
async def email_sync_run(request: Request,
                         days: int = Query(7, description="How many days back to scan")):
    """The 6 AM sync: same pipeline as the UI button, token-gated for Cloud
    Scheduler (which cannot hold a browser session).

    One run does the whole morning routine in order: read the mailbox, log new
    messages, classify replies, verify delivery (bounces classified BEFORE the
    reply rule reads the log), apply the reply rule in both directions, detect
    out-of-office deferrals. By the time anyone opens the board, yesterday's
    replies are already reflected in it.

    A 7-day window is plenty for a daily run (each scan overlaps the last six)
    while keeping the IMAP pass fast; the delivery check widens itself to 30
    days internally.
    """
    # deep=False EXPLICITLY. When an endpoint function is called directly (not
    # via HTTP), an omitted Query parameter is not its declared default but the
    # Query(...) marker object itself - which is truthy. Every scheduled sync
    # was therefore silently running in deep mode (scanned_days: 3650 in the
    # response gave it away). Same trap as everywhere we call handlers as
    # functions: pass every flag explicitly.
    _require_token(request)
    return await sync_emails(days=max(1, min(days, 365)), deep=False)


@app.post("/email/sync")
async def sync_emails(days: int = Query(30, description="How many days back to scan"),
                      deep: bool = Query(False, description="Search per known contact — captures full history from the start")):
    """
    Sync Beatrice's Gmail (IMAP, same App Password as sending) against known
    contacts in companies + LPs. Logs exchanges, classifies replies with AI,
    stamps last_reply_at, and auto-advances Contacted → Responded on reply.
    deep=true searches per known address/domain (no mailbox-size cap) over a
    10-year window, so the email activity log holds every exchange ever made.
    """
    if deep:
        days = max(days, 3650)
    from services.email_sync_service import sync_mailbox, classify_reply
    from services.reply_intel import bucket_reply
    from google.cloud import bigquery as bq_lib

    # Known contacts: email → entity. A company is reachable via its contact
    # email AND any address we actually drafted/sent outreach to — if we
    # emailed an address, a reply from it must match the company even when
    # SmartFill later changed the contact on file.
    _FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
                  "yahoo.co.uk", "icloud.com", "aol.com", "live.com", "protonmail.com",
                  "proton.me", "me.com", "msn.com"}

    def _dom(s: str) -> str:
        s = (s or "").strip().lower()
        if "@" in s:
            s = s.split("@")[-1]
        else:
            s = re.sub(r"^https?://(www\.)?", "", s).split("/")[0]
        return "" if not s or s in _FREE_MAIL else s

    known, known_domains, companies_by_name = {}, {}, {}
    for c in bq_handler.get_universe():
        companies_by_name[c.get("name")] = c  # full row: feeds action bucketing
        entry = {"type": "company", "name": c.get("name"), "status": c.get("status"),
                 "is_test": c.get("source") == "Internal Test",
                 "stored_cls": c.get("reply_classification") or ""}
        for em in {(c.get("contact_email") or "").strip().lower(),
                   (c.get("outreach_draft_to") or "").strip().lower()}:
            if em:
                known[em] = entry
        # Domain fallback: a reply from ANYONE at the company's domain matches
        # it — founders often reply personally when we wrote to hello@/info@
        for d in {_dom(c.get("contact_email")), _dom(c.get("outreach_draft_to")), _dom(c.get("website"))}:
            if d:
                known_domains.setdefault(d, entry)
    for inv in investor_handler.get_all():
        em = (inv.get("contact_email") or "").strip().lower()
        if em:
            # setdefault: if an address belongs to BOTH a company and an LP,
            # the company wins — stage moves matter more than an LP note
            known.setdefault(em, {"type": "investor", "name": inv.get("name"), "status": inv.get("status")})
            d = _dom(em)
            if d:
                known_domains.setdefault(d, known[em])
    if not known:
        return {"status": "Complete", "message": "No known contact emails in the database yet."}

    def _sender_of(em: str) -> dict:
        """Resolve a counterparty address to its entity: exact match, then domain."""
        em = (em or "").lower()
        return known.get(em) or known_domains.get(em.split("@")[-1] if "@" in em else "", {}) or {}

    # Deep mode: search only contacts we actually corresponded with (outreach
    # drafted/sent, or already in a dialogue stage) — searching the whole
    # 3,000-company universe would mean thousands of IMAP searches.
    deep_addresses = None
    if deep:
        deep_addresses = set()
        dialogue = {"Contacted", "Responded", "Meeting", "DD", "Offer", "Won", "Lost"}
        for c in companies_by_name.values():
            if c.get("outreach_draft_to") or c.get("outreach_sent_at") or c.get("status") in dialogue:
                for em in ((c.get("contact_email") or ""), (c.get("outreach_draft_to") or "")):
                    em = em.strip().lower()
                    if em:
                        deep_addresses.add(em)
                for d in {_dom(c.get("contact_email")), _dom(c.get("outreach_draft_to")), _dom(c.get("website"))}:
                    if d:
                        deep_addresses.add("@" + d)
        for inv in investor_handler.get_all():
            em = (inv.get("contact_email") or "").strip().lower()
            if em and (inv.get("status") or "Identified") not in ("Identified", "Researched"):
                deep_addresses.add(em)
        deep_addresses = sorted(deep_addresses)

    # THREAD MAP: every Message-ID we have ever logged, mapped to its company.
    # This is the third matching rung — it catches a reply from a DIFFERENT
    # domain (a founder's personal address, a parent company) that address and
    # domain matching can never see, because the reply's In-Reply-To/References
    # headers name our own message. Found in production: ReadyGo replied from a
    # foreign domain and was invisible for a month.
    thread_map = {}
    try:
        thread_map = bq_handler.get_message_id_entity_map()
    except Exception as e:
        logger.warning(f"[EmailSync] thread map unavailable (address matching only): {e}")

    try:
        entries = sync_mailbox(known, days=days, known_domains=known_domains, deep=deep,
                               deep_addresses=deep_addresses, thread_map=thread_map)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mailbox sync failed: {e}")

    # Dedup against already-logged messages
    seen = bq_handler.get_logged_message_ids()
    new_entries = [e for e in entries if e.get("message_id") not in seen]

    # ── Autoresponders are not replies ───────────────────────────────────────
    # An "I'm away until the 15th" used to advance the company to Responded,
    # consume two Gemini calls and land on the triage queue. Now it is detected
    # first (deterministically, one cheap AI call only if the date cannot be
    # read), stamped on the company to move the follow-up reminder out, and
    # then kept OUT of `replies` so none of the reply machinery touches it.
    from services.ooo_detect import detect as _ooo_detect, followup_days as _ooo_followup_days

    received_new = [e for e in new_entries if e["direction"] == "received"]
    ooo_hits, replies = [], []
    for e in received_new:
        try:
            got = _ooo_detect(e.get("subject", ""), e.get("snippet", ""),
                              headers=e.get("headers", ""),
                              received_on=_as_date(e.get("sent_at")))
        except Exception as ex:
            logger.warning(f"OOO detection failed for {e.get('entity_name')}: {ex}")
            got = {"is_ooo": False}
        if got.get("is_ooo"):
            e["classification"] = "out_of_office"
            e["summary"] = ("Automatic out-of-office reply"
                            + (f", back {got['until'].isoformat()}" if got.get("until") else ", no return date stated"))
            e["_ooo"] = got
            ooo_hits.append(e)
        else:
            replies.append(e)

    advanced, classified = [], 0
    ooo_applied = []
    for e in ooo_hits:
        if e.get("entity_type") != "company":
            continue
        try:
            ooo_applied.append(_apply_ooo(e))
        except Exception as ex:
            logger.warning(f"OOO stamp failed for {e.get('entity_name')}: {ex}")

    for r in replies[:25]:  # classification bounded per sync run
        result = classify_reply(r["subject"], r["snippet"], r["entity_name"])
        if result:
            r["classification"] = result.get("classification", "")
            r["summary"] = result.get("summary", "")
            classified += 1

    # ── Cross-domain contact adoption (the ReadyGo rule) ─────────────────────
    # A reply matched by THREAD came from an address on a domain we do not
    # know. If a HUMAN at the company wrote it, that address is now the best
    # contact we have and is adopted. The guard, per Ishu: never adopt a third
    # party. One cheap relation check per such reply (a handful ever), and
    # every uncertain answer means log-but-do-not-adopt — a wrong adoption
    # would misdirect all future outreach, a missed one costs nothing.
    contact_adopted = []
    for r in replies:
        if r.get("matched_by") != "thread" or r.get("entity_type") != "company":
            continue
        if r.get("classification") in bq_handler.NON_REPLY_CLASSES:
            continue  # an autoresponder or bounce proves nothing about a person
        from services.email_sync_service import assess_sender_relation
        relation = assess_sender_relation(
            r["entity_name"], r.get("counterparty_name", ""),
            r["counterparty_email"], r.get("subject", ""), r.get("snippet", ""))
        if relation != "company":
            bq_handler.add_activity_note(
                r["entity_name"],
                f"Reply matched by thread from {r['counterparty_email']} (unknown domain) — "
                f"sender judged {relation}, so the stored contact was NOT changed.",
                created_by="email-sync")
            continue
        try:
            bq_handler.client.query(
                f"UPDATE `{bq_handler.table_id}` SET contact_email = @ce WHERE name = @name",
                job_config=bq_lib.QueryJobConfig(query_parameters=[
                    bq_lib.ScalarQueryParameter("ce", "STRING", r["counterparty_email"]),
                    bq_lib.ScalarQueryParameter("name", "STRING", r["entity_name"]),
                ])).result()
            bq_handler.add_activity_note(
                r["entity_name"],
                f"Contact adopted from their reply: {r['counterparty_email']} "
                f"(cross-domain, thread-matched, sender judged to be the company). "
                f"Future emails go there.",
                created_by="email-sync")
            contact_adopted.append(f"{r['entity_name']} -> {r['counterparty_email']}")
        except Exception as ex:
            logger.warning(f"Cross-domain adoption failed for {r['entity_name']}: {ex}")

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
            sender = _sender_of(r["counterparty_email"])
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

    # ── Email documents: file and read every attachment on inbound mail ──────
    # Runs on NEW entries only (dedupe also guards inside, so overlap with the
    # retro backfill is safe). Field updates go through the whitelist in
    # email_docs_service and every change lands in the Activity Log.
    docs_filed = []
    try:
        from services.email_docs_service import AI_READS_PER_RUN, process_email_documents
        doc_ai_budget = [AI_READS_PER_RUN]
        for e in new_entries:
            if e.get("direction") == "received" and e.get("attachments"):
                names = process_email_documents(
                    bq_handler, gcs_handler, e, companies_by_name.get(e.get("entity_name")),
                    ai_budget=doc_ai_budget)
                docs_filed += [f"{e['entity_name']}: {n}" for n in names]
    except Exception as e:
        logger.warning(f"[EmailDocs] processing failed (non-fatal): {e}")

    # ── Action buckets (Responded-stage intelligence) ────────────────────────
    # One ungrounded call per reply: our company record + the email content →
    # an action bucket, a one-sentence rationale, an optional follow-up date,
    # and (for buckets that warrant one) a suggested response draft.
    # Advisory only: buckets NEVER move stages.
    bucketed, bucketed_names, bucket_errors = [], set(), []
    bucket_budget = 25

    def _thread_for(name: str) -> list:
        return sorted((e for e in entries if e.get("entity_name") == name),
                      key=lambda x: x.get("sent_at") or "")

    def _apply_bucket(ename: str, b: dict, event_time: str):
        bq_handler.client.query(
            f"""UPDATE `{bq_handler.table_id}` SET
                action_bucket = @b, action_rationale = @r, action_follow_up_date = @f,
                action_set_at = CURRENT_TIMESTAMP(),
                action_reply_subject = @rs, action_reply_body = @rb
                WHERE name = @name""",
            job_config=bq_lib.QueryJobConfig(query_parameters=[
                bq_lib.ScalarQueryParameter("b", "STRING", b["bucket"]),
                bq_lib.ScalarQueryParameter("r", "STRING", b["rationale"]),
                bq_lib.ScalarQueryParameter("f", "STRING", b["follow_up_date"]),
                bq_lib.ScalarQueryParameter("rs", "STRING", b["reply_subject"]),
                bq_lib.ScalarQueryParameter("rb", "STRING", b["reply_body"]),
                bq_lib.ScalarQueryParameter("name", "STRING", ename),
            ])).result()
        note = f"Action bucket: {b['label']}" + (f" | {b['rationale']}" if b.get("rationale") else "")
        if b.get("follow_up_date"):
            note += f" | Follow up: {b['follow_up_date']}"
        bq_handler._log_activity(ename, "note", "email-sync", note_text=note, event_time=event_time)
        bucketed_names.add(ename)
        bucketed.append(f"{ename}: {b['label']}")

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
                # Auto-advance: a reply means dialogue — Contacted → Responded.
                # The Internal Test row advances from ANY pre-Responded state so
                # the loop is testable regardless of where it started.
                sender = _sender_of(r["counterparty_email"])
                past_contact = {"Responded", "Meeting", "DD", "Offer", "Won"}
                if sender.get("status") == "Contacted" or (sender.get("is_test") and sender.get("status") not in past_contact):
                    bq_handler.update_company_status(ename, "Responded", created_by="email-sync")
                    advanced.append(ename)
                # Action bucket for this new reply (bounded per run)
                company_row = companies_by_name.get(ename)
                if company_row and bucket_budget > 0 and ename not in bucketed_names:
                    bucket_budget -= 1
                    try:
                        b = bucket_reply(company_row, r["subject"], r["snippet"], thread=_thread_for(ename))
                        if b:
                            _apply_bucket(ename, b, r["sent_at"])
                        else:
                            bucket_errors.append(f"{ename}: model returned no bucket")
                    except Exception as be:
                        bucket_errors.append(f"{ename}: {be}")
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
    past_contact = {"Responded", "Meeting", "DD", "Offer", "Won"}
    for r in sorted((e for e in entries if e["direction"] == "received"), key=lambda x: x.get("sent_at") or ""):
        ename = r["entity_name"]
        if r["entity_type"] != "company" or ename in handled:
            continue
        # An autoresponder is not a reply, so it must not trigger the advance
        # here either. Re-checked (not read off `_ooo`) because this pass also
        # walks messages logged in EARLIER runs, which carry no such marker.
        try:
            from services.ooo_detect import is_auto_reply as _is_auto
            if _is_auto(r.get("subject", ""), r.get("snippet", ""), r.get("headers", "")):
                continue
        except Exception:
            pass
        sender = _sender_of(r["counterparty_email"])
        if sender.get("status") == "Contacted" or (sender.get("is_test") and sender.get("status") not in past_contact):
            try:
                # Ensure the reply stamp exists (idempotent), then advance
                bq_handler.client.query(
                    f"""UPDATE `{bq_handler.table_id}` SET last_reply_at = IFNULL(last_reply_at, @ts) WHERE name = @name""",
                    job_config=bq_lib.QueryJobConfig(query_parameters=[
                        bq_lib.ScalarQueryParameter("ts", "TIMESTAMP", r["sent_at"]),
                        bq_lib.ScalarQueryParameter("name", "STRING", ename),
                    ])).result()
                bq_handler.update_company_status(ename, "Responded", created_by="email-sync")
                advanced.append(ename)
                handled.add(ename)
            except Exception as e:
                logger.warning(f"Self-heal advance failed for {ename}: {e}")

    # DELIVERY CHECK, before the reply rule. Order matters: a bounce message must
    # be classified as 'bounce' BEFORE the reply rule looks at the log, or the
    # mailer-daemon report counts as a genuine reply and promotes the company to
    # Responded — the exact inverse of what a bounce means.
    delivery = {}
    try:
        delivery = _verify_delivery(dry_run=False, window_days=max(days, 30))
    except Exception as e:
        logger.warning(f"Delivery verification failed (non-fatal): {e}")

    # THE REPLY RULE, applied in both directions. Runs AFTER the advances above
    # so a reply logged moments ago is already visible and no company is pulled
    # back and pushed forward in the same run.
    #
    # Anything with no reply on record that a HUMAN moved is not touched here: it
    # comes back under needs_confirmation for the UI to ask about, because they
    # may know the founder rang instead of writing. Everything else is corrected
    # automatically, so the board and the Responded page always agree.
    reply_rule = {"promote": [], "demote": [], "needs_confirmation": []}
    try:
        reply_rule = bq_handler.reconcile_reply_stages()
    except Exception as e:
        logger.warning(f"Reply-stage reconciliation failed (non-fatal): {e}")
    pulled_back = reply_rule["demote"]

    # Retro bucketing: companies with a logged reply but no action bucket yet
    # (replies synced before buckets existed, or a call failed). Reads from
    # email_log — the single source of truth — so it works regardless of the
    # mailbox scan window (old replies beyond 30 days still get bucketed).
    # Bounded so a big backlog spreads over a few sync runs.
    retro_budget = min(15, bucket_budget)
    if retro_budget > 0:
        try:
            rows = bq_handler.client.query(f"""
                SELECT e.entity_name, e.direction, e.subject, e.snippet, e.sent_at
                FROM `{bq_handler.project_id}.{bq_handler.dataset_id}.email_log` e
                JOIN `{bq_handler.table_id}` t ON t.name = e.entity_name
                WHERE t.action_bucket IS NULL
                  AND t.status IN ('Contacted', 'Responded', 'Meeting', 'DD', 'Offer')
                  AND e.entity_type = 'company'
                ORDER BY e.entity_name, e.sent_at
            """).result()
            threads = {}
            for row in rows:
                threads.setdefault(row.entity_name, []).append(
                    {"direction": row.direction, "subject": row.subject or "",
                     "snippet": row.snippet or "", "sent_at": str(row.sent_at or "")})
            for ename, msgs in threads.items():
                if retro_budget <= 0:
                    break
                if ename in bucketed_names:
                    continue
                comp = companies_by_name.get(ename)
                if not comp:
                    continue
                received = [m for m in msgs if m["direction"] == "received" and m["snippet"].strip()]
                if not received:
                    # Log row had no readable snippet — try this run's mailbox scan
                    received = [m for m in _thread_for(ename)
                                if m.get("direction") == "received" and (m.get("snippet") or "").strip()]
                    if not received:
                        continue
                latest = received[-1]
                retro_budget -= 1
                try:
                    b = bucket_reply(comp, latest["subject"], latest["snippet"], thread=msgs)
                    if b:
                        # We already answered (latest thread message is ours) —
                        # keep the bucket label but drop the suggested reply.
                        if msgs and msgs[-1]["direction"] == "sent":
                            b["reply_subject"], b["reply_body"] = "", ""
                        _apply_bucket(ename, b, latest["sent_at"])
                    else:
                        bucket_errors.append(f"{ename}: model returned no bucket")
                except Exception as e:
                    logger.warning(f"Retro bucketing failed for {ename}: {e}")
                    bucket_errors.append(f"{ename}: {e}")
        except Exception as e:
            logger.warning(f"Retro bucketing query failed: {e}")
            bucket_errors.append(f"retro query: {e}")

    # Suggested-reply hygiene: once WE are the latest message in a company's
    # thread (we answered), the stored suggestion is consumed — clear it.
    # The bucket label and rationale stay; only the pending reply disappears.
    try:
        bq_handler.client.query(f"""
            UPDATE `{bq_handler.table_id}` SET
                action_reply_subject = NULL, action_reply_body = NULL
            WHERE action_reply_body IS NOT NULL AND name IN (
                SELECT entity_name FROM (
                    SELECT entity_name, direction,
                           ROW_NUMBER() OVER (PARTITION BY entity_name ORDER BY sent_at DESC) AS rn
                    FROM `{bq_handler.project_id}.{bq_handler.dataset_id}.email_log`
                    WHERE entity_type = 'company')
                WHERE rn = 1 AND direction = 'sent')""").result()
    except Exception as e:
        logger.warning(f"Suggested-reply hygiene pass failed: {e}")

    return {
        "status": "Success",
        "scanned_days": days,
        "known_contacts": len(known),
        "messages_matched": len(entries),
        "new_logged": inserted,
        "replies_found": len(replies),
        "replies_classified": classified,
        "auto_advanced": advanced,
        "pulled_back": pulled_back,
        # Companies the rule wants to move back but will not without a human
        # answer, because a person put them in Responded. The UI prompts on these.
        "needs_confirmation": reply_rule["needs_confirmation"],
        "contact_adopted_from_replies": contact_adopted,
        "documents_filed": docs_filed,
        "delivery": {k: delivery.get(k) for k in
                     ("bounces_found", "sends_missing_from_mailbox", "pulled_back")},
        "reclassified": reclassified,
        "action_buckets": bucketed,
        "bucket_errors": bucket_errors[:10],
        "message": f"Logged {inserted} new messages ({len(replies)} replies, {classified} classified"
                   + (f", {len(reclassified)} reclassified" if reclassified else "")
                   + (f", {len(bucketed)} action-bucketed" if bucketed else "")
                   + (f", {len(bucket_errors)} bucket attempts FAILED: {bucket_errors[0]}" if bucket_errors else "") + "). "
                   + (f"Advanced to Responded: {', '.join(advanced)}. " if advanced else "")
                   + (f"Pulled back (no reply on record): "
                      + ", ".join(f"{p['name']} -> {p['to']}" for p in pulled_back) + "."
                      if pulled_back else "")
                   + ("No stage changes." if not advanced and not pulled_back else ""),
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
