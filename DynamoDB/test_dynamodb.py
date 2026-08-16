import os
import boto3
from decimal import Decimal
from dotenv import load_dotenv

# 1. Load credentials from .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

# 2. Connect to DynamoDB Resource
dynamodb = boto3.resource(
    'dynamodb',
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY")
)

table = dynamodb.Table('UserSkinProfiles')

def test_dynamo_lifecycle():
    mock_user_id = "user_hackathon_001"
    
    # Payload using Decimal for floating-point numbers
    mock_profile = {
        "user_id": mock_user_id,
        "skin_biome": "oily",
        "budget_usd": Decimal("40.00"),  # Use Decimal instead of float
        "climate_type": "tropical humid",
        "acute_condition": "active_breakout",
        "known_allergies": ["limonene", "fragrance"],
        "current_routine": {
            "AM": ["P439055"],
            "PM": ["P421277"]
        }
    }
    
    # 1. Write Item
    print(f"Writing mock user profile to DynamoDB table 'UserSkinProfiles'...")
    table.put_item(Item=mock_profile)
    print("✅ Write successful!")
    
    # 2. Read Item
    print(f"\nRetrieving profile for {mock_user_id}...")
    response = table.get_item(Key={"user_id": mock_user_id})
    item = response.get("Item")
    
    if item:
        print("✅ Retrieved Item from AWS DynamoDB:")
        for key, value in item.items():
            print(f"   • {key}: {value}")
    else:
        print("❌ Item not found.")

if __name__ == "__main__":
    test_dynamo_lifecycle()