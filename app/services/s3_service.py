import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from typing import Optional
from datetime import datetime
import os
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class S3Service:
    """
    Service for handling S3 file uploads and operations.
    """

    def __init__(self):
        """Initialize S3 client with AWS credentials from settings."""
        # Use regional endpoint to ensure signature matches the URL host
        regional_endpoint = f"https://s3.{settings.aws_region}.amazonaws.com"
        
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            endpoint_url=regional_endpoint,
            config=BotoConfig(
                signature_version='s3v4',
                s3={'addressing_style': 'virtual'},
                connect_timeout=10,  # Increased from 5 to 10 seconds
                read_timeout=60,     # Increased from 10 to 60 seconds
                retries={'max_attempts': 3}  # Increased retries from 2 to 3
            )
        )
        self.bucket_name = settings.aws_s3_bucket_name
        self.region = settings.aws_region

    def upload_visitor_image(
        self,
        file_content: bytes,
        visitor_number: str,
        content_type: str = "image/jpeg"
    ) -> Optional[str]:
        """
        Upload visitor image to S3 bucket.

        Args:
            file_content: Binary content of the image file
            visitor_number: Visitor number in YYYYMMDDHHMMSS format
            content_type: MIME type of the image (default: image/jpeg)

        Returns:
            URL of the uploaded image if successful, None otherwise

        Raises:
            Exception: If upload fails
        """
        try:
            # Determine file extension from content type
            extension_map = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp"
            }
            extension = extension_map.get(content_type.lower(), ".jpg")

            # Create S3 object key using visitor number
            object_key = f"visitors/{visitor_number}{extension}"

            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=file_content,
                ContentType=content_type
                # Note: ACL removed - bucket uses bucket policy for public access
            )

            # Generate pre-signed URL (valid for 7 days)
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': object_key
                },
                ExpiresIn=604800  # 7 days in seconds
            )

            logger.info(f"Successfully uploaded visitor image: {object_key}")
            return url

        except ClientError as e:
            logger.error(f"Failed to upload visitor image to S3: {str(e)}")
            raise Exception(f"Failed to upload image: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error during S3 upload: {str(e)}")
            raise Exception(f"Failed to upload image: {str(e)}")

    def delete_visitor_image(self, img_url: str) -> bool:
        """
        Delete visitor image from S3 bucket.

        Args:
            img_url: Full URL of the image to delete

        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            # Extract object key from URL
            # Example URL: https://bucket-name.s3.region.amazonaws.com/visitors/20251126123045.jpg
            object_key = img_url.split(f"{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/")[-1]

            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=object_key
            )

            logger.info(f"Successfully deleted visitor image: {object_key}")
            return True

        except ClientError as e:
            logger.error(f"Failed to delete visitor image from S3: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during S3 deletion: {str(e)}")
            return False

    def check_image_exists(self, img_url: str) -> bool:
        """
        Check if an image exists in S3 bucket.

        Args:
            img_url: Full URL of the image to check

        Returns:
            True if image exists, False otherwise
        """
        try:
            object_key = img_url.split(f"{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/")[-1]

            self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=object_key
            )
            return True

        except ClientError:
            return False
        except Exception:
            return False


# Create a singleton instance
s3_service = S3Service()
