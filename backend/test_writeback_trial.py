import os
import sys
from dotenv import load_dotenv

# Add the backend directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.lightspeed_client import LightspeedClient

def run_trial():
    load_dotenv()
    client = LightspeedClient()
    
    sku = "210000049579"
    location = "Bici Adanac"
    
    print(f"--- Running Writeback Trial for SKU: {sku} at {location} ---")
    
    # 1. Find the Item by SKU
    print(f"Searching for item in Lightspeed...")
    items = client.get_item_by_sku(sku)
    
    if not items or len(items) == 0:
        print(f"Error: SKU {sku} not found in Lightspeed.")
        return
        
    item = items[0]
    item_id = item['itemID']
    description = item['description']
    print(f"Found Item: {description} (itemID: {item_id})")
    
    # 2. Get the ItemShop relation for Adanac (shopID: 3)
    print(f"Fetching shop relations for {location} (shopID: 3)...")
    shop_id = client.shop_id_map.get(location)
    item_shops = client.get_item_shops_full(item_id)
    
    target_shop_relation = None
    for shop in item_shops:
        if str(shop['shopID']) == shop_id:
            target_shop_relation = shop
            break
            
    if not target_shop_relation:
        print(f"Error: Could not find relation for shopID {shop_id} on this item.")
        return
        
    item_shop_id = target_shop_relation['itemShopID']
    print(f"DEBUG: Shop Relation Keys: {target_shop_relation.keys()}")
    current_rop = target_shop_relation.get('reorderPoint', 0)
    current_dl = target_shop_relation.get('reorderLevel', 0) # Lightspeed DL is often called reorderLevel in API
    
    print(f"Current levels at {location}: ROP={current_rop}, DL={current_dl}")
    
    # 3. Perform the UPDATE (TRIAL)
    # We'll increment the ROP by 1 for testing, or set to a specific value
    test_rop = int(current_rop) + 1 if current_rop else 1
    test_dl = int(current_dl) if current_dl else 1
    
    print(f"PROPOSED UPDATE: Reorder Point -> {test_rop}")
    
    result = client.update_reorder_levels(item_shop_id, test_rop, test_dl)
    
    if result:
        print("SUCCESS! Lightspeed updated successfully.")
        print(f"New ROP for {sku} at {location}: {result.get('reorderPoint')}")
    else:
        print("FAILED: API update failed. Check logs.")

if __name__ == "__main__":
    run_trial()
