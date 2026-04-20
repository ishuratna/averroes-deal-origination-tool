import pandas as pd
import io
import logging
import uuid
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

INTERNAL_SCHEMA = [
    "name", "website", "sector", "region", "description", 
    "match_score", "status", "source", "contact_name", 
    "contact_email", "linkedin_url", "estimated_ebitda", "ingested_at"
]

HEADER_MAP = {
    "Company": "name",
    "HQ_country": "region",
    "Subsector": "sector",
    "Description": "description",
    "Investment_angle": "investment_angle",
    "Notes": "notes",
    "VC_source_url": "website",
    "Revenue_est_low_gbp_m": "estimated_ebitda",
    "SaaS_fit": "saas_fit"
}

def safe_float(val, default=0.0):
    """Convert to float safely, handling None, empty strings, NaN."""
    if val is None or val == "":
        return default
    try:
        result = float(val)
        if pd.isna(result):
            return default
        return result
    except (ValueError, TypeError):
        return default

def parse_proprietary_excel(file_content: bytes) -> List[Dict]:
    """Parses an Excel file with fuzzy matching and safety fallbacks."""
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        processed_targets = []
        
        raw_cols = [str(c).strip() for c in df.columns]
        df.columns = raw_cols
        
        def find_col(aliases: List[str]):
            for alias in aliases:
                for col in raw_cols:
                    if alias.lower() in col.lower():
                        return col
            return None

        name_col = find_col(["Company", "Name", "Entity", "Co"]) or raw_cols[0]
        region_col = find_col(["HQ_country", "Country", "Region", "Location"])
        sector_col = find_col(["Subsector", "Sector", "Industry", "Vertical"])
        desc_col = find_col(["Description", "Summary", "About"])
        revenue_col = find_col(["Revenue_est", "Revenue", "Turnover", "Size"])
        website_col = find_col(["VC_source_url", "Website", "URL", "Link"])

        for _, row in df.iterrows():
            target = {field: "" for field in INTERNAL_SCHEMA}
            target["match_score"] = 0.8
            target["estimated_ebitda"] = 0.0

            target["name"] = str(row[name_col]).strip() if pd.notna(row[name_col]) else "Unknown Entity"
            if region_col:
                target["region"] = str(row[region_col]).strip() if pd.notna(row[region_col]) else ""
            if sector_col:
                target["sector"] = str(row[sector_col]).strip() if pd.notna(row[sector_col]) else ""
            if website_col and pd.notna(row[website_col]):
                target["website"] = str(row[website_col]).strip()

            if revenue_col and pd.notna(row[revenue_col]):
                target["estimated_ebitda"] = safe_float(
                    str(row[revenue_col]).replace('\u00a3','').replace('£','').replace('m','').replace('M','').replace(',','').strip()
                )

            context_bits = []
            if desc_col and pd.notna(row[desc_col]):
                context_bits.append(str(row[desc_col]))
            
            for col in raw_cols:
                if col not in [name_col, region_col, sector_col, desc_col, revenue_col, website_col]:
                    if pd.notna(row[col]):
                        context_bits.append(f"{col}: {row[col]}")
            
            target["description"] = " | ".join(context_bits)

            target["company_id"] = str(uuid.uuid4())
            target["source"] = "Custom File"  # Overridden by endpoint
            target["status"] = "Pending"       # Overridden by AI scoring
            target["ingested_at"] = datetime.utcnow().isoformat()

            processed_targets.append(target)
            
        return processed_targets

    except Exception as e:
        logger.error(f"Excel parsing failed: {e}")
        raise e
