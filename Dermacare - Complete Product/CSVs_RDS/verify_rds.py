import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = f"postgresql://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASS')}@{os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT', '5432')}/{os.environ.get('DB_NAME')}"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("--- 1. TABLE ROW COUNTS ---")
    for table in ['products', 'ingredients', 'product_ingredients', 'ingredient_interactions']:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        print(f"Table '{table}': {count} rows")

    print("\n--- 2. VERIFY CREATED INDEXES ---")
    index_query = """
    SELECT tablename, indexname 
    FROM pg_indexes 
    WHERE schemaname = 'public'
    ORDER BY tablename, indexname;
    """
    indexes = conn.execute(text(index_query)).fetchall()
    for tablename, indexname in indexes:
        print(f"[{tablename}] -> {indexname}")

    print("\n--- 3. SAMPLE RELATIONAL JOIN: RETINOL CONFLICTS ---")
    conflict_query = """
    SELECT 
        i1.inci_name AS active_1, 
        i2.inci_name AS active_2, 
        ix.interaction_type
    FROM ingredient_interactions ix
    JOIN ingredients i1 ON ix.ingredient_id_1 = i1.id
    JOIN ingredients i2 ON ix.ingredient_id_2 = i2.id
    WHERE i1.inci_name = 'retinol'
    LIMIT 5;
    """
    sample_rules = conn.execute(text(conflict_query)).fetchall()
    for row in sample_rules:
        print(f"Rule: {row[0]} + {row[1]} -> {row[2]}")