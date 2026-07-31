"""HTTP client for the HLC (Cycles Lambert) dealer API, Canada v3.0.

Read-only. Everything here was verified against the live API, and several
behaviours contradict https://developer.hlc.bike/docs/apiref/orders — the notes
below are the observed truth, not the documented one:

  * GET /Orders requires at least one of ordersNumbers/poNumbers/cartIds/dateFrom.
    The docs only mention dateFrom/dateTo; poNumbers works and is undocumented.
  * GET /Orders/Tracking takes `orderNumbers` (plural). The docs' parameter table
    says `cartIds` and the docs' example says `orderNumber`; both are wrong.
    cartIds does work but returns PurchaseOrderNumber: null, which makes the
    result unjoinable, so we always query by order number.
  * An order whose package has a blank tracking number makes the endpoint throw
    a 500 ("The tracking number must be specified to build a tracking url") and
    takes the whole batch down with it. This is a permanent server-side data bug,
    not a transient error, so batches are binary-split to isolate the bad order.
  * The request URL is capped near 2048 chars (IIS): 175 order numbers returned
    200, 190 returned 404. Batches are therefore chunked by URL length.
  * An unknown order number yields 204 with an empty body, not [].
  * The tracking URL field is spelled "TrakingUrl" (sic).
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

from app.services.hlc import config

# Measured ceiling is ~2048 chars; stay well under it so a long base URL or an
# unusually long order number can't push a chunk over the edge.
_MAX_QUERY_CHARS = 1500

# Depth guard for the poison-pill bisect. A chunk of N needs ~log2(N) levels;
# 12 covers 4096 order numbers, far beyond any realistic batch.
_MAX_SPLIT_DEPTH = 12


class HlcError(Exception):
    """Raised when HLC is unreachable or returns an unusable response."""


class HlcClient:
    def __init__(self, transport: Optional[Callable[..., Any]] = None):
        self.base_url = config.BASE_URL
        self.api_key = config.API_KEY
        # Injectable so tests exercise the chunking and bisect logic without a
        # network. Defaults to requests.get, matching the module-level `requests`
        # style used by lightspeed_client.
        self._transport = transport or requests.get

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"ApiKey {self.api_key}",
            "language": config.LANGUAGE,
        }

    def _get(self, path: str, params: Dict[str, str]) -> Optional[requests.Response]:
        """GET with a shared timeout. Returns None on a transport error.

        Only the URL path is logged — never the query string, which carries
        order and purchase-order numbers.
        """
        url = f"{self.base_url}{path}"
        try:
            return self._transport(
                url, headers=self._headers(), params=params, timeout=config.TIMEOUT_SECONDS
            )
        except Exception as e:
            print(f"HLC API error (GET {urlparse(url).path}): {e}")
            return None

    # -- Orders -------------------------------------------------------------

    def get_orders(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        po_numbers: Optional[List[str]] = None,
        order_numbers: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Order history. At least one filter is required by the API.

        Dates are 'yyyy-mm-dd'. Raises HlcError rather than returning a partial
        or empty list on failure, so callers can't mistake an outage for
        "this account has no orders".
        """
        params: Dict[str, str] = {}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        if po_numbers:
            params["poNumbers"] = ",".join(po_numbers)
        if order_numbers:
            # Note the API's spelling: ordersNumbers, not orderNumbers, on /Orders.
            params["ordersNumbers"] = ",".join(order_numbers)
        if not params:
            raise HlcError("get_orders requires at least one filter")

        response = self._get("/Orders", params)
        if response is None:
            raise HlcError("HLC /Orders unreachable")
        if response.status_code == 204:
            return []
        if response.status_code != 200:
            raise HlcError(f"HLC /Orders returned {response.status_code}")
        payload = response.json()
        if not isinstance(payload, list):
            raise HlcError("HLC /Orders returned an unexpected payload")
        return payload

    # -- Tracking -----------------------------------------------------------

    def get_tracking(self, order_numbers: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Tracking rows for the given HLC order numbers.

        Returns (rows, failed_order_numbers). Failures are isolated to the
        individual orders that trigger HLC's 500, so one bad order never hides
        tracking for the rest of the batch.
        """
        unique = list(dict.fromkeys(n for n in order_numbers if n))
        rows: List[Dict[str, Any]] = []
        failed: List[str] = []
        for chunk in self._chunk_by_url_length(unique):
            chunk_rows, chunk_failed = self._fetch_tracking(chunk, depth=0)
            rows.extend(chunk_rows)
            failed.extend(chunk_failed)
        return rows, failed

    @staticmethod
    def _chunk_by_url_length(order_numbers: List[str]) -> List[List[str]]:
        """Split into batches whose encoded `orderNumbers` value stays under the
        URL cap. Chunking on length rather than a fixed count keeps the batches
        as large as the server actually allows."""
        chunks: List[List[str]] = []
        current: List[str] = []
        length = 0
        for number in order_numbers:
            encoded = len(quote(number, safe="")) + 1  # +1 for the comma
            if current and length + encoded > _MAX_QUERY_CHARS:
                chunks.append(current)
                current = []
                length = 0
            current.append(number)
            length += encoded
        if current:
            chunks.append(current)
        return chunks

    def _fetch_tracking(self, order_numbers: List[str], depth: int) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Fetch one batch, bisecting around any order that makes HLC throw."""
        if not order_numbers:
            return [], []

        response = self._get("/Orders/Tracking", {"orderNumbers": ",".join(order_numbers)})

        if response is not None and response.status_code == 200:
            payload = response.json()
            if isinstance(payload, list):
                return payload, []
            print("HLC tracking: unexpected payload shape, skipping batch")
            return [], list(order_numbers)

        # 204 = no shipments yet for these orders. A normal, expected state.
        if response is not None and response.status_code == 204:
            return [], []

        # A single order that still fails is the poison pill itself — record it
        # and move on rather than losing the whole batch.
        if len(order_numbers) == 1:
            status = response.status_code if response is not None else "transport error"
            print(f"HLC tracking: order {order_numbers[0]} failed ({status}), skipping")
            return [], list(order_numbers)

        if depth >= _MAX_SPLIT_DEPTH:
            print(f"HLC tracking: split depth exceeded, skipping {len(order_numbers)} orders")
            return [], list(order_numbers)

        mid = len(order_numbers) // 2
        left_rows, left_failed = self._fetch_tracking(order_numbers[:mid], depth + 1)
        right_rows, right_failed = self._fetch_tracking(order_numbers[mid:], depth + 1)
        return left_rows + right_rows, left_failed + right_failed
