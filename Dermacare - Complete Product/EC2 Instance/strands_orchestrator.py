import os
import json
import boto3
import psycopg2
import asyncio
from io import BytesIO
import base64
from PIL import Image
from google import genai
from dotenv import load_dotenv
from strands import Agent, tool
from strands.models.gemini import GeminiModel
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn

# ---------------------------------------------------------
# 1. Environment & AWS Configuration
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"), override=True) 

region = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")

dynamodb = boto3.resource("dynamodb", region_name=region)
user_table = dynamodb.Table("UserSkinProfiles")

s3_client = boto3.client("s3", region_name=region)
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "agents-for-humans-images")

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
# 3. Enhanced Strands Tools for Consultation Agent
# ---------------------------------------------------------
@tool
def fetch_user_profile_and_ingredients(user_id: str) -> str:
    """Fetches comprehensive user profile from DynamoDB"""
    print(f"\n⚡ [TOOL 1: DynamoDB] Fetching profile for: {user_id}")
    try:
        response = user_table.get_item(Key={"user_id": user_id})
        item = response.get("Item", {})

        profile_data = {
            "user_id": user_id,
            "latest_scanned_ingredients": item.get("latest_scanned_ingredients", []),
            "inferred_product_type": item.get("inferred_product_type", "Unknown"),
            "scanned_images": item.get("scanned_images", []),
            "skin_biome": item.get("skin_biome", "unknown"),
            "acute_condition": item.get("acute_condition", "none"),
            "climate_type": item.get("climate_type", "temperate"),
            # Fixed hardcoded budget
            "budget_usd": item.get("budget_usd", "No limit specified"),
            "known_allergies": item.get("known_allergies", []),
            "current_routine": item.get("current_routine", {}),
            "product_contexts": item.get("product_contexts", {}),
            "patientAnswers": item.get("patientAnswers", []),
        }
        
        print(f"   -> Found {len(profile_data['latest_scanned_ingredients'])} ingredients for a {profile_data['inferred_product_type']}.")
        print(f"   -> Skin type: {profile_data['skin_biome']}, Budget: {profile_data['budget_usd']}")
        
        return json.dumps(profile_data)

    except Exception as e:
        print(f"❌ Error fetching from DynamoDB: {e}")
        return json.dumps({"error": str(e)})

