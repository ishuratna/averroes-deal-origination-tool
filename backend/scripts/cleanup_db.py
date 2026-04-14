import os
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv()

def cleanup_bq():
    project = os.getenv('GOOGLE_CLOUD_PROJECT')
    dataset = os.getenv('BIGQUERY_DATASET', 'averroes_deal_flow')
    client = bigquery.Client(project=project)
    
    table_id = f"{project}.{dataset}.targets"
    
    # Wipe placeholders
    query = f"""
    UPDATE `{table_id}`
    SET 
        contact_name = NULL,
        contact_email = NULL,
        linkedin_url = NULL
    WHERE 
        contact_name IN ('System Override Required', 'Data Missing', 'Pending Activation', 'Unknown Founder')
        OR contact_email = 'research@averroescapital.com'
        OR contact_name IS NULL
    """
    
    print(f"🧹 Cleaning up {table_id}...")
    try:
        query_job = client.query(query)
        query_job.result()
        print("✅ BigQuery placeholders wiped.")
    except Exception as e:
        print(f"❌ Cleanup failed: {e}")

if __name__ == "__main__":
    cleanup_bq()
