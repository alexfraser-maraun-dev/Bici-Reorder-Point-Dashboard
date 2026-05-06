import os
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv()

client = bigquery.Client()
dataset_id = os.getenv("BQ_DATASET", "bici-klaviyo-datasync.light_speed_retailne")
print(f"Listing tables in {dataset_id}...")
tables = client.list_tables(dataset_id)
for table in tables:
    print(table.table_id)
