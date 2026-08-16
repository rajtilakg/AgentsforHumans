import os
import sys
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Automatically detect the directory where this script lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 1. Load .env from the script's folder
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "skincaredb")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS")

if not all([DB_HOST, DB_NAME, DB_USER, DB_PASS]):
    print("❌ Error: Missing database environment variables in .env file.")
    sys.exit(1)

# 2. Connect to RDS
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
print("Connecting to AWS RDS PostgreSQL...")

try:
    engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 10})
    with engine.connect() as conn:
        print("✅ Connection established successfully!")
except Exception as e:
    print(f"❌ Connection failed: {e}")
    sys.exit(1)

# 3. Create Schema Tables
print("\nCreating relational schema...")
create_tables_sql = """
DROP TABLE IF EXISTS ingredient_interactions CASCADE;
DROP TABLE IF EXISTS product_ingredients CASCADE;
DROP TABLE IF EXISTS ingredients CASCADE;
DROP TABLE IF EXISTS products CASCADE;

CREATE TABLE products (
    id VARCHAR(50) PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    category TEXT,
    price NUMERIC(10, 2)
);

CREATE TABLE ingredients (
    id INT PRIMARY KEY,
    inci_name TEXT NOT NULL,
    potency_tier INT DEFAULT 1,
    time_of_day VARCHAR(10) DEFAULT 'ANY',
    barrier_tax_score INT DEFAULT 0,
    sun_sensitivity BOOLEAN DEFAULT FALSE
);

CREATE TABLE product_ingredients (
    product_id VARCHAR(50) REFERENCES products(id) ON DELETE CASCADE,
    ingredient_id INT REFERENCES ingredients(id) ON DELETE CASCADE,
    concentration_rank INT,
    PRIMARY KEY (product_id, ingredient_id)
);

CREATE TABLE ingredient_interactions (
    ingredient_id_1 INT REFERENCES ingredients(id) ON DELETE CASCADE,
    ingredient_id_2 INT REFERENCES ingredients(id) ON DELETE CASCADE,
    interaction_type VARCHAR(20) NOT NULL,
    PRIMARY KEY (ingredient_id_1, ingredient_id_2)
);
"""

with engine.begin() as conn:
    conn.execute(text(create_tables_sql))
print("✅ Tables created.")

# 4. Stream & Load Data using absolute paths
try:
    print("\n[1/4] Loading table_products_clean.csv...")
    path_products = os.path.join(BASE_DIR, 'table_products_clean.csv')
    df_products = pd.read_csv(path_products)[['id', 'name', 'brand', 'category', 'price']]
    df_products.to_sql('products', engine, if_exists='append', index=False, method='multi', chunksize=1000)
    print(f"  Loaded {len(df_products)} products.")

    print("\n[2/4] Loading table_ingredients_clean.csv...")
    path_ingredients = os.path.join(BASE_DIR, 'table_ingredients_clean.csv')
    df_ingredients = pd.read_csv(path_ingredients)[['id', 'inci_name', 'potency_tier', 'time_of_day', 'barrier_tax_score', 'sun_sensitivity']]
    df_ingredients.to_sql('ingredients', engine, if_exists='append', index=False, method='multi', chunksize=1000)
    print(f"  Loaded {len(df_ingredients)} ingredients.")

    print("\n[3/4] Loading table_product_ingredients_clean.csv...")
    path_bridge = os.path.join(BASE_DIR, 'table_product_ingredients_clean.csv')
    df_bridge = pd.read_csv(path_bridge)[['product_id', 'ingredient_id', 'concentration_rank']]
    df_bridge.to_sql('product_ingredients', engine, if_exists='append', index=False, method='multi', chunksize=2000)
    print(f"  Loaded {len(df_bridge)} bridge mappings.")

    print("\n[4/4] Loading table_ingredient_interactions_clean.csv...")
    path_interactions = os.path.join(BASE_DIR, 'table_ingredient_interactions_clean.csv')
    df_interactions = pd.read_csv(path_interactions)[['ingredient_id_1', 'ingredient_id_2', 'interaction_type']]
    df_interactions.to_sql('ingredient_interactions', engine, if_exists='append', index=False, method='multi', chunksize=1000)
    print(f"  Loaded {len(df_interactions)} interaction rules.")

except Exception as e:
    print(f"❌ Error during data loading: {e}")
    sys.exit(1)

# 5. Build Performance Indexes Post-Load
print("\nCreating indexes for accelerated query execution...")
create_indexes_sql = """
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_product_ingredients_ingredient ON product_ingredients(ingredient_id);
CREATE INDEX IF NOT EXISTS idx_ingredients_inci_name ON ingredients(inci_name);
"""

with engine.begin() as conn:
    conn.execute(text(create_indexes_sql))
print("✅ Performance indexes applied successfully.")

print("\n🚀 ALL DATA SUCCESSFULLY LOADED & INDEXED IN AWS RDS POSTGRESQL!")