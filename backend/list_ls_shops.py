import os
import requests
from dotenv import load_dotenv
load_dotenv()

from app.services.lightspeed_client import LightspeedClient

def list_shops():
    print("--- Listing All Shops in Lightspeed ---")
    ls = LightspeedClient()
    url = f"{ls.base_url}/Shop.json"
    
    response = requests.get(url, headers=ls._get_headers())
    if response.status_code == 200:
        shops = response.json().get("Shop", [])
        if isinstance(shops, dict): shops = [shops]
        
        for s in shops:
            print(f"- {s.get('name')} (shopID: {s.get('shopID')})")
    else:
        print(f"Error: {response.text}")

if __name__ == "__main__":
    list_shops()
