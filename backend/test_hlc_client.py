"""HlcClient transport behaviour.

Every case here mirrors something observed against the live Canada v3.0 API —
see the module docstring in app/services/hlc/client.py for the raw findings.
"""

import unittest

from app.services.hlc import config
from app.services.hlc.client import HlcClient, HlcError, _MAX_QUERY_CHARS


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise AssertionError("json() must not be called on an empty response")
        return self._payload


class FakeTransport:
    """Stands in for requests.get. `poison` order numbers make the call 500, the
    way HLC does for an order whose package has a blank tracking number."""

    def __init__(self, poison=(), rows_for=None, status=200):
        self.poison = set(poison)
        self.rows_for = rows_for or {}
        self.status = status
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        requested = [n for n in (params.get("orderNumbers") or "").split(",") if n]
        if self.poison & set(requested):
            return FakeResponse(500)
        rows = [row for number in requested for row in self.rows_for.get(number, [])]
        if not rows:
            return FakeResponse(204)
        return FakeResponse(200, rows)


def _row(order_number, box, tracking):
    return {
        "OrderNumber": order_number,
        "BoxNumber": box,
        "TrackingNumber": tracking,
        "Carrier": "Fedex",
        "TrakingUrl": f"https://www.fedex.com/fedextrack/?trknbr={tracking}",
        "PurchaseOrderNumber": "16320",
    }


class TrackingBatchingTest(unittest.TestCase):
    def test_single_poison_order_does_not_lose_the_batch(self):
        """The whole point of the bisect: one permanently-broken order must not
        hide tracking for every other PO in the batch."""
        numbers = [f"LSO360{i:04d}" for i in range(20)]
        rows_for = {n: [_row(n, f"CNT-{i:06d}", f"87500000{i:04d}")] for i, n in enumerate(numbers)}
        transport = FakeTransport(poison={"LSO3600007"}, rows_for=rows_for)

        rows, failed = HlcClient(transport=transport).get_tracking(numbers)

        self.assertEqual(failed, ["LSO3600007"])
        self.assertEqual(len(rows), 19)
        self.assertNotIn("LSO3600007", {r["OrderNumber"] for r in rows})

    def test_multiple_poison_orders_are_isolated_individually(self):
        numbers = [f"LSO360{i:04d}" for i in range(16)]
        rows_for = {n: [_row(n, f"CNT-{i:06d}", f"87500000{i:04d}")] for i, n in enumerate(numbers)}
        transport = FakeTransport(poison={"LSO3600002", "LSO3600011"}, rows_for=rows_for)

        rows, failed = HlcClient(transport=transport).get_tracking(numbers)

        self.assertEqual(sorted(failed), ["LSO3600002", "LSO3600011"])
        self.assertEqual(len(rows), 14)

    def test_batches_stay_under_the_url_length_cap(self):
        """HLC 404s past ~2048 URL chars, so batches are chunked on length."""
        numbers = [f"LSO36{i:05d}" for i in range(300)]
        transport = FakeTransport(rows_for={})

        HlcClient(transport=transport).get_tracking(numbers)

        self.assertGreater(len(transport.calls), 1)
        for call in transport.calls:
            self.assertLessEqual(len(call["params"]["orderNumbers"]), _MAX_QUERY_CHARS)
        # Every order still gets requested exactly once.
        requested = [n for call in transport.calls for n in call["params"]["orderNumbers"].split(",")]
        self.assertEqual(sorted(requested), sorted(numbers))

    def test_204_is_no_shipments_not_an_error(self):
        transport = FakeTransport(rows_for={})
        rows, failed = HlcClient(transport=transport).get_tracking(["LSO3600601"])
        # FakeResponse.json() raises if called, so this also proves the empty
        # body is never parsed.
        self.assertEqual(rows, [])
        self.assertEqual(failed, [])

    def test_duplicate_order_numbers_are_requested_once(self):
        transport = FakeTransport(rows_for={})
        HlcClient(transport=transport).get_tracking(["LSO1", "LSO1", "LSO2", ""])
        self.assertEqual(transport.calls[0]["params"]["orderNumbers"], "LSO1,LSO2")

    def test_transport_failure_on_a_single_order_is_reported_not_raised(self):
        def boom(*args, **kwargs):
            raise ConnectionError("network down")

        rows, failed = HlcClient(transport=boom).get_tracking(["LSO1"])
        self.assertEqual(rows, [])
        self.assertEqual(failed, ["LSO1"])


class AuthAndOrdersTest(unittest.TestCase):
    def test_api_key_uses_hlcs_scheme(self):
        transport = FakeTransport(rows_for={})
        client = HlcClient(transport=transport)
        client.api_key = "secret123"
        client.get_tracking(["LSO1"])
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "ApiKey secret123")
        self.assertEqual(headers["language"], config.LANGUAGE)

    def test_get_orders_requires_a_filter(self):
        with self.assertRaises(HlcError):
            HlcClient(transport=FakeTransport()).get_orders()

    def test_get_orders_raises_rather_than_returning_empty_on_failure(self):
        """An outage must not read as 'this account has no orders' — that would
        silently blank out tracking for every PO."""
        client = HlcClient(transport=lambda *a, **k: FakeResponse(500))
        with self.assertRaises(HlcError):
            client.get_orders(date_from="2026-06-01")

    def test_get_orders_passes_filters_through(self):
        calls = []

        def transport(url, headers=None, params=None, timeout=None):
            calls.append(params)
            return FakeResponse(200, [])

        HlcClient(transport=transport).get_orders(date_from="2026-06-01", po_numbers=["16320", "16321"])
        # Note /Orders spells it "ordersNumbers"; poNumbers is undocumented but real.
        self.assertEqual(calls[0], {"dateFrom": "2026-06-01", "poNumbers": "16320,16321"})


if __name__ == "__main__":
    unittest.main()
