import os
import json
import boto3
import psycopg2
import asyncio
from io import BytesIO
from PIL import Image
from google import genai
from dotenv import load_dotenv
from strands import Agent, tool
from strands.models.gemini import GeminiModel
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------
# 1. Environment & AWS Configuration
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Note: adjusted dotenv path to look one level up since this sits in /backend theoretically, or adjust as needed.
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=True) 

region = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=region)
user_table = dynamodb.Table("UserSkinProfiles")

s3_client = boto3.client("s3", region_name=region)
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "agents-for-humans-images")

# Initialize the new google-genai Client for the Vision Extraction Agent
genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
# 3. Strands Tools for Consultation Agent
# ---------------------------------------------------------
@tool
def fetch_user_profile_and_ingredients(user_id: str) -> str:
    """Fetches user questionnaire answers, the extracted ingredient array, and the inferred product type."""
    print(f"\n⚡ [TOOL 1: DynamoDB] Fetching profile for: {user_id}")
    try:
        response = user_table.get_item(Key={"user_id": user_id})
        item = response.get("Item", {})

        profile_data = {
            "user_id": user_id,
            "latest_scanned_ingredients": item.get("latest_scanned_ingredients", []),
            "inferred_product_type": item.get("inferred_product_type", "Unknown"),
            "patientAnswers": item.get("patientAnswers", {})
        }
        print(f"   -> Found {len(profile_data['latest_scanned_ingredients'])} ingredients for a {profile_data['inferred_product_type']}.")
        return json.dumps(profile_data)

    except Exception as e:
        print(f"❌ Error fetching from DynamoDB: {e}")
        return json.dumps({"error": str(e)})

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
# 4. Consultation Agent Setup
# ---------------------------------------------------------
gemini_model = GeminiModel(
    model_id="gemini-3.5-flash-lite",
    client_args={"api_key": os.environ.get("GEMINI_API_KEY")},
)

skincare_agent = Agent(
    model=gemini_model,
    system_prompt=(
        "You are an expert cosmetic dermatologist AI assistant.\n"
        "1. When provided a user ID, call `fetch_user_profile_and_ingredients` to retrieve their profile, their extracted ingredients, and the AI-inferred product type.\n"
        "2. Take the ingredient array and call `batch_lookup_ingredients` to get fuzzy-matched database records.\n"
        "3. Synthesize the findings into a clear, professional assessment. Explicitly mention the inferred product type (e.g., 'I see you uploaded a moisturizer...') and address their specific skin goals."
    ),
    tools=[fetch_user_profile_and_ingredients, batch_lookup_ingredients],
)

# ---------------------------------------------------------
# 5. FastAPI Server Integration & Pipeline Orchestration
# ---------------------------------------------------------
app = FastAPI()

class ChatPayload(BaseModel):
    message: str
    history: list = []
    user_id: str = "user_hackathon_final_test" 

def run_vision_extraction_pipeline(user_id: str, new_image_keys: list):
    """Agent 1: Downloads S3 images, extracts ingredients, infers product type, saves to DB."""
    print(f"\n👁️ [VISION AGENT] Processing {len(new_image_keys)} new images for {user_id}...")
    images_data = []
    
    try:
        for key in new_image_keys:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            img = Image.open(BytesIO(obj['Body'].read()))
            images_data.append(img)
            
        prompt = (
            "I am providing up to 5 images of different skincare product ingredient labels. "
            "1. Extract EVERY SINGLE ingredient from ALL the provided images and combine them into one master list of strings. Do not skip any images. "
            "2. Infer the product types for ALL products shown (e.g., 'Cleanser, Serum, and Sunscreen'). "
            "Return ONLY a valid JSON object exactly like this: {\"inferred_product_type\": \"...\", \"ingredients\": [\"...\", \"...\"]}"
        )        
        
        response = genai_client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[prompt] + images_data
        )
        
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        extracted_data = json.loads(raw_text)
        
        user_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET latest_scanned_ingredients = :ing, inferred_product_type = :ptype, processed_images_cache = :cache",
            ExpressionAttributeValues={
                ":ing": extracted_data.get("ingredients", []),
                ":ptype": extracted_data.get("inferred_product_type", "Unknown"),
                ":cache": new_image_keys 
            }
        )
        print(f"✅ [VISION AGENT] Extracted {len(extracted_data.get('ingredients', []))} ingredients.")
        
    except Exception as e:
        print(f"❌ [VISION AGENT] Failed to extract data: {e}")

@app.post("/invoke")
async def invoke_agent(payload: ChatPayload):
    print(f"\n🚀 Receiving UI request for {payload.user_id}")
    
    # --- PIPELINE STEP 1: Vision Extraction Check ---
    try:
        response = user_table.get_item(Key={"user_id": payload.user_id})
        item = response.get("Item", {})
        scanned_images = item.get("scanned_images", [])
        processed_images_cache = item.get("processed_images_cache", [])
        if scanned_images and scanned_images != processed_images_cache:
            await asyncio.to_thread(run_vision_extraction_pipeline, payload.user_id, scanned_images)
    except Exception as e:
        print(f"⚠️ Pipeline check failed: {e}")

   # --- PIPELINE STEP 2: True Real-Time Streaming ---
    async def stream_generator():
        try:
            profile_json = fetch_user_profile_and_ingredients(payload.user_id)
            try:
                ingredients = json.loads(profile_json).get("latest_scanned_ingredients", [])
            except:
                ingredients = []
            db_results = batch_lookup_ingredients(ingredients)
            
            sys_prompt = (
                "You are an expert cosmetic dermatologist AI assistant.\n"
                f"User Profile: {profile_json}\n"
                f"Database Matches: {db_results}\n"
                "Synthesize the findings into a clear, professional assessment."
            )
            
            response = genai_client.models.generate_content_stream(
                model='gemini-3.5-flash-lite',
                contents=f"User says: {payload.message}",
                config={'system_instruction': sys_prompt}
            )
            
            for chunk in response:
                if chunk.text:
                    # Break into words and yield with a micro-delay
                    words = chunk.text.split(' ')
                    for i, word in enumerate(words):
                        yield word + (" " if i < len(words)-1 else "")
                        await asyncio.sleep(0.03) # 30ms delay forces flush & simulates typing
                        
        except Exception as e:
            yield f"Error generating response: {str(e)}"
            
    return StreamingResponse(stream_generator(), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
