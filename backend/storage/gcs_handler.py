import os
import json
from google.cloud import storage
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class GCSHandler:
    """
    Handles file interactions with Google Cloud Storage.
    """
    def __init__(self, bucket_name: str = "averroes-deal-intelligence"):
        self.bucket_name = bucket_name
        self.storage_client = storage.Client()
        self._ensure_bucket()

    def _ensure_bucket(self):
        """
        Check if bucket exists or create it.
        """
        try:
            self.storage_client.get_bucket(self.bucket_name)
        except Exception:
            try:
                self.storage_client.create_bucket(self.bucket_name)
                logger.info(f"Bucket {self.bucket_name} created.")
            except Exception as e:
                logger.warning(f"Could not create bucket: {e}. Check credentials.")

    def save_companies(self, companies: list, source: str):
        """
        Save a list of company objects to GCS as JSON.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"scraped/{source}/{timestamp}_targets.json"
        
        try:
            bucket = self.storage_client.bucket(self.bucket_name)
            blob = bucket.blob(filename)
            blob.upload_from_string(
                data=json.dumps(companies, indent=2),
                content_type='application/json'
            )
            logger.info(f"Successfully saved {len(companies)} companies to {filename}")
            return filename
        except Exception as e:
            logger.error(f"Failed to upload to GCS: {str(e)}")
            return None

    def list_files(self, prefix: str = "scraped/"):
        """
        Lists files in the GCS bucket.
        """
        try:
            bucket = self.storage_client.bucket(self.bucket_name)
            blobs = bucket.list_blobs(prefix=prefix)
            return [blob.name for blob in blobs]
        except Exception:
            return []

if __name__ == "__main__":
    # Test
    # handler = GCSHandler()
    pass
