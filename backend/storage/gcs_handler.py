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
        try:
            self.storage_client = storage.Client()
            self._ensure_bucket()
        except Exception as e:
            logger.warning(f"GCS Storage Client could not be initialized (Missing Credentials?): {e}")
            self.storage_client = None

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
            if self.storage_client is None:
                return None
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

    def save_raw_file(self, content: bytes, filename: str, content_type: str):
        """
        Upload binary file content to GCS.
        """
        try:
            if self.storage_client is None:
                return None
            bucket = self.storage_client.bucket(self.bucket_name)
            blob = bucket.blob(f"uploads/{filename}")
            blob.upload_from_string(data=content, content_type=content_type)
            logger.info(f"Raw file saved to GCS: uploads/{filename}")
            return blob.public_url
        except Exception as e:
            logger.error(f"Failed to upload raw file to GCS: {str(e)}")
            return None

    def list_files(self, prefix: str = "scraped/"):

if __name__ == "__main__":
    # Test
    # handler = GCSHandler()
    pass
