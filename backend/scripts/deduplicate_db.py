import os
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv()

def deduplicate_bq():
    project = os.getenv('GOOGLE_CLOUD_PROJECT', 'averroes-deal-origination')
    dataset = os.getenv('BIGQUERY_DATASET', 'averroes_deal_flow')
    client = bigquery.Client(project=project)
    
    table_id = f"{project}.{dataset}.targets"
    
    # This query keeps only the most recent entry for each unique company name
    # Using COALESCE to handle rows with old NULL timestamps
    query = f"""
    CREATE OR REPLACE TABLE `{table_id}` AS
    SELECT * EXCEPT(row_num)
    FROM (
      SELECT *,
             ROW_NUMBER() OVER(PARTITION BY name ORDER BY COALESCE(ingested_at, TIMESTAMP '2026-01-01 00:00:00') DESC, company_id) as row_num
      FROM `{table_id}`
    )
    WHERE row_num = 1
    """
    
    print(f"🔄 Deduplicating table: {table_id}...")
    try:
        query_job = client.query(query)
        query_job.result()
        print("✅ Success: Table deduplicated. Every company name is now unique.")
    except Exception as e:
        print(f"❌ Deduplication failed: {e}")

if __name__ == "__main__":
    deduplicate_bq()
