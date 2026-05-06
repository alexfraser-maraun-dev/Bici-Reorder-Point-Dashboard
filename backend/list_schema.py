import os
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv()

client = bigquery.Client()
dataset_id = os.getenv("BQ_DATASET", "bici-klaviyo-datasync.light_speed_retailne")
table = client.get_table(f"{dataset_id}.LS_itemshop_history")
print("LS_itemshop_history schema:")
for schema_field in table.schema:
    print(f"{schema_field.name} ({schema_field.field_type})")
