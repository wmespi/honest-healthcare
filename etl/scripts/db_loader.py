import pandas as pd
import os
from sqlalchemy import create_engine, text
import sys

def load_gold_to_db():
    print(">>> [DB Loader] Starting sync from Gold CSV to Postgres...")
    
    # 1. Configuration
    csv_path = "/app/data/gold/emory_gold.csv"
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/honest_healthcare")
    table_name = "emory_negotiated_rates"
    
    # 2. Check source
    if not os.path.exists(csv_path):
        print(f"!!! [DB Loader] Source file not found: {csv_path}")
        return

    try:
        # 3. Read Data
        df = pd.read_csv(csv_path)
        print(f">>> [DB Loader] Loaded {len(df)} rows from CSV.")
        
        # 4. Connect to DB
        engine = create_engine(db_url)
        
        # 5. Write to DB (Replace existing table for fresh sync)
        # We'll use the DataFrame index as our 'id' column
        df.index.name = 'id'
        df.to_sql(table_name, engine, if_exists='replace', index=True)
        print(f">>> [DB Loader] Successfully synced to table: {table_name}")
        
        # 6. Add primary key constraint and indexes
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {table_name} ADD PRIMARY KEY (id)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_billing_code ON {table_name} (billing_code)"))
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_hospital_name ON {table_name} (hospital_name)"))
            conn.commit()
            print(">>> [DB Loader] Created primary key and performance indexes.")
            
    except Exception as e:
        print(f"!!! [DB Loader] Sync failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    load_gold_to_db()
