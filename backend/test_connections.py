import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

print("Testing Supabase...")
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("DATABASE_URL not found")
else:
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            print("✅ Supabase connection successful")
    except Exception as e:
        print(f"❌ Supabase failed: {e}")

print("Testing BigQuery...")
from app.services.bigquery_sync import fetch_unified_metrics
try:
    df = fetch_unified_metrics(trailing_days=60)
    print(f"✅ BigQuery connection successful. Returned {len(df)} rows.")
except Exception as e:
    print(f"❌ BigQuery failed: {e}")
