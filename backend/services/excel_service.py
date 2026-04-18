import pandas as pd
import io
import logging
import uuid
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

# Standard schema for our BigQuery "Master Universe"
INTERNAL_SCHEMA = [
    "name", "website", "sector", "region", "description", 
    "match_score", "status", "source", "contact_name", 
    "contact_email", "linkedin_url", "estimated_ebitda", "ingested_at"
]

# Smart mapping based on your specific Excel structure
HEADER_MAP = {
    "Company": "name",
    "HQ_country": "region",
    "Subsector": "sector",
    "Description": "description",
    "Investment_angle": "investment_angle",  # Will be merged into description
    "Notes": "notes",                        # Will be merged into description
    "VC_source_url": "website",
    "Revenue_est_low_gbp_m": "estimated_ebitda",
    "SaaS_fit": "saas_fit"
}

def parse_proprietary_excel(file_content: bytes) -> List[Dict]:
    """
    Parses an Excel file with fuzzy matching and safety fallbacks.
    """
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        processed_targets = []
        
        # 1. Normalize Headers
        raw_cols = [str(c).strip() for c in df.columns]
        df.columns = raw_cols
        
        # Fuzzy mapping logic
        def find_col(aliases: List[str]):
            for alias in aliases:
                for col in raw_cols:
                    if alias.lower() in col.lower():
                        return col
            return None

        # Resolve critical columns with fuzzy matching
        name_col = find_col(["Company", "Name", "Entity", "Co"]) or raw_cols[0]
        region_col = find_col(["HQ_country", "Country", "Region", "Location"])
        sector_col = find_col(["Subsector", "Sector", "Industry", "Vertical"])
        desc_col = find_col(["Description", "Summary", "About"])
        revenue_col = find_col(["Revenue_est", "Revenue", "Turnover", "Size"])

        for _, row in df.iterrows():
            target = {field: "" for field in INTERNAL_SCHEMA}
            
            # Map resolved columns
            target["name"] = str(row[name_col]) if pd.notna(row[name_col]) else "Unknown Entity"
            if region_col: target["region"] = str(row[region_col]) if pd.notna(row[region_col]) else ""
            if sector_col: target["sector"] = str(row[sector_col]) if pd.notna(row[sector_col]) else ""
            
            # Smart Revenue Extraction
            if revenue_col and pd.notna(row[revenue_col]):
                try:
                    val = str(row[revenue_col]).replace('£', '').replace('m', '').replace('M', '').replace(',', '').strip()
                    target["estimated_ebitda"] = float(val)
                except:
                    target["estimated_ebitda"] = 0.0

            # Dynamic Context Dump: Capture everything else so no data is lost
            context_bits = []
            if desc_col and pd.notna(row[desc_col]):
                context_bits.append(str(row[desc_col]))
            
            # Add ALL other columns into the description for searchability
            for col in raw_cols:
                if col not in [name_col, region_col, sector_col, desc_col, revenue_col]:
                    if pd.notna(row[col]):
                        context_bits.append(f"{col}: {row[col]}")
            
            target["description"] = " | ".join(context_bits)

            # Metadata
            target["company_id"] = str(uuid.uuid4())
            target["source"] = "Custom File"
            target["status"] = "Qualified"
            target["ingested_at"] = datetime.utcnow().isoformat()
            target["match_score"] = 0.8  # Default high for proprietary lists

            processed_targets.append(target)
            
        return processed_targets

    except Exception as e:
        logger.error(f"Excel parsing failed: {e}")
        raise e
