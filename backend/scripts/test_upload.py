import pandas as pd
import io
import os
import sys

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.excel_service import parse_proprietary_excel

def simulate_upload_test():
    print("🚀 Starting Local Parser Simulation...")
    
    # Create a dummy dataframe matching your schema to test robustness
    data = {
        "Company": ["AQMetrics", "Test AI"],
        "HQ_country": ["Ireland", "UK"],
        "Subsector": ["FinTech", "SaaS"],
        "Revenue_est_low_gbp_m": ["£5.0", "12.5"],
        "Description": ["RegTech logic", "AI logic"]
    }
    df = pd.DataFrame(data)
    
    # Convert to Excel bytes
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    excel_content = output.getvalue()
    
    print("✅ Dummy Excel created in memory.")
    
    try:
        targets = parse_proprietary_excel(excel_content)
        print(f"✅ Success! Parsed {len(targets)} targets.")
        
        for t in targets:
            print(f"\n--- Target: {t['name']} ---")
            print(f"Region: {t['region']}")
            print(f"Sector: {t['sector']}")
            print(f"Revenue: {t['estimated_ebitda']}M")
            print(f"Description sample: {t['description'][:50]}...")
            
    except Exception as e:
        print(f"❌ Parser Simulation Failed: {e}")

if __name__ == "__main__":
    simulate_upload_test()
