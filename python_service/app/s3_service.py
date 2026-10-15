import os
import boto3
from botocore.client import Config
from io import BytesIO

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION", "eu-north-1"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4")  # Enforces AWS4-HMAC-SHA256
        )
        self.bucket_name = os.getenv("S3_BUCKET_NAME")

    def download_image_to_memory(self, object_key: str) -> bytes:
        buffer = BytesIO()
        self.s3_client.download_fileobj(self.bucket_name, object_key, buffer)
        return buffer.getvalue()

    def upload_processed_image(self, file_bytes: bytes, output_key: str) -> str:
        buffer = BytesIO(file_bytes)
        self.s3_client.upload_fileobj(
            buffer, 
            self.bucket_name, 
            output_key,
            ExtraArgs={"ContentType": "image/png"}
        )
        
        # Generate a secure download link valid for 1 hour using SigV4
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": output_key},
            ExpiresIn=3600
        )