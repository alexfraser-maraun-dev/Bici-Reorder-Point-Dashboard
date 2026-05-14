"""
Confirms that pushes correctly target the specific item+shop combination.
Tests all 3 shops for item 37 with different ROP/DL values to prove isolation.
"""
import os
from dotenv import load_dotenv
import requests

load_dotenv(dotenv_path="/Users/alesfraser-maraun/Desktop/BICI_replen_level_automation/backend/.env")

from app.services.lightspeed_client import LightspeedClient
client = LightspeedClient()

# Item 37's itemShopIDs per store (confirmed from previous test):
# shopID 2 (Victoria)   -> itemShopID 20380
# shopID 3 (Adanac)     -> itemShopID 67043
# shopID 20 (Langford)  -> itemShopID 2892281

shop_tests = [
    ("Victoria",    "2",  "20380",   11, 22),
    ("Bici Adanac", "3",  "67043",   33, 66),
    ("Langford",    "20", "2892281", 55, 110),
]

print("=== Setting distinct values per shop for item 37 ===\n")
for location, shop_id, item_shop_id, rop, dl in shop_tests:
    rec = {
        "system_id": "37",
        "sku": "TEST-37",
        "location": location,
        "recommended_reorder_point": rop,
        "recommended_desired_level": dl,
    }
    success = client.sync_recommendation(rec)
    print(f"  {location} (shopID={shop_id}, itemShopID={item_shop_id}) -> ROP={rop}, DL={dl}: {'✅' if success else '❌'}")

print("\n=== Verifying each shop got the correct distinct values ===\n")
all_correct = True
for location, shop_id, item_shop_id, rop, dl in shop_tests:
    url = f"{client.base_url}/ItemShop/{item_shop_id}.json"
    result = requests.get(url, headers=client._get_headers()).json().get("ItemShop", {})
    actual_rop = result.get("reorderPoint")
    actual_dl  = result.get("reorderLevel")
    correct = str(actual_rop) == str(rop) and str(actual_dl) == str(dl)
    all_correct = all_correct and correct
    print(f"  {location}: ROP={actual_rop} (exp {rop}), DL={actual_dl} (exp {dl}) {'✅' if correct else '❌ MISMATCH'}")

print(f"\nAll shops correctly isolated: {'✅ YES' if all_correct else '❌ NO'}")

# Reset all back to 0
print("\n=== Resetting all test values to 0 ===")
for location, shop_id, item_shop_id, rop, dl in shop_tests:
    url = f"{client.base_url}/ItemShop/{item_shop_id}.json"
    requests.put(url, headers=client._get_headers(), json={"reorderPoint": "0", "reorderLevel": "0"})
    print(f"  Reset {location} ✅")
