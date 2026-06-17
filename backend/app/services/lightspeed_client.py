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


def _to_number(value, default: float = 0.0) -> float:
    """Coerce a Lightspeed string/number field (e.g. "0", "2") to a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

class LightspeedClient:
    def __init__(self):
        self.account_id = os.getenv("LIGHTSPEED_ACCOUNT_ID")
        self.client_id = os.getenv("LIGHTSPEED_CLIENT_ID")
        self.client_secret = os.getenv("LIGHTSPEED_CLIENT_SECRET")
        self.refresh_token = os.getenv("LIGHTSPEED_REFRESH_TOKEN")
        self.bearer_token = os.getenv("LIGHTSPEED_BEARER_TOKEN")
        self.base_url = f"https://api.lightspeedapp.com/API/V3/Account/{self.account_id}"
        # Legacy (non-V3) API base. SpecialOrder, its relation chaining, and the
        # Order.arrivalDate ("expected date") used by the special-order dashboard are
        # legacy-API features not exposed by V3, so they are fetched from here while
        # reusing the same OAuth bearer token / refresh machinery as the V3 client.
        self.legacy_base_url = f"https://api.lightspeedapp.com/API/Account/{self.account_id}"

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

    # ------------------------------------------------------------------
    # Legacy-API access for the Special Order dashboard
    # ------------------------------------------------------------------
    @staticmethod
    def _as_list(value) -> List[Dict[str, Any]]:
        """
        Normalize a Lightspeed relation/collection field to a plain list. The API
        returns a single object when count == 1, a list when count > 1, and omits the
        key entirely (only an `@attributes` wrapper) when count == 0.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _legacy_request(self, method: str, path: str, params: dict = None, max_retries: int = 3) -> Optional[requests.Response]:
        """
        Authenticated request against the legacy (non-V3) Lightspeed API. Mirrors
        `_request` (401 -> refresh -> retry once) but targets `legacy_base_url` and
        additionally honors 429 rate-limit back-off via the `retry-after` header.
        Returns the Response (caller inspects status) or None on a transport error.
        """
        url = f"{self.legacy_base_url}{path}"
        for attempt in range(max_retries):
            try:
                headers = self._get_headers()
                headers["Accept"] = "application/json"
                response = requests.request(method, url, headers=headers, params=params, timeout=20)
                if response.status_code == 401:
                    self._refresh_access_token()
                    headers = self._get_headers()
                    headers["Accept"] = "application/json"
                    response = requests.request(method, url, headers=headers, params=params, timeout=20)
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after", "")
                    delay = int(retry_after) if retry_after.isdigit() else 2
                    time.sleep(delay)
                    continue
                return response
            except Exception as e:
                print(f"Lightspeed legacy API Error ({method} {path}): {e}")
                if attempt == max_retries - 1:
                    return None
                time.sleep(1)
        return None

    def get_special_orders(self, page_limit: int = 100, max_pages: int = 50) -> List[Dict[str, Any]]:
        """
        Pages through open (incomplete) special orders, loading the relations needed to
        resolve item and the attached purchase order. Returns raw SpecialOrder dicts, each
        potentially carrying SaleLine / SaleLine.Item / OrderLine. (Customer is NOT an
        allowed relation here, so customer names are resolved separately via
        get_customers_by_ids.)
        """
        relations = '["SaleLine","SaleLine.Item","OrderLine"]'
        results: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            params = {
                "completed": "false",
                "load_relations": relations,
                "limit": str(page_limit),
                "offset": str(offset),
            }
            response = self._legacy_request("GET", "/SpecialOrder.json", params=params)
            if response is None or response.status_code != 200:
                if response is not None:
                    print(f"Error fetching special orders: {response.status_code} {response.text[:300]}")
                break
            page = self._as_list(response.json().get("SpecialOrder"))
            results.extend(page)
            if len(page) < page_limit:
                break
            offset += page_limit
        return results

    def get_orders_by_ids(self, order_ids: List[str], chunk_size: int = 40) -> Dict[str, Dict[str, Any]]:
        """
        Fetches the purchase orders (Order entity) behind a set of special orders and
        returns, keyed by orderID, just the fields the overdue logic needs:
          { orderID: { "arrivalDate", "complete", "vendor_id", "vendor_name",
                       "received_started" } }
        `arrivalDate` is the PO's expected date; `received_started` is True if any line
        shows receiving progress (numReceived / checkedIn > 0).
        """
        unique_ids = sorted({str(o) for o in order_ids if o and str(o) != "0"})
        order_map: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            params = {
                "orderID": f"IN,[{','.join(chunk)}]",
                "load_relations": '["Vendor","OrderLines"]',
                "limit": "100",
            }
            response = self._legacy_request("GET", "/Order.json", params=params)
            if response is None or response.status_code != 200:
                if response is not None:
                    print(f"Error fetching orders: {response.status_code} {response.text[:300]}")
                continue
            for order in self._as_list(response.json().get("Order")):
                order_id = str(order.get("orderID"))
                order_lines_wrap = order.get("OrderLines")
                lines = self._as_list(order_lines_wrap.get("OrderLine")) if isinstance(order_lines_wrap, dict) else []
                received_started = any(
                    _to_number(line.get("numReceived")) > 0 or _to_number(line.get("checkedIn")) > 0
                    for line in lines
                )
                vendor = order.get("Vendor") or {}
                order_map[order_id] = {
                    "arrivalDate": order.get("arrivalDate") or None,
                    "complete": str(order.get("complete")).lower() == "true",
                    "vendor_id": order.get("vendorID"),
                    "vendor_name": vendor.get("name"),
                    "received_started": received_started,
                }
        return order_map

    def get_customers_by_ids(self, customer_ids: List[str], chunk_size: int = 40) -> Dict[str, Dict[str, Any]]:
        """
        Resolves customer names/contact for a set of special orders, keyed by customerID:
          { customerID: { "first_name", "last_name", "company", "phone" } }
        Customer is not loadable as a relation on SpecialOrder, so it's fetched here.
        """
        unique_ids = sorted({str(c) for c in customer_ids if c and str(c) != "0"})
        customer_map: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            params = {
                "customerID": f"IN,[{','.join(chunk)}]",
                "load_relations": '["Contact"]',
                "limit": "100",
            }
            response = self._legacy_request("GET", "/Customer.json", params=params)
            if response is None or response.status_code != 200:
                if response is not None:
                    print(f"Error fetching customers: {response.status_code} {response.text[:300]}")
                continue
            for cust in self._as_list(response.json().get("Customer")):
                cust_id = str(cust.get("customerID"))
                contact = cust.get("Contact") or {}
                phones = contact.get("Phones", {}).get("ContactPhone") if isinstance(contact.get("Phones"), dict) else None
                phone = None
                phone_list = self._as_list(phones)
                if phone_list:
                    phone = phone_list[0].get("number")
                customer_map[cust_id] = {
                    "first_name": cust.get("firstName"),
                    "last_name": cust.get("lastName"),
                    "company": cust.get("company"),
                    "phone": phone,
                }
        return customer_map
