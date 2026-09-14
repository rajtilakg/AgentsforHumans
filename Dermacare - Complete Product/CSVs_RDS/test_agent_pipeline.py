import os
import sys
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# 1. Load Database Connection
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "skincaredb")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL)

# ==========================================
# TEST CASE 1: MOCK INPUT DATA
# ==========================================
print("==================================================")
print("🧪 RUNNING HACKATHON AGENT SIMULATION TEST")
print("==================================================")

# Simulated DynamoDB Profile
user_profile = {
    "user_id": "user_hackathon_demo",
    "skin_biome": "oily / acne-prone",
    "budget_usd": 35.0,
    "acute_condition": "active_breakout",
    "current_shelf_product_ids": ["P439055"], # Let's say user already uses a Retinol cream
    "current_routine_active_names": ["retinol"]
}

# Simulated Nova Pro OCR Output (User took a photo of an AHA Exfoliator)
scanned_product = {
    "scanned_name": "Glycolic Acid 7% Exfoliating Toner",
    "ocr_extracted_ingredients": [
        "water aqua", 
        "glycolic acid", 
        "aloe barbadensis leaf water", 
        "panax ginseng root extract"
    ]
}

# Simulated Weather API
environmental_data = {
    "forecast_tomorrow_uv_index": 8.5, # Very High UV
    "condition": "Sunny"
}

print(f"👤 User Profile: {user_profile['skin_biome']}, Budget: ${user_profile['budget_usd']}")
print(f"📸 Scanned Product: {scanned_product['scanned_name']}")
print(f"☀️ Tomorrow's UV Index: {environmental_data['forecast_tomorrow_uv_index']}\n")

# ==========================================
# TEST CASE 2: RELATIONAL DATABASE QUERIES
# ==========================================
with engine.connect() as conn:
    # 1. Match Scanned Ingredients against Ingredients Master Table
    print("🔍 [Step 1] Resolving Scanned Ingredients in Database...")
    formatted_ingredients = [f"%{ing}%" for ing in scanned_product["ocr_extracted_ingredients"]]
    
    match_query = text("""
        SELECT id, inci_name, potency_tier, time_of_day, barrier_tax_score, sun_sensitivity
        FROM ingredients
        WHERE inci_name ILIKE ANY(:ing_list);
    """)
    matched_ingredients = conn.execute(match_query, {"ing_list": formatted_ingredients}).fetchall()
    
    matched_ing_ids = []
    scanned_barrier_tax = 0
    photosensitive_flag = False

    for row in matched_ingredients:
        matched_ing_ids.append(row.id)
        scanned_barrier_tax += row.barrier_tax_score
        if row.sun_sensitivity:
            photosensitive_flag = True
        print(f"   -> Matched: {row.inci_name} (ID: {row.id}) | Potency: {row.potency_tier} | Barrier Tax: {row.barrier_tax_score} | Sun Sensitive: {row.sun_sensitivity}")

    # 2. Check for Chemical Conflicts between Scanned Product & Current Shelf
    print("\n⚔️ [Step 2] Checking Chemical Conflicts against User's Shelf...")
    conflict_query = text("""
        SELECT 
            i1.inci_name AS scanned_active,
            i2.inci_name AS shelf_active,
            ix.interaction_type
        FROM ingredient_interactions ix
        JOIN ingredients i1 ON ix.ingredient_id_1 = i1.id
        JOIN ingredients i2 ON ix.ingredient_id_2 = i2.id
        WHERE ix.ingredient_id_1 = ANY(:scanned_ids)
          AND i2.inci_name = ANY(:shelf_actives);
    """)
    
    conflicts_found = conn.execute(conflict_query, {
        "scanned_ids": matched_ing_ids,
        "shelf_actives": user_profile["current_routine_active_names"]
    }).fetchall()

    if conflicts_found:
        for conf in conflicts_found:
            print(f"   ⚠️ CONFLICT DETECTED: {conf.scanned_active.upper()} (Scanned) + {conf.shelf_active.upper()} (Shelf) -> Type: {conf.interaction_type.upper()}")
    else:
        print("   ✅ No direct chemical conflicts found.")

    # 3. Find Budget-Friendly Compatible Alternatives from Products Table
    print(f"\n💡 [Step 3] Querying Compatible Alternatives under ${user_profile['budget_usd']}...")
    alt_query = text("""
        SELECT p.name, p.brand, p.price
        FROM products p
        WHERE p.price <= :budget
          AND (p.name ILIKE '%gel%' OR p.name ILIKE '%water%')
        ORDER BY p.price ASC
        LIMIT 3;
    """)
    alternatives = conn.execute(alt_query, {"budget": user_profile["budget_usd"]}).fetchall()
    for alt in alternatives:
        print(f"   -> {alt.brand} - {alt.name} (${alt.price})")

# ==========================================
# TEST CASE 3: COMPILE CONTEXT FOR GEMINI FLASH
# ==========================================
print("\n==================================================")
print("🤖 AGENT PAYLOAD READY FOR GEMINI FLASH")
print("==================================================")

gemini_payload = {
    "user_state": user_profile,
    "scanned_product": scanned_product["scanned_name"],
    "chemical_conflicts": [f"{c.scanned_active} conflicts with {c.shelf_active}" for c in conflicts_found],
    "total_scanned_barrier_tax": scanned_barrier_tax,
    "has_sun_sensitivity_risk": photosensitive_flag,
    "uv_forecast": environmental_data["forecast_tomorrow_uv_index"]
}

print(json.dumps(gemini_payload, indent=2))
print("\n✅ TEST COMPLETE: Your database successfully resolved matching, conflicts, taxes, and alternatives!")