@tool
def analyze_ingredients_with_properties(ingredients_list_json: str) -> str:
    """Performs batch fuzzy search on PostgreSQL and returns detailed ingredient properties"""
    try:
        if isinstance(ingredients_list_json, list):
            ingredients = ingredients_list_json
        else:
            ingredients = json.loads(ingredients_list_json)
    except Exception:
        ingredients = [ingredients_list_json]

    print(f"\n⚡ [TOOL 2: RDS] Analyzing {len(ingredients)} ingredients with properties...")
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
                SELECT inci_name, id, potency_tier, time_of_day, barrier_tax_score, sun_sensitivity,
                       GREATEST(similarity(lower(inci_name), lower(%s)),
                                word_similarity(lower(%s), lower(inci_name))) AS match_score
                FROM ingredients
                ORDER BY match_score DESC LIMIT 1;
            """
            cursor.execute(query, (clean_name, clean_name))
            row = cursor.fetchone()

            if row:
                results.append({
                    "queried": clean_name,
                    "matched_inci": row[0],
                    "ingredient_id": row[1],
                    "potency_tier": row[2],
                    "time_of_day": row[3],
                    "barrier_tax_score": row[4],
                    "sun_sensitivity": row[5],
                    "match_score": round(float(row[6]), 3)
                })
            else:
                results.append({
                    "queried": clean_name,
                    "matched_inci": "No match",
                    "ingredient_id": None,
                    "match_score": 0.0
                })

    except Exception as e:
        print(f"❌ DB error: {e}")
        if conn: conn.rollback()
        return json.dumps({"error": str(e), "partial_results": results})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    return json.dumps(results)

@tool
def check_ingredient_interactions(ingredient_ids_json: str) -> str:
    """Checks for conflicts and synergies between ingredients using their IDs."""
    try:
        if isinstance(ingredient_ids_json, list):
            ingredient_ids = ingredient_ids_json
        else:
            ingredient_ids = json.loads(ingredient_ids_json)
    except Exception:
        return json.dumps({"error": "Invalid ingredient IDs format"})

    ingredient_ids = [id for id in ingredient_ids if id is not None]
    
    if len(ingredient_ids) < 2:
        return json.dumps({"conflicts": [], "synergies": [], "message": "Need at least 2 ingredients to check interactions"})

    print(f"\n⚡ [TOOL 3: RDS] Checking interactions for {len(ingredient_ids)} ingredients...")
    
    conflicts = []
    synergies = []
    conn = None
    cursor = None

    try:
        conn = get_pg_connection()
        cursor = conn.cursor()

        placeholders = ','.join(['%s'] * len(ingredient_ids))
        cursor.execute(f"""
            SELECT id, inci_name FROM ingredients WHERE id IN ({placeholders})
        """, ingredient_ids)
        
        id_to_name = {row[0]: row[1] for row in cursor.fetchall()}

        for i, id1 in enumerate(ingredient_ids):
            for id2 in ingredient_ids[i+1:]:
                cursor.execute("""
                    SELECT interaction_type 
                    FROM ingredient_interactions 
                    WHERE ingredient_id_1 = %s AND ingredient_id_2 = %s
                """, (id1, id2))
                
                result = cursor.fetchone()
                if result:
                    interaction = {
                        "ingredient_1": id_to_name.get(id1, f"ID {id1}"),
                        "ingredient_2": id_to_name.get(id2, f"ID {id2}"),
                        "type": result[0]
                    }
                    if result[0] == "conflict":
                        conflicts.append(interaction)
                    elif result[0] == "synergy":
                        synergies.append(interaction)

    except Exception as e:
        print(f"❌ DB error: {e}")
        if conn: conn.rollback()
        return json.dumps({"error": str(e)})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    return json.dumps({
        "conflicts": conflicts,
        "synergies": synergies,
        "total_checked": len(ingredient_ids)
    })

@tool
def get_product_recommendations(concern: str, max_price: int = 150) -> str:
    """Recommends real products from the database based on skin concerns."""
    print(f"\n⚡ [TOOL 4: RDS] Finding products for '{concern}' under ${max_price}...")
    
    conn = None
    cursor = None
    
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        
        search_term = f"%{concern}%"
        cursor.execute("""
            SELECT p.id, p.name, p.brand, p.category, p.price
            FROM products p
            WHERE (LOWER(p.name) LIKE LOWER(%s) OR LOWER(p.brand) LIKE LOWER(%s))
            AND p.price <= %s
            AND p.category = 'Skincare'
            ORDER BY p.price ASC
            LIMIT 5
        """, (search_term, search_term, max_price))
        
        products = cursor.fetchall()
        
        if not products:
            return json.dumps({"message": f"No products found for '{concern}' under ${max_price}", "products": []})
        
        results = []
        for prod in products:
            prod_id, name, brand, category, price = prod
            
            cursor.execute("""
                SELECT i.inci_name, pi.concentration_rank
                FROM product_ingredients pi
                JOIN ingredients i ON pi.ingredient_id = i.id
                WHERE pi.product_id = %s
                ORDER BY pi.concentration_rank ASC
                LIMIT 5
            """, (prod_id,))
            
            ingredients = [row[0] for row in cursor.fetchall()]
            
            results.append({
                "name": name,
                "brand": brand,
                "price": float(price) if price else 0,
                "top_ingredients": ingredients
            })
        
    except Exception as e:
        print(f"❌ DB error: {e}")
        if conn: conn.rollback()
        return json.dumps({"error": str(e)})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
    
    return json.dumps({"products": results, "count": len(results)})

# ---------------------------------------------------------
# 4. Consultation Agent Setup with Enhanced System Prompt
# ---------------------------------------------------------
gemini_model = GeminiModel(
    model_id="gemini-3.5-flash-lite",
    client_args={"api_key": os.environ.get("GEMINI_API_KEY")},
)

ENHANCED_SYSTEM_PROMPT = """You are DermaCare AI, an expert cosmetic skincare consultant specializing in ingredient analysis and personalized recommendations.

## YOUR CAPABILITIES
You have access to a comprehensive skincare database with:
- 6,222 ingredients with detailed properties (potency, timing, barrier impact, sun sensitivity)
- 113 documented ingredient interactions (conflicts and synergies)
- 2,269 real products with complete ingredient lists
- User's uploaded product images with extracted ingredients
- User's comprehensive skin profile (skin type, climate, budget, allergies, acute conditions)
- User's questionnaire answers about their skin concerns and goals

## YOUR WORKFLOW
1. **Fetch User Profile**: Call `fetch_user_profile_and_ingredients(user_id)` to get their complete profile.
2. **Analyze Ingredients**: Call `analyze_ingredients_with_properties(ingredients)` to get detailed properties for each ingredient
3. **Check Interactions**: Call `check_ingredient_interactions(ingredient_ids)` to identify conflicts and synergies
4. **Recommend Products** (if asked): Call `get_product_recommendations(concern, max_price)` for targeted suggestions

