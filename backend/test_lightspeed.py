import os
from dotenv import load_dotenv
load_dotenv()

from app.services.lightspeed_client import LightspeedClient

def test_lightspeed():
    print("--- Testing Lightspeed API Connection ---")
    ls = LightspeedClient()
    
    # Test Item ID from previous samples (Carbs Fuel: 210000117942)
    test_item_id = "210000117942"
    
    print(f"Attempting to fetch shop map for Item ID: {test_item_id}")
    try:
        shop_map = ls.get_item_shops(test_item_id)
        if shop_map:
            print("Successfully connected to Lightspeed!")
            print("Found ItemShops:")
            for shop, ishop_id in shop_map.items():
                print(f"- {shop}: {ishop_id}")
        else:
            print("Failed to fetch shops. Check your credentials and Item ID.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_lightspeed()
