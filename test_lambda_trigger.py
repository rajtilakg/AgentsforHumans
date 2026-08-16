import os
import time
import boto3
from dotenv import load_dotenv

# 1. Load your master .env file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

# Initialize S3 and DynamoDB clients
s3_client = boto3.client(
    's3',
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
)

dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
)

bucket_name = os.environ.get("S3_BUCKET_NAME")
table = dynamodb.Table('UserSkinProfiles')

def run_end_to_end_test():
    # We will use the same mock user you created earlier
    test_user_id = "user_hackathon_001"
    
    # We are generating a unique filename using a timestamp so you can run this multiple times
    timestamp = int(time.time())
    test_image_key = f"uploads/{test_user_id}/live_trigger_test_{timestamp}.jpg"
    
    print("\n==================================================")
    print("🧪 RUNNING SERVERLESS PIPELINE TEST")
    print("==================================================")

    # STEP 1: Upload a dummy file to S3
    print(f"\n🚀 Step 1: Uploading mock image to S3...")
    print(f"   -> Bucket: {bucket_name}")
    print(f"   -> Key: {test_image_key}")
    
    s3_client.put_object(
        Bucket=bucket_name,
        Key=test_image_key,
        Body=b"fake image content for lambda trigger",
        ContentType="image/jpeg"
    )
    print("✅ S3 Upload complete!")
    
    # STEP 2: Let AWS run the automation
    print("\n⏳ Step 2: Waiting 4 seconds for Lambda to trigger and update DynamoDB...")
    time.sleep(4)
    
    # STEP 3: Verify the database
    print(f"\n🔍 Step 3: Querying DynamoDB for user: {test_user_id}...")
    response = table.get_item(Key={'user_id': test_user_id})
    item = response.get('Item', {})
    
    last_scanned = item.get('last_scanned_image')
    
    print("\n==================================================")
    print("📊 FINAL RESULTS")
    print("==================================================")
    
    if last_scanned == test_image_key:
        print(f"🎉 SUCCESS! The serverless pipeline worked perfectly.")
        print(f"   DynamoDB automatically registered the new image: {last_scanned}")
    else:
        print("❌ FAILED. DynamoDB did not update.")
        print(f"   Expected: {test_image_key}")
        print(f"   Found: {last_scanned}")
        print("\nTroubleshooting: Check the Lambda CloudWatch logs to see if it crashed.")

if __name__ == "__main__":
    if not bucket_name:
        print("❌ Error: S3_BUCKET_NAME is missing from your .env file.")
    else:
        run_end_to_end_test()