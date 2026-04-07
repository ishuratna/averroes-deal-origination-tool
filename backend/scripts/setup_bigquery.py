from google.cloud import bigquery
import logging

logger = logging.getLogger(__name__)

def setup_averroes_target_db(project_id: str, dataset_id: str = "averroes_deal_flow"):
    """
    Sets up the BigQuery dataset and tables for the deal target database.
    """
    client = bigquery.Client(project=project_id)
    dataset_ref = f"{project_id}.{dataset_id}"

    # 1. Create Dataset
    try:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "EU" # Focus on UK/Europe data location
        client.create_dataset(dataset, exists_ok=True)
        logger.info(f"Dataset {dataset_id} confirmed/created.")
    except Exception as e:
        logger.error(f"Failed to create dataset: {e}")
        return

    # 2. Create Targets Table
    table_id = f"{dataset_ref}.targets"
    schema = [
        bigquery.SchemaField("company_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("website", "STRING"),
        bigquery.SchemaField("sector", "STRING"),
        bigquery.SchemaField("ebitda_est", "FLOAT"),
        bigquery.SchemaField("revenue_est", "FLOAT"),
        bigquery.SchemaField("match_score", "FLOAT"),
        bigquery.SchemaField("source", "STRING"),
        bigquery.SchemaField("region", "STRING"),
        bigquery.SchemaField("contact_details", "JSON"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP", default_value_expression="CURRENT_TIMESTAMP"),
        bigquery.SchemaField("status", "STRING")
    ]

    try:
        table = bigquery.Table(table_id, schema=schema)
        client.create_table(table, exists_ok=True)
        logger.info(f"Table {table_id} confirmed/created.")
    except Exception as e:
        logger.error(f"Failed to create table: {e}")

if __name__ == "__main__":
    # project = os.getenv("GOOGLE_CLOUD_PROJECT")
    # setup_averroes_target_db(project)
    pass
