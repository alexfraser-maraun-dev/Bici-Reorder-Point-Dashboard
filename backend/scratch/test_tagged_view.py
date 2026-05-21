import os
from dotenv import load_dotenv
load_dotenv()

from app.services.bigquery_sync import fetch_tagged_items_metrics, QUALIFIED_ITEMS_VIEW

print("QUALIFIED_ITEMS_VIEW is:", QUALIFIED_ITEMS_VIEW)
try:
    df = fetch_tagged_items_metrics(force_refresh=True)
    print(f"Success! fetch_tagged_items_metrics returned {len(df)} rows.")
    if len(df) > 0:
        print("First few rows:")
        print(df.head(5))
except Exception as e:
    print(f"Failed: {e}")
