import os
import boto3
from dotenv import load_dotenv

# 1. Explicitly load .env from the script's own folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

# 2. Read environment variables
bucket_name = os.environ.get("S3_BUCKET_NAME")
region = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")
access_key = os.environ.get("AWS_ACCESS_KEY_ID")
secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

print(f"Bucket: {bucket_name}")
print(f"Region: {region}")
print(f"Access Key Loaded: {'Yes' if access_key else 'No'}")

if not bucket_name or not access_key or not secret_key:
    raise ValueError("Missing S3 configuration in .env file.")

# 3. Initialize S3 Client
s3_client = boto3.client(
    's3',
    region_name=region,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key
)

def test_upload():
    test_key = "uploads/test_user/sample_test.txt"
    test_content = b"Hello from S3 test script!"
    
    print(f"\nUploading test object to s3://{bucket_name}/{test_key} ...")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=test_key,
        Body=test_content,
        ContentType="text/plain"
    )
    print("✅ Direct S3 upload successful!")

    # 4. Generate Presigned URL for frontend uploads
    presigned_url = s3_client.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': bucket_name,
            'Key': "uploads/test_user/presigned_sample.jpg",
            'ContentType': 'image/jpeg'
        },
        ExpiresIn=300
    )
    print("\n✅ Presigned URL generated successfully:")
    print(presigned_url)

if __name__ == "__main__":
    test_upload()