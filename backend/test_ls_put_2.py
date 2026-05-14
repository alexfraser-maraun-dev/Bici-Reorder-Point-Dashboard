import os
import json
from dotenv import load_dotenv
import requests

load_dotenv(dotenv_path="/Users/alesfraser-maraun/Desktop/BICI_replen_level_automation/backend/.env")

from app.services.lightspeed_client import LightspeedClient

client = LightspeedClient()

item_shop_id = "610" # from previous run

# Try with itemShopID in payload
url = f"{client.base_url}/ItemShop/{item_shop_id}.json"
payload = {
    "ItemShop": {
        "itemShopID": item_shop_id,
        "reorderPoint": "10",
        "reorderLevel": "20"
    }
}
print(f"Sending payload: {json.dumps(payload)}")
response = requests.put(url, headers=client._get_headers(), json=payload)
print(f"Status Code: {response.status_code}")
print(f"Result: {json.dumps(response.json(), indent=2)}")
