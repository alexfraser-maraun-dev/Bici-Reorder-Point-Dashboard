import requests
import os
import time
from typing import Dict, Any, Optional, List

# OAuth scopes this app requires. Single source of truth shared by the
# re-authorization helper. `employee:inventory` powers the existing ItemShop
# reorder-point writeback; `employee:purchase_orders` powers PO creation.
# NOTE: scope is granted at the authorization step, so re-authorizing for PO
# access MUST also re-request employee:inventory or the writeback path breaks.
LIGHTSPEED_SCOPES = ["employee:inventory", "employee:purchase_orders"]
LIGHTSPEED_AUTHORIZE_URL = "https://cloud.lightspeedapp.com/auth/oauth/authorize"
LIGHTSPEED_TOKEN_URL = "https://cloud.lightspeedapp.com/oauth/access_token.php"

class LightspeedClient:
    def __init__(self):
        self.account_id = os.getenv("LIGHTSPEED_ACCOUNT_ID")
        self.client_id = os.getenv("LIGHTSPEED_CLIENT_ID")
        self.client_secret = os.getenv("LIGHTSPEED_CLIENT_SECRET")
        self.refresh_token = os.getenv("LIGHTSPEED_REFRESH_TOKEN")
        self.bearer_token = os.getenv("LIGHTSPEED_BEARER_TOKEN")
        self.base_url = f"https://api.lightspeedapp.com/API/V3/Account/{self.account_id}"
        
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

    def check_po_access(self) -> bool:
        """
        Verifies the current token can access the purchase-order (Order) endpoint.
        Returns True on a 200 from a minimal Order read; False on 401/403 (e.g.
        the token lacks the employee:purchase_orders scope) or any error.
        """
        response = self._request("GET", "/Order.json", params={"limit": 1})
        if response is None:
            return False
        return response.status_code == 200

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
        # Per API docs, fields are sent flat (not nested inside ItemShop wrapper)
        payload = {
            "reorderPoint": str(reorder_point),
            "reorderLevel": str(reorder_level)
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

    def _request(self, method: str, path: str, params: dict = None, json: dict = None) -> Optional[requests.Response]:
        """
        Issues an authenticated request, transparently refreshing the bearer token
        once on a 401. `path` is appended to base_url (e.g. "/Order.json").
        Returns the Response (caller inspects status), or None on a transport error.
        """
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, headers=self._get_headers(), params=params, json=json, timeout=15)
            if response.status_code == 401:
                self._refresh_access_token()
                response = requests.request(method, url, headers=self._get_headers(), params=params, json=json, timeout=15)
            return response
        except Exception as e:
            print(f"Lightspeed API Error ({method} {path}): {e}")
            return None

    def get_open_orders(self, vendor_id: str = None, shop_id: str = None) -> List[Dict[str, Any]]:
        """
        Fetches open (incomplete, non-archived) purchase orders with their line
        items loaded, optionally filtered by vendor and/or shop. Used to reconcile
        suggested buys against POs that already exist so we don't create duplicates.
        Returns a list of Order dicts, each with a normalized "OrderLine" list.
        """
        params = {
            "complete": "false",
            "archived": "false",
            "load_relations": '["OrderLines"]',
        }
        if vendor_id is not None:
            params["vendorID"] = str(vendor_id)
        if shop_id is not None:
            params["shopID"] = str(shop_id)

        response = self._request("GET", "/Order.json", params=params)
        if response is None or response.status_code != 200:
            if response is not None:
                print(f"Error fetching open orders: {response.text}")
            return []

        orders = response.json().get("Order", [])
        if isinstance(orders, dict):
            orders = [orders]

        # Normalize the nested OrderLines -> OrderLine into a plain list on each order.
        for order in orders:
            lines = order.get("OrderLines", {}).get("OrderLine", [])
            if isinstance(lines, dict):
                lines = [lines]
            order["OrderLine"] = lines
        return orders

    def create_order(self, vendor_id: str, shop_id: str, ordered_date: str = None) -> Optional[Dict[str, Any]]:
        """
        Creates a new (empty) purchase order header. Line items are added separately
        via add_order_line. Returns the created Order dict (incl. orderID) or None.
        """
        payload = {
            "vendorID": str(vendor_id),
            "shopID": str(shop_id),
        }
        if ordered_date:
            payload["orderedDate"] = ordered_date

        response = self._request("POST", "/Order.json", json=payload)
        if response is None or response.status_code not in (200, 201):
            if response is not None:
                print(f"Error creating order: {response.text}")
            return None
        return response.json().get("Order")

    def add_order_line(self, order_id: str, item_id: str, quantity: int, price: float = None) -> Optional[Dict[str, Any]]:
        """
        Adds a line item to an existing purchase order. Returns the created
        OrderLine dict (incl. orderLineID) or None.
        """
        payload = {
            "orderID": str(order_id),
            "itemID": str(item_id),
            "quantity": str(int(quantity)),
        }
        if price is not None:
            payload["price"] = str(price)

        response = self._request("POST", "/OrderLine.json", json=payload)
        if response is None or response.status_code not in (200, 201):
            if response is not None:
                print(f"Error adding order line: {response.text}")
            return None
        return response.json().get("OrderLine")

    def update_order_line(self, order_line_id: str, quantity: int) -> Optional[Dict[str, Any]]:
        """
        Updates the quantity on an existing order line (used to top up a line that
        already exists on an open PO). Returns the updated OrderLine dict or None.
        """
        payload = {"quantity": str(int(quantity))}
        response = self._request("PUT", f"/OrderLine/{order_line_id}.json", json=payload)
        if response is None or response.status_code != 200:
            if response is not None:
                print(f"Error updating order line {order_line_id}: {response.text}")
            return None
        return response.json().get("OrderLine")

    def sync_recommendation(self, rec: Dict[str, Any]) -> bool:
        item_identity = rec.get('lightspeed_item_id') or rec.get('system_id') or rec.get('sku') or ''
        system_id = str(item_identity)
        location = rec.get('location')
        
        print(f"[Lightspeed] Attempting sync for SKU: {rec.get('sku')} (ID: {system_id}) at {location}")
        
        # 1. Get the shopID
        target_shop_id = self.shop_id_map.get(location)
        if not target_shop_id:
            print(f"  [Error] Location {location} not mapped to a shopID")
            return False
            
        # 2. Resolve internal itemID. New payloads send lightspeed_item_id;
        # SKU lookup remains only as a compatibility fallback for older payloads.
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