## RESPONSE STRUCTURE
**1. Product Recognition & Context**
- Start by acknowledging what products they uploaded
- Reference any product_contexts they provided (AM/PM usage, frequency, notes)
- Acknowledge their skin_biome and acute_condition if relevant

**2. Ingredient Analysis**
- Highlight KEY ACTIVES first (potency_tier = 3)
- Note if they require PM use (time_of_day = "PM")
- Flag if they cause sun_sensitivity
- Warn if barrier_tax_score > 2, ESPECIALLY if skin_biome = "sensitive" or "dry"

**3. Interaction Warnings** (CRITICAL SAFETY)
- If conflicts exist, clearly warn about specific ingredient pairs
- Explain WHY they conflict
- Suggest separation strategies

**4. Climate & Allergy Considerations**
- ALWAYS check known_allergies before recommending anything

**5. Routine Optimization**
- Use their current_routine (AM/PM lists) as a starting point
- Address their specific skin concerns from patientAnswers

## SAFETY GUIDELINES
- NEVER diagnose medical conditions (eczema, rosacea, dermatitis, acne vulgaris)
- NEVER prescribe treatments or medications
- ALWAYS recommend seeing a dermatologist for persistent/severe conditions
- Focus on cosmetic appearance improvements only

## TONE & STYLE
- Professional yet approachable and friendly
- Evidence-based (reference ingredient properties from database)
- Specific and actionable
- Honest about limitations
- NEVER use LaTeX formatting (like $\rightarrow$). If you need an arrow, use standard text like "->" or "→"."""

skincare_agent = Agent(
    model=gemini_model,
    system_prompt=ENHANCED_SYSTEM_PROMPT,
    tools=[
        fetch_user_profile_and_ingredients, 
        analyze_ingredients_with_properties, 
        check_ingredient_interactions,
        get_product_recommendations
    ],
)

# ---------------------------------------------------------
# 5. FastAPI Server Integration & Pipeline Orchestration
# ---------------------------------------------------------
app = FastAPI()

class ChatPayload(BaseModel):
    message: str
    history: list = []
    user_id: str
    inline_image_keys: Optional[list] = []

def run_vision_extraction_pipeline(user_id: str, new_image_keys: list):
    """Agent 1: Extracts ingredients for routine images saved in DynamoDB."""
    print(f"\n👁️ [VISION AGENT] Processing {len(new_image_keys)} routine images for {user_id}...")
    images_data = []
    
    try:
        for key in new_image_keys:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            img = Image.open(BytesIO(obj['Body'].read()))
            images_data.append(img)
            
        prompt = (
            "Extract EVERY ingredient from ALL images into one master list. Preserve exact INCI names. "
            "Infer ALL product types shown. OUTPUT STRICT JSON ONLY: "
            "{\"inferred_product_type\": \"type\", \"ingredients\": [\"ingredient1\", \"ingredient2\"]}"
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
        print(f"✅ [VISION AGENT] Extracted {len(extracted_data.get('ingredients', []))} routine ingredients.")
        
    except Exception as e:
        print(f"❌ [VISION AGENT] Failed to extract data: {e}")

def extract_inline_ingredients(image_keys: list):
    """Agent 1b: Extracts ingredients strictly for one-off transient images."""
    print(f"\n👁️ [VISION AGENT] Extracting ingredients from {len(image_keys)} transient images...")
    images_data = []
    try:
        for key in image_keys:
            if not key: continue
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            img = Image.open(BytesIO(obj['Body'].read()))
            images_data.append(img)
        
        if not images_data:
            return [], images_data

        prompt = (
            "Extract EVERY ingredient from the provided product labels into a master list. "
            "Preserve exact INCI names. Output STRICT JSON only: "
            "{\"ingredients\": [\"water\", \"niacinamide\"]}"
        )
        
        response = genai_client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=[prompt] + images_data
        )
        
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        extracted_data = json.loads(raw_text)
        ingredients = extracted_data.get("ingredients", [])
        print(f"✅ [VISION AGENT] Extracted {len(ingredients)} transient ingredients.")
        return ingredients, images_data
        
    except Exception as e:
        print(f"❌ [VISION AGENT] Transient extraction failed: {e}")
        return [], images_data

@app.post("/invoke")
async def invoke_agent(payload: ChatPayload):
    print(f"\n🚀 Receiving UI request for {payload.user_id}")
    
    vision_extraction_needed = False
    scanned_images = []
    try:
        response = user_table.get_item(Key={"user_id": payload.user_id})
        item = response.get("Item", {})
        scanned_images = item.get("scanned_images", [])
        processed_images_cache = item.get("processed_images_cache", [])
        if scanned_images and scanned_images != processed_images_cache:
            vision_extraction_needed = True
    except Exception as e:
        print(f"⚠️ Pipeline check failed: {e}")

    async def stream_generator():
        try:
            yield "📥 **Receiving your message...**\n\n"
            await asyncio.sleep(0.3)
            
            # 1. Process Routine Images (DynamoDB)
            if vision_extraction_needed:
                yield "🔍 **Checking your routine images...**\n\n"
                await asyncio.to_thread(run_vision_extraction_pipeline, payload.user_id, scanned_images)
                yield "✅ **Routine ingredients extracted**\n\n"
            
            yield "💾 **Fetching your skin profile from database...**\n\n"
            profile_json = await asyncio.to_thread(fetch_user_profile_and_ingredients, payload.user_id)
            try:
                profile_data = json.loads(profile_json)
                routine_ingredients = profile_data.get("latest_scanned_ingredients", [])
            except:
                routine_ingredients = []
            yield "✅ **Profile loaded successfully**\n\n"
            await asyncio.sleep(0.3)
            
            # 2. Process Transient Images (S3) BEFORE Database lookup
            contents_list = [f"User says: {payload.message}"]
            transient_ingredients = []
            
            if payload.inline_image_keys:
                valid_keys = [k for k in payload.inline_image_keys if k]
                if valid_keys:
                    yield f"📸 **Extracting ingredients from {len(valid_keys)} attached image(s)...**\n\n"
                    extracted_transient, loaded_images = await asyncio.to_thread(extract_inline_ingredients, valid_keys)
                    transient_ingredients = extracted_transient
                    contents_list.extend(loaded_images)
                    yield "✅ **Attached images processed**\n\n"

            # 3. Combine ingredients for a unified Database lookup
            all_ingredients = routine_ingredients + transient_ingredients
            
            if all_ingredients:
                yield f"🔬 **Analyzing {len(all_ingredients)} ingredients with database properties...**\n\n"
                ingredient_analysis = await asyncio.to_thread(analyze_ingredients_with_properties, all_ingredients)
                yield "✅ **Ingredient analysis complete**\n\n"
                
                try:
                    analysis_data = json.loads(ingredient_analysis)
                    ingredient_ids = [item.get("ingredient_id") for item in analysis_data if item.get("ingredient_id")]
                    if len(ingredient_ids) >= 2:
                        yield "🔍 **Checking for ingredient interactions...**\n\n"
                        interaction_results = await asyncio.to_thread(check_ingredient_interactions, ingredient_ids)
                        interactions = json.loads(interaction_results)
                        if interactions.get("conflicts"):
                            yield f"⚠️ **Found {len(interactions['conflicts'])} potential conflict(s)**\n\n"
                        if interactions.get("synergies"):
                            yield f"✨ **Found {len(interactions['synergies'])} beneficial synergy/synergies**\n\n"
                except:
                    interaction_results = json.dumps({"conflicts": [], "synergies": []})
            else:
                ingredient_analysis = json.dumps([])
                interaction_results = json.dumps({"conflicts": [], "synergies": []})
            
            yield "✨ **Generating your personalized recommendations...**\n\n"
            await asyncio.sleep(0.4)
            yield "---\n\n"
            
            sys_prompt = (
                 "You are DermaCare AI, an expert cosmetic skincare consultant.\n\n"
                f"USER PROFILE:\n{profile_json}\n\n"
                "=== INGREDIENT SOURCE BREAKDOWN ===\n"
                f"1. ROUTINE INGREDIENTS (Permanent products in sidebar): {routine_ingredients}\n"
                f"2. TRANSIENT INGREDIENTS (Products just attached in chat): {transient_ingredients}\n"
                "===================================\n\n"
                f"DATABASE ANALYSIS (Properties for all combined ingredients):\n{ingredient_analysis}\n\n"
                f"INGREDIENT INTERACTIONS:\n{interaction_results}\n\n"
                "Synthesize these findings into a clear, structured assessment following your system instructions. "
                "CRITICAL: If the user asks to compare an attached product to their routine, explicitly compare the TRANSIENT INGREDIENTS list against the ROUTINE INGREDIENTS list to see if they overlap or conflict. "
                "Do not rely solely on the text answers in their profile to determine their routine; the ROUTINE INGREDIENTS list represents their actual scanned products."
            )

            response = genai_client.models.generate_content_stream(
                model='gemini-3.5-flash-lite',
                contents=contents_list,
                config={'system_instruction': sys_prompt}
            )
            
            for chunk in response:
                if chunk.text:
                    words = chunk.text.split(' ')
                    for i, word in enumerate(words):
                        yield word + (" " if i < len(words)-1 else "")
                        await asyncio.sleep(0.03) 
                        
        except Exception as e:
            yield f"Error generating response: {str(e)}"
            
    return StreamingResponse(stream_generator(), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
