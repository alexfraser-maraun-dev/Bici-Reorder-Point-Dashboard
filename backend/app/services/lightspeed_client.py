import requests
import os
import time
from typing import Dict, Any, Optional, List

class LightspeedClient:
    def __init__(self):
        self.account_id = os.getenv("LIGHTSPEED_ACCOUNT_ID")
        self.client_id = os.getenv("LIGHTSPEED_CLIENT_ID")
        self.client_secret = os.getenv("LIGHTSPEED_CLIENT_SECRET")
        self.refresh_token = os.getenv("LIGHTSPEED_REFRESH_TOKEN")
        self.bearer_token = os.getenv("LIGHTSPEED_BEARER_TOKEN")
        self.base_url = f"https://api.lightspeedapp.com/API/Account/{self.account_id}"
        
        # Mapping from Spreadsheet Names to Lightspeed shopIDs
        self.shop_id_map = {
            "Victoria": "2",
            "Bici Adanac": "3",
            "Langford": "20"
        }
        
    def _refresh_access_token(self):
        url = "https://cloud.lightspeedapp.com/oauth/access_token.php"
        payload = {
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token"
        }
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            new_token = response.json().get("access_token")
            self.bearer_token = new_token
            return new_token
        else:
            raise Exception(f"Failed to refresh Lightspeed token: {response.text}")

    def _get_headers(self):
        if not self.bearer_token:
            self._refresh_access_token()
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json"
        }

    def check_health(self) -> bool:
        """
        Pings the API to check if the credentials and connection are valid.
        """
        url = f"{self.base_url}.json"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            if response.status_code == 401:
                self._refresh_access_token()
                response = requests.get(url, headers=self._get_headers(), timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Lightspeed health check failed: {e}")
            return False

    def get_item_shops(self, item_id: str) -> Dict[str, str]:
        """
        Fetches the itemShopIDs for an item, mapped by their shopID.
        Returns {shopID: itemShopID}
        """
        url = f"{self.base_url}/Item/{item_id}.json"
        params = {"load_relations": '["ItemShops"]'}
        
        try:
            response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
            if response.status_code == 401:
                self._refresh_access_token()
                response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
                
            if response.status_code != 200:
                print(f"Error fetching item {item_id}: {response.text}")
                return {}
                
            data = response.json()
            item_shops = data.get("Item", {}).get("ItemShops", {}).get("ItemShop", [])
            
            if isinstance(item_shops, dict):
                item_shops = [item_shops]
                
            # Map shopID -> itemShopID
            mapping = {}
            for ishop in item_shops:
                s_id = str(ishop.get("shopID"))
                is_id = str(ishop.get("itemShopID"))
                mapping[s_id] = is_id
            return mapping
        except Exception as e:
            print(f"Lightspeed API Error: {e}")
            return {}
    def get_item_by_sku(self, sku: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/Item.json"
        params = {"systemSku": sku}
        response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            items = response.json().get("Item", [])
            if isinstance(items, dict): return [items]
            return items
        return []

    def get_item_shops_full(self, item_id: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/Item/{item_id}.json"
        params = {"load_relations": '["ItemShops"]'}
        response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            item_shops = data.get("Item", {}).get("ItemShops", {}).get("ItemShop", [])
            if isinstance(item_shops, dict): return [item_shops]
            return item_shops
        return []

    def update_reorder_levels(self, item_shop_id: str, reorder_point: int, reorder_level: int) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/ItemShop/{item_shop_id}.json"
        payload = {
            "ItemShop": {
                "reorderPoint": reorder_point,
                "reorderLevel": reorder_level
            }
        }
        try:
            response = requests.put(url, headers=self._get_headers(), json=payload, timeout=10)
            if response.status_code == 401:
                self._refresh_access_token()
                response = requests.put(url, headers=self._get_headers(), json=payload, timeout=10)
            
            if response.status_code == 200:
                return response.json().get("ItemShop")
            return None
        except Exception as e:
            print(f"Lightspeed Update Error: {e}")
            return None

    def sync_recommendation(self, rec: Dict[str, Any]) -> bool:
        system_id = str(rec.get('system_id', ''))
        location = rec.get('location')
        
        print(f"[Lightspeed] Attempting sync for SKU: {rec.get('sku')} (ID: {system_id}) at {location}")
        
        # 1. Get the shopID
        target_shop_id = self.shop_id_map.get(location)
        if not target_shop_id:
            print(f"  [Error] Location {location} not mapped to a shopID")
            return False
            
        # 2. Resolve internal itemID
        # We try searching by the system_id provided (which might be the internal ID or SystemSKU)
        items = []
        
        # Try as internal ID first if it's numeric
        if system_id.isdigit():
            url = f"{self.base_url}/Item/{system_id}.json"
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code == 200:
                items = [resp.json().get("Item")]
        
        # Fallback to systemSku search
        if not items or items[0] is None:
            items = self.get_item_by_sku(system_id)
            
        if not items:
            print(f"  [Error] Item {system_id} not found in Lightspeed API")
            return False
        
        internal_item_id = items[0].get("itemID")
        print(f"  [Info] Resolved internal itemID: {internal_item_id}")

        # 3. Get itemShopIDs
        item_shop_mapping = self.get_item_shops(internal_item_id)
        item_shop_id = item_shop_mapping.get(target_shop_id)
        
        if not item_shop_id:
            print(f"  [Error] No ItemShop entry for shop {target_shop_id}")
            return False
            
        # 4. Push update
        rop = int(rec.get('recommended_reorder_point', 0))
        dl = int(rec.get('recommended_desired_level', 0))
        
        print(f"  [Action] Updating {location} (Shop {target_shop_id}) -> ROP: {rop}, DL: {dl}")
        result = self.update_reorder_levels(item_shop_id, rop, dl)
        
        if result:
            print(f"  [Success] Lightspeed confirmed update for itemShopID {item_shop_id}")
            return True
        else:
            print(f"  [Error] Lightspeed rejected the update request")
            return False
