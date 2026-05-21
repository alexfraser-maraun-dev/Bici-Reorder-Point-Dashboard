import os
from dotenv import load_dotenv
load_dotenv()

from app.services.bigquery_sync import get_bq_client, APP_DATASET

client = get_bq_client()
view_id = f"{APP_DATASET}.replen_qualified_items"

try:
    table = client.get_table(view_id)
    print("View ID:", view_id)
    print("Table type:", table.table_type)
    if table.table_type == "VIEW":
        print("View definition:")
        print(table.view_query)
    else:
        print("Table schema:")
        for field in table.schema:
            print(f"  {field.name}: {field.field_type}")
except Exception as e:
    print("Failed to get view info:", e)
