import os
import json
import boto3
import psycopg2
import asyncio
from dotenv import load_dotenv
from strands import Agent, tool
from strands.models.gemini import GeminiModel
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------
# 1. Environment & AWS Configuration
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=True)

region = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=region)
user_table = dynamodb.Table("UserSkinProfiles")

# ---------------------------------------------------------
# 2. Database Connection Helper
# ---------------------------------------------------------
def get_pg_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST"),
        database=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASS"),
        port=os.environ.get("DB_PORT", "5432"),
        connect_timeout=10,
    )

# ---------------------------------------------------------
# 3. Strands Tools
# ---------------------------------------------------------
@tool
def fetch_user_profile_and_ingredients(user_id: str) -> str:
    """Fetches user questionnaire answers and scanned ingredient array from DynamoDB."""
    print(f"\n⚡ [TOOL 1: DynamoDB] Fetching profile for: {user_id}")
    try:
        response = user_table.get_item(Key={"user_id": user_id})
        item = response.get("Item", {})

        profile_data = {
            "user_id": user_id,
            "latest_scanned_ingredients": item.get("latest_scanned_ingredients", []),
            "patientAnswers": item.get("patientAnswers", {})
        }
        print(f"   -> Found {len(profile_data['latest_scanned_ingredients'])} scanned ingredients.")
        return json.dumps(profile_data)

    except Exception as e:
        print(f"❌ Error fetching from DynamoDB: {e}")
        return json.dumps({"error": str(e), "latest_scanned_ingredients": [], "patientAnswers": {}})

@tool
def batch_lookup_ingredients(ingredients_list_json: str) -> str:
    """Performs batch fuzzy search on PostgreSQL RDS using pg_trgm."""
    try:
        if isinstance(ingredients_list_json, list):
            ingredients = ingredients_list_json
        else:
            ingredients = json.loads(ingredients_list_json)
    except Exception:
        ingredients = [ingredients_list_json]

    print(f"\n⚡ [TOOL 2: RDS] Running lookup for {len(ingredients)} items...")
    results = []
    conn = None
    cursor = None

    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

        for ing in ingredients:
            clean_name = ing.strip()
            query = """
                SELECT inci_name,
                       GREATEST(similarity(lower(inci_name), lower(%s)),
                                word_similarity(lower(%s), lower(inci_name))) AS score
                FROM ingredients
                ORDER BY score DESC LIMIT 1;
            """
            cursor.execute(query, (clean_name, clean_name))
            row = cursor.fetchone()

            if row:
                results.append({"queried": clean_name, "matched": row[0], "score": round(float(row[1]), 3)})
            else:
                results.append({"queried": clean_name, "matched": "No match", "score": 0.0})

    except Exception as e:
        print(f"❌ DB error: {e}")
        if conn: conn.rollback()
        return json.dumps({"error": str(e), "partial_results": results})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    return json.dumps(results)

# ---------------------------------------------------------
# 4. Agent Setup
# ---------------------------------------------------------
gemini_model = GeminiModel(
    model_id="gemini-3.5-flash-lite",
    client_args={"api_key": os.environ.get("GEMINI_API_KEY")},
)

skincare_agent = Agent(
    model=gemini_model,
    system_prompt=(
        "You are an expert cosmetic dermatologist AI assistant.\n"
        "1. When provided a user ID, call `fetch_user_profile_and_ingredients` to retrieve their `patientAnswers` and `latest_scanned_ingredients`.\n"
        "2. Take the ingredient array and call `batch_lookup_ingredients` to get fuzzy-matched database records.\n"
        "3. Synthesize the findings into a clear, professional assessment addressing their goals."
    ),
    tools=[fetch_user_profile_and_ingredients, batch_lookup_ingredients],
)

# ---------------------------------------------------------
# 5. FastAPI Server Integration
# ---------------------------------------------------------
app = FastAPI()

class ChatPayload(BaseModel):
    message: str
    history: list = []
    user_id: str = "user_hackathon_final_test" 

@app.post("/invoke")
async def invoke_agent(payload: ChatPayload):
    print(f"\n🚀 Receiving UI request for {payload.user_id}")
    agent_prompt = f"User ID: {payload.user_id}. User says: {payload.message}"
    
    try:
        report = await asyncio.to_thread(skincare_agent, agent_prompt)
        print("✅ Analysis complete. Sending to UI.")
        return {"reply": str(report)}
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"reply": f"Agent encountered an error: {str(e)}"}
