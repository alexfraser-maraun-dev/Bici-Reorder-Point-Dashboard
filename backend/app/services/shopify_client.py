"""
Shopify Admin API client (GraphQL).

The customer-promised special-order ETA lives on a Shopify *order* metafield
(`custom.special_order_eta`, type `date`). Historically the dashboard read that value
indirectly via Fivetran -> BigQuery (a nightly copy), so edits made in Shopify took up to a
day to surface and there was no way to change an ETA from inside the tool.

This client talks to the Shopify Admin API directly so the read and write paths share one
source of truth: `get_open_special_orders()` is a drop-in replacement for the BigQuery pull
(`bigquery_sync.get_shopify_special_orders()`) returning the identical row shape, and
`set_order_eta()` writes the metafield back via `metafieldsSet`. An edit is therefore visible
on the very next refresh with no Fivetran lag.

Auth supports both Shopify app models:
  * Dev Dashboard app (current) — the client-credentials grant: POST the app's Client ID +
    Secret (`SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET`) to `/admin/oauth/access_token` for a
    short-lived (~24h) Admin API token, cached and refreshed on expiry/401 (like the Lightspeed
    client). Requires the app and store to be in the same Shopify organization.
  * Admin-created custom app (legacy) — a static `SHOPIFY_ADMIN_API_TOKEN` (shpat_/shpca_),
    used directly if set.
"""

import os
import re
import time
import threading
import requests
from typing import Any, Dict, List, Optional

# The metafield that holds the customer-promised ETA, mirrored from the BigQuery query in
# bigquery_sync.get_shopify_special_orders().
_ETA_NAMESPACE = "custom"
_ETA_KEY = "special_order_eta"

# Financial states that mean the order is no longer a live, financially-sound special order.
# Mirrors the exclusions in the BigQuery query so the live read returns the same population.
_EXCLUDED_FINANCIAL = {"REFUNDED", "PARTIALLY_REFUNDED", "VOIDED"}

# How many orders / line items to pull per page. Orders tagged `SO` are a small population, so
# a single page is usually enough; pagination is handled anyway for safety.
_ORDERS_PER_PAGE = 100
_LINE_ITEMS_PER_PAGE = 50
# Hard stop so a runaway cursor loop can never hammer the API.
_MAX_PAGES = 50

_GID_NUM = re.compile(r"/(\d+)$")


def _gid_to_id(gid: Optional[str]) -> Optional[str]:
    """`gid://shopify/Order/12345` -> `"12345"` (matches the numeric id the rest of the app
    and the Shopify admin deep-links use)."""
    if not gid:
        return None
    m = _GID_NUM.search(gid)
    return m.group(1) if m else None


# Refresh the client-credentials token this many seconds before its stated expiry, so an
# in-flight request never races the expiry boundary.
_TOKEN_SKEW_SECONDS = 120


