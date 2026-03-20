import boto3
import hashlib
import logging
from typing import Optional, Tuple
from botocore.exceptions import ClientError
from app.core.settings import settings

logger = logging.getLogger(__name__)

class S3StorageService:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID.get_secret_value(),
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY.get_secret_value(),
            region_name=settings.AWS_REGION
        )
        self.bucket_name = settings.S3_BUCKET
        logger.info(f"S3 Storage initialized with bucket: {self.bucket_name}")

    def _generate_s3_key(self, ticker: str, filing_type: str, filing_date: str, filename: str, accession_number: str = "") -> str:
        """
        Generate S3 key path.
        
        For raw files: sec/raw/{ticker}/{filing_type}/{filing_date}_{accession}.html
        For parsed files: sec/parsed/{ticker}/{filing_type}/{filing_date}_{filename}
        """
        # Check if this is for parsed content
        if filing_type.startswith("parsed/"):
            # sec/parsed/{ticker}/{filing_type}/{filing_date}_{filename}
            actual_filing_type = filing_type.replace("parsed/", "")
            return f"sec/parsed/{ticker}/{actual_filing_type}/{filing_date}_{filename}"
        
        # Raw files: sec/raw/{ticker}/{filing_type}/{filing_date}_{accession}.html
        clean_filing_type = filing_type.replace(" ", "")
        
        if accession_number:
            clean_accession = accession_number.replace("-", "")
            doc_filename = f"{filing_date}_{clean_accession}.html"
        else:
            doc_filename = f"{filing_date}_{filename}"
        
        return f"sec/raw/{ticker}/{clean_filing_type}/{doc_filename}"

    def _calculate_hash(self, content: bytes) -> str:
        """Calculate SHA256 hash of content"""
        return hashlib.sha256(content).hexdigest()

    def upload_filing(
        self,
        ticker: str,
        filing_type: str,
        filing_date: str,
        filename: str,
        content: bytes,
        content_type: str = "text/html",
        accession_number: str = ""
    ) -> Tuple[str, str]:
        """
        Upload a filing to S3.
        
        S3 Path: sec/raw/{ticker}/{filing_type}/{filing_date}_{accession}.html
        
        Returns: (s3_key, content_hash)
        """
        s3_key = self._generate_s3_key(ticker, filing_type, filing_date, filename, accession_number)
        content_hash = self._calculate_hash(content)
        
        logger.info(f"  📤 Uploading to S3: {s3_key}")
        
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content,
                ContentType=content_type,
                Metadata={
                    'ticker': ticker,
                    'filing_type': filing_type,
                    'filing_date': filing_date,
                    'content_hash': content_hash
                }
            )
            logger.info(f"  ✅ Upload successful: {s3_key}")
            return s3_key, content_hash
        except ClientError as e:
            logger.error(f"  ❌ S3 upload failed: {e}")
            raise

    def check_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3"""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError:
            return False

    def get_file(self, s3_key: str) -> Optional[bytes]:
        """Download a file from S3"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"Failed to get file from S3: {e}")
            return None

    def delete_file(self, s3_key: str) -> bool:
        """Delete a file from S3"""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file from S3: {e}")
            return False

    def delete_prefix(self, prefix: str) -> int:
        """Delete all S3 objects under a given prefix. Returns count deleted."""
        deleted_count = 0
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                objects = page.get("Contents", [])
                if not objects:
                    continue
                all_keys = [{"Key": obj["Key"]} for obj in objects]
                # S3 delete_objects is limited to 1000 keys per request
                for i in range(0, len(all_keys), 1000):
                    batch = all_keys[i:i + 1000]
                    self.s3_client.delete_objects(
                        Bucket=self.bucket_name,
                        Delete={"Objects": batch},
                    )
                    deleted_count += len(batch)
                logger.info(f"Deleted {len(all_keys)} S3 objects under prefix '{prefix}'")
        except Exception as e:
            logger.error(f"Error deleting S3 prefix '{prefix}': {e}")
        return deleted_count

    def delete_keys(self, keys: list) -> int:
        """Delete a specific list of S3 keys in one batched call. Returns count deleted."""
        if not keys:
            return 0
        try:
            objects = [{"Key": k} for k in keys]
            resp = self.s3_client.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": objects, "Quiet": True},
            )
            return len(keys) - len(resp.get("Errors", []))
        except Exception as e:
            logger.error(f"Error deleting {len(keys)} S3 keys: {e}")
            return 0

    def list_files(self, prefix: str) -> list:
        """List files in S3 with given prefix"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            return [obj['Key'] for obj in response.get('Contents', [])]
        except ClientError as e:
            logger.error(f"Failed to list S3 files: {e}")
            return []

    def upload(self, data: str, s3_key: str, content_type: str = "application/json") -> str:
        """Upload string data to S3. Returns s3_key on success."""
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=data.encode('utf-8'),
                ContentType=content_type,
            )
            logger.info(f"  ✅ Uploaded to S3: {s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"  ❌ S3 upload failed: {e}")
            raise

    def upload_content(self, content: str, s3_key: str, content_type: str = "application/json") -> str:
        return self.upload(content, s3_key, content_type)

    def upload_json(self, data: dict, s3_key: str) -> str:
        import json
        return self.upload(json.dumps(data, indent=2, default=str), s3_key)

    def store_signal_data(
        self,
        signal_type: str,
        ticker: str,
        data: dict,
        timestamp: str = None
    ) -> str:
        """
        Store signal data to S3 with standardized path structure.

        Common method for all signal types to ensure consistent storage patterns.

        Args:
            signal_type: Type of signal ('jobs', 'patents', 'techstack')
            ticker: Company ticker symbol
            data: Dictionary containing signal data
            timestamp: Optional timestamp string (defaults to current UTC time)

        Returns:
            s3_key on success

        S3 Path Structure:
            signals/{signal_type}/{ticker}/{timestamp}.json

        Examples:
            signals/jobs/AAPL/20240115_143052.json
            signals/patents/MSFT/20240115_143052.json
            signals/techstack/GOOGL/20240115_143052.json
        """
        from datetime import datetime, timezone

        if timestamp is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        ticker = ticker.upper()
        s3_key = f"signals/{signal_type}/{ticker}/{timestamp}.json"

        logger.info(f"  💾 Storing {signal_type} data to S3: {s3_key}")

        return self.upload_json(data, s3_key)


from app.services.utils import make_singleton_factory

get_s3_service = make_singleton_factory(S3StorageService)