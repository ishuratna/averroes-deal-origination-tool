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
    Parses an Excel file and maps it to the Master Universe schema.
    """
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        processed_targets = []
        
        # Normalize headers (remove whitespace/lowercase)
        df.columns = [c.strip() for c in df.columns]
        
        for _, row in df.iterrows():
            target = {field: None for field in INTERNAL_SCHEMA}
            
            # 1. Direct Mapping
            for excel_col, internal_field in HEADER_MAP.items():
                if excel_col in df.columns:
                    val = row[excel_col]
                    if pd.notna(val):
                        # Handle numeric values for estimated_ebitda
                        if internal_field == "estimated_ebitda":
                            try:
                                # Clean £, m, etc.
                                clean_val = str(val).replace('£', '').replace('m', '').replace('M', '').strip()
                                target[internal_field] = float(clean_val)
                            except:
                                target[internal_field] = 0.0
                        else:
                            target[internal_field] = str(val)

            # 2. Smart Merging (Description + Angle + Notes)
            desc_parts = []
            if target.get("description"): desc_parts.append(target["description"])
            
            if "Investment_angle" in row and pd.notna(row["Investment_angle"]):
                desc_parts.append(f"Angle: {row['Investment_angle']}")
            if "Notes" in row and pd.notna(row["Notes"]):
                desc_parts.append(f"Notes: {row['Notes']}")
            if "Source_category" in row and pd.notna(row["Source_category"]):
                desc_parts.append(f"Source Context: {row['Source_category']}")
                
            target["description"] = " | ".join(desc_parts)

            # 3. Defaults & Metadata
            target["company_id"] = str(uuid.uuid4())
            target["source"] = "Custom File"
            target["status"] = "Qualified"
            target["ingested_at"] = datetime.utcnow().isoformat()
            
            # Simple match score logic for custom files
            # If "SaaS_fit" is Yes, boost the score
            base_score = 0.5
            if "SaaS_fit" in row and str(row["SaaS_fit"]).lower() == "yes":
                base_score = 0.8
            target["match_score"] = base_score

            processed_targets.append(target)
            
        return processed_targets

    except Exception as e:
        logger.error(f"Excel parsing failed: {e}")
        raise e