class ShopifyClient:
    def __init__(self):
        # Accept either the bare handle or a full *.myshopify.com domain.
        domain = (os.getenv("SHOPIFY_SHOP_DOMAIN") or "").strip()
        if domain and not domain.endswith("myshopify.com"):
            domain = f"{domain}.myshopify.com"
        self.shop_domain = domain
        self.api_version = os.getenv("SHOPIFY_API_VERSION", "2026-04")
        self.endpoint = (
            f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"
            if self.shop_domain
            else None
        )

        # Auth: a static Admin API token wins if present; otherwise client-credentials.
        self.static_token = (os.getenv("SHOPIFY_ADMIN_API_TOKEN") or "").strip()
        self.client_id = (os.getenv("SHOPIFY_API_KEY") or "").strip()
        self.client_secret = (os.getenv("SHOPIFY_API_SECRET") or "").strip()
        self._token: Optional[str] = self.static_token or None
        # A static token never expires; a fetched one is refreshed before _token_expiry.
        self._token_expiry: float = float("inf") if self.static_token else 0.0
        # Serializes concurrent token fetches (parallel SO refreshes) into one exchange.
        self._token_lock = threading.Lock()

    def _configured(self) -> bool:
        if not self.endpoint:
            return False
        return bool(self.static_token or (self.client_id and self.client_secret))

    def _fetch_token(self) -> str:
        """Exchanges Client ID + Secret for a short-lived Admin API token via the
        client-credentials grant. Serialized so parallel callers share one exchange."""
        with self._token_lock:
            # Another thread may have refreshed while we waited for the lock.
            if self._token and time.time() < self._token_expiry - _TOKEN_SKEW_SECONDS:
                return self._token
            url = f"https://{self.shop_domain}/admin/oauth/access_token"
            resp = requests.post(
                url,
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Shopify token exchange failed (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            body = resp.json()
            token = body.get("access_token")
            if not token:
                raise RuntimeError(f"Shopify token exchange returned no access_token: {body}")
            self._token = token
            self._token_expiry = time.time() + float(body.get("expires_in", 86399))
            return token

    def _access_token(self) -> str:
        if self.static_token:
            return self.static_token
        if self._token and time.time() < self._token_expiry - _TOKEN_SKEW_SECONDS:
            return self._token
        return self._fetch_token()

    def _graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Issues a GraphQL request and returns the `data` payload. Transparently refreshes a
        client-credentials token once on a 401. Raises on transport, HTTP, or GraphQL-level
        (`errors`) failure so write callers can surface the problem."""
        if not self._configured():
            raise RuntimeError(
                "Shopify is not configured (set SHOPIFY_SHOP_DOMAIN plus either "
                "SHOPIFY_ADMIN_API_TOKEN or SHOPIFY_API_KEY/SHOPIFY_API_SECRET)."
            )

        def _post() -> requests.Response:
            return requests.post(
                self.endpoint,
                headers={
                    "X-Shopify-Access-Token": self._access_token(),
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
                timeout=30,
            )

        resp = _post()
        if resp.status_code == 401 and not self.static_token:
            # Token expired or revoked — force a refresh and retry once.
            self._token = None
            self._token_expiry = 0.0
            resp = _post()
        if resp.status_code != 200:
            raise RuntimeError(f"Shopify HTTP {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {body['errors']}")
        return body.get("data") or {}

    # ------------------------------------------------------------------ read

    _OPEN_SO_QUERY = """
    query OpenSpecialOrders($cursor: String, $lineItems: Int!) {
      orders(first: %d, after: $cursor, query: "tag:SO", sortKey: CREATED_AT, reverse: true) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          name
          email
          displayFulfillmentStatus
          displayFinancialStatus
          createdAt
          cancelledAt
          closed
          test
          metafield(namespace: "%s", key: "%s") { value }
          lineItems(first: $lineItems) { nodes { sku } }
        }
      }
    }
    """ % (_ORDERS_PER_PAGE, _ETA_NAMESPACE, _ETA_KEY)

    def get_open_special_orders(self) -> List[Dict[str, Any]]:
        """
        Live equivalent of `bigquery_sync.get_shopify_special_orders()`: open Shopify orders
        tagged `SO` (not fulfilled, not refunded/voided/cancelled/closed/test), one row per
        (order x line SKU), with the `custom.special_order_eta` metafield as `eta`.

        Returns the identical row shape so downstream matching/flagging is unchanged:
            {order_id, order_name, email, fulfillment_status, financial_status,
             created_at, eta, sku}

        Returns [] on any failure (missing config, transport, GraphQL errors) so the
        Lightspeed-based special-order triage never breaks when Shopify is unavailable —
        preserving the resilience guarantee the BigQuery pull had.
        """
        if not self._configured():
            print("Shopify not configured; skipping live special-order pull.")
            return []
        try:
            rows: List[Dict[str, Any]] = []
            cursor: Optional[str] = None
            for _ in range(_MAX_PAGES):
                data = self._graphql(
                    self._OPEN_SO_QUERY, {"cursor": cursor, "lineItems": _LINE_ITEMS_PER_PAGE}
                )
                conn = data.get("orders") or {}
                for o in conn.get("nodes") or []:
                    # Mirror the BigQuery exclusions (the `tag:SO` search can't express them all).
                    if o.get("displayFulfillmentStatus") == "FULFILLED":
                        continue
                    if (o.get("displayFinancialStatus") or "") in _EXCLUDED_FINANCIAL:
                        continue
                    if o.get("cancelledAt") or o.get("closed") or o.get("test"):
                        continue

                    order_id = _gid_to_id(o.get("id"))
                    order_name = o.get("name")
                    email = (o.get("email") or "").strip().lower() or None
                    eta = (o.get("metafield") or {}).get("value")
                    base = {
                        "order_id": order_id,
                        "order_name": order_name,
                        "email": email,
                        "fulfillment_status": o.get("displayFulfillmentStatus"),
                        "financial_status": o.get("displayFinancialStatus"),
                        "created_at": o.get("createdAt"),
                        "eta": eta,
                    }
                    line_skus = [
                        li.get("sku") for li in ((o.get("lineItems") or {}).get("nodes") or [])
                    ]
                    # One row per line item, mirroring the order_line join (a SKU may be None).
                    if line_skus:
                        for sku in line_skus:
                            rows.append({**base, "sku": sku})
                    else:
                        rows.append({**base, "sku": None})

                page = conn.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                cursor = page.get("endCursor")
            return rows
        except Exception as e:
            print(f"Failed to fetch Shopify special orders: {e}")
            return []

    # ----------------------------------------------------------------- write

    _SET_ETA_MUTATION = """
    mutation SetOrderEta($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key value }
        userErrors { field message }
      }
    }
    """

    def set_order_eta(self, order_id: str, eta: str) -> Dict[str, Any]:
        """
        Sets (creates or updates) the `custom.special_order_eta` date metafield on a Shopify
        order. `order_id` is the numeric Shopify order id; `eta` is an ISO date `YYYY-MM-DD`.

        Returns the set metafield dict on success; raises RuntimeError on GraphQL/user errors.
        """
        if not str(order_id or "").strip():
            raise ValueError("order_id is required")
        variables = {
            "metafields": [
                {
                    "ownerId": f"gid://shopify/Order/{order_id}",
                    "namespace": _ETA_NAMESPACE,
                    "key": _ETA_KEY,
                    "type": "date",
                    "value": eta,
                }
            ]
        }
        data = self._graphql(self._SET_ETA_MUTATION, variables)
        result = data.get("metafieldsSet") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise RuntimeError(f"Shopify rejected the ETA update: {user_errors}")
        metafields = result.get("metafields") or []
        return metafields[0] if metafields else {}
