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

# NOTE: financial status is deliberately NOT used to exclude orders. The money-on-account
# refund in the special-order flow happens in LIGHTSPEED, not Shopify, so a Shopify refund never
# means "this special order was paid out". What a refund on an unfulfilled order actually means is
# the placeholder repair: CS could not find an existing Lightspeed item, so they later refund the
# stand-in line and swap in the real LS SKU. Filtering on REFUNDED/PARTIALLY_REFUNDED therefore hid
# precisely the orders that had just been repaired -- ~14% of the live population -- from
# procurement. An order holds an active special order when it is neither fulfilled nor archived.

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

    def check_health(self) -> bool:
        """
        Reports whether Shopify is configured and reachable with valid credentials.
        Mirrors LightspeedClient.check_health: a trivial GraphQL ping that returns False
        (rather than raising) when unconfigured or on any error.
        """
        if not self._configured():
            return False
        try:
            self._graphql("query { shop { name } }")
            return True
        except Exception as e:
            print(f"Shopify health check failed: {e}")
            return False

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

    # phone + shippingAddress give the matcher extra identity signals (both are part of the
    # orders scope, unlike `customer { … }` which would need read_customers and could fail the
    # whole query on a scope gap).
    _OPEN_SO_QUERY = """
    query OpenSpecialOrders($cursor: String, $lineItems: Int!) {
      orders(first: %d, after: $cursor, query: "tag:SO", sortKey: CREATED_AT, reverse: true) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          name
          email
          phone
          shippingAddress { name phone }
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

        Returns the identical row shape so downstream matching/flagging is unchanged
        (plus the phone / customer-name identity signals the tiered matcher uses):
            {order_id, order_name, email, phone, customer_name, fulfillment_status,
             financial_status, created_at, eta, sku}

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
                    # Fulfilled or archived => the special order is done. See the note on
                    # financial status above for why a refund is NOT an exclusion.
                    if o.get("displayFulfillmentStatus") == "FULFILLED":
                        continue
                    if o.get("cancelledAt") or o.get("closed") or o.get("test"):
                        continue

                    order_id = _gid_to_id(o.get("id"))
                    order_name = o.get("name")
                    email = (o.get("email") or "").strip().lower() or None
                    eta = (o.get("metafield") or {}).get("value")
                    ship = o.get("shippingAddress") or {}
                    base = {
                        "order_id": order_id,
                        "order_name": order_name,
                        "email": email,
                        "phone": o.get("phone") or ship.get("phone"),
                        "customer_name": ship.get("name"),
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

    # ------------------------------------------------------- arbitrary lookup

    # Everything the manual-link UI needs to show the user *which* order they are about to
    # link: identity, state, and the full line-item list they confirm against. Deliberately
    # NOT restricted to `tag:SO` or to open orders — the whole point of this path is to reach
    # an order the automatic population never considered.
    _ORDER_DETAIL_FIELDS = """
      id
      name
      email
      phone
      createdAt
      cancelledAt
      closed
      test
      tags
      displayFulfillmentStatus
      displayFinancialStatus
      shippingAddress { name phone }
      metafield(namespace: "%s", key: "%s") { value }
      lineItems(first: %d) { nodes { sku title variantTitle quantity } }
    """ % (_ETA_NAMESPACE, _ETA_KEY, _LINE_ITEMS_PER_PAGE)

    _SEARCH_ORDERS_QUERY = """
    query SearchOrders($q: String!, $first: Int!) {
      orders(first: $first, query: $q, sortKey: CREATED_AT, reverse: true) {
        nodes { %s }
      }
    }
    """ % _ORDER_DETAIL_FIELDS

    _ORDERS_BY_ID_QUERY = """
    query OrdersByIds($ids: [ID!]!) {
      nodes(ids: $ids) { ... on Order { %s } }
    }
    """ % _ORDER_DETAIL_FIELDS

    @staticmethod
    def _order_detail(node: Dict[str, Any]) -> Dict[str, Any]:
        """One GraphQL order node -> the flat detail record the manual-link UI renders."""
        ship = node.get("shippingAddress") or {}
        return {
            "order_id": _gid_to_id(node.get("id")),
            "order_name": node.get("name"),
            "customer_email": (node.get("email") or "").strip().lower() or None,
            "customer_phone": node.get("phone") or ship.get("phone"),
            "customer_name": ship.get("name"),
            "created_at": node.get("createdAt"),
            "shopify_expected_date": (node.get("metafield") or {}).get("value"),
            "fulfillment_status": node.get("displayFulfillmentStatus"),
            "financial_status": node.get("displayFinancialStatus"),
            "cancelled": bool(node.get("cancelledAt")),
            "closed": bool(node.get("closed")),
            "test": bool(node.get("test")),
            "tags": node.get("tags") or [],
            "line_items": [
                {
                    "sku": li.get("sku"),
                    "title": li.get("title"),
                    "variant_title": li.get("variantTitle"),
                    "quantity": li.get("quantity"),
                }
                for li in ((node.get("lineItems") or {}).get("nodes") or [])
            ],
        }

    def search_orders(self, term: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Finds orders by number (`244786`, `#244786`) or by any other Shopify order-search
        term (email, customer name), newest first — regardless of tag, fulfillment state or
        age. This backs the "link this SO to *any* Shopify order" flow, so it must reach
        orders the `tag:SO` dashboard population deliberately excludes.

        Returns [] (never raises) when Shopify is unconfigured or the search fails; the
        caller's local candidate list is then all the user sees.
        """
        term = (term or "").strip()
        if not term or not self._configured():
            return []
        # A bare/`#`-prefixed number is nearly always an order number — search that field
        # explicitly so `#244786` doesn't also drag in every order mentioning the digits.
        digits = term.lstrip("#").strip()
        query = f"name:{digits}" if digits.isdigit() else term
        try:
            data = self._graphql(self._SEARCH_ORDERS_QUERY, {"q": query, "first": max(1, min(limit, 25))})
        except Exception as e:
            print(f"Shopify order search failed for {term!r}: {e}")
            return []
        return [self._order_detail(n) for n in ((data.get("orders") or {}).get("nodes") or [])]

    def get_orders_by_ids(self, order_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches specific orders by numeric id, in the same per-(order x line SKU) row shape
        as `get_open_special_orders()` so `shopify_match.build_shopify_index` can absorb
        them. Used to resurrect manually-linked orders that fall outside the open `SO`-tagged
        population (a fulfilled or untagged order a human deliberately linked) — none of the
        open-population exclusions apply here, because the link is an explicit human decision.

        Returns [] on any failure, which simply lets those links lapse to auto-matching.
        """
        ids = sorted({str(o).strip() for o in order_ids if str(o or "").strip()})
        if not ids or not self._configured():
            return []
        try:
            data = self._graphql(
                self._ORDERS_BY_ID_QUERY,
                {"ids": [f"gid://shopify/Order/{oid}" for oid in ids]},
            )
        except Exception as e:
            print(f"Failed to fetch Shopify orders by id: {e}")
            return []
        rows: List[Dict[str, Any]] = []
        for node in data.get("nodes") or []:
            if not node:  # a deleted / inaccessible id comes back as null
                continue
            detail = self._order_detail(node)
            base = {
                "order_id": detail["order_id"],
                "order_name": detail["order_name"],
                "email": detail["customer_email"],
                "phone": detail["customer_phone"],
                "customer_name": detail["customer_name"],
                "fulfillment_status": detail["fulfillment_status"],
                "financial_status": detail["financial_status"],
                "created_at": detail["created_at"],
                "eta": detail["shopify_expected_date"],
            }
            skus = [li["sku"] for li in detail["line_items"]] or [None]
            rows.extend({**base, "sku": sku} for sku in skus)
        return rows

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

    _DELETE_ETA_MUTATION = """
    mutation DeleteOrderEta($metafields: [MetafieldIdentifierInput!]!) {
      metafieldsDelete(metafields: $metafields) {
        deletedMetafields { ownerId key }
        userErrors { field message }
      }
    }
    """

    def delete_order_eta(self, order_id: str) -> None:
        """
        Removes the `custom.special_order_eta` metafield from a Shopify order (the "clear ETA"
        path). Deleting an already-absent metafield is a no-op on Shopify's side, so this is
        idempotent. Raises RuntimeError on GraphQL/user errors.
        """
        if not str(order_id or "").strip():
            raise ValueError("order_id is required")
        variables = {
            "metafields": [
                {
                    "ownerId": f"gid://shopify/Order/{order_id}",
                    "namespace": _ETA_NAMESPACE,
                    "key": _ETA_KEY,
                }
            ]
        }
        data = self._graphql(self._DELETE_ETA_MUTATION, variables)
        result = data.get("metafieldsDelete") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise RuntimeError(f"Shopify rejected the ETA delete: {user_errors}")
