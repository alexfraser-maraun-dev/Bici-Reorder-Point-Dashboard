import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app import main
from app.services.lightspeed_client import LightspeedReadError
from app.services.lightspeed_gateway import FakeLightspeedGateway
from app.services.po_snapshot_service import PurchaseOrderSnapshotCache


def _order(order_id, vendor_id, shop_id, ordered=False):
    return {
        "orderID": str(order_id),
        "vendorID": str(vendor_id),
        "shopID": str(shop_id),
        "complete": "false",
        "archived": "false",
        "orderedDate": "2026-07-01" if ordered else None,
        "OrderLine": [],
    }


class _CountingGateway:
    def __init__(self, orders):
        self.gateway = FakeLightspeedGateway(orders)
        self.calls = 0
        self.fail = False
        self.include_lines_values = []

    def list_purchase_orders(self, include_lines=True):
        self.calls += 1
        self.include_lines_values.append(include_lines)
        if self.fail:
            raise LightspeedReadError("fixture page failed")
        return self.gateway.list_purchase_orders()


class PurchaseOrderSnapshotCacheTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.gateway = _CountingGateway([
            _order("16192", "807", "3"),
            _order("200", "807", "2", ordered=True),
            _order("300", "401", "3"),
        ])
        self.cache = PurchaseOrderSnapshotCache(
            gateway_factory=lambda: self.gateway,
            ttl_seconds=15,
            clock=lambda: self.now[0],
        )

    def test_one_complete_walk_is_filtered_for_multiple_vendor_shop_requests(self):
        adanac = self.cache.get_orders(vendor_id="807", shop_id="3")
        victoria = self.cache.get_orders(vendor_id="807", shop_id="2")
        self.assertEqual(self.gateway.calls, 1)
        self.assertEqual([row["orderID"] for row in adanac["orders"]], ["16192"])
        self.assertEqual([row["orderID"] for row in victoria["orders"]], ["200"])
        self.assertFalse(adanac["meta"]["cache_hit"])
        self.assertTrue(victoria["meta"]["cache_hit"])
        self.assertEqual(victoria["meta"]["total_order_count"], 3)
        self.assertEqual(self.gateway.include_lines_values, [False])
        self.assertFalse(victoria["meta"]["includes_lines"])

    def test_expiry_and_manual_refresh_each_perform_one_new_complete_walk(self):
        self.cache.get_orders()
        self.now[0] += 16
        expired = self.cache.get_orders()
        forced = self.cache.get_orders(force_refresh=True)
        self.assertEqual(self.gateway.calls, 3)
        self.assertFalse(expired["meta"]["cache_hit"])
        self.assertFalse(forced["meta"]["cache_hit"])

    def test_expired_refresh_failure_does_not_return_stale_orders(self):
        self.cache.get_orders()
        self.now[0] += 16
        self.gateway.fail = True
        with self.assertRaises(LightspeedReadError):
            self.cache.get_orders(vendor_id="807")
        self.assertEqual(self.gateway.calls, 2)

    def test_callers_cannot_mutate_the_shared_snapshot(self):
        first = self.cache.get_orders(vendor_id="807", shop_id="3")
        first["orders"][0]["vendorID"] = "changed"
        second = self.cache.get_orders(vendor_id="807", shop_id="3")
        self.assertEqual(second["orders"][0]["vendorID"], "807")

    def test_peek_never_waits_on_an_in_flight_walk(self):
        """peek_orders is documented never to trigger a load; it must not block on one
        either. A complete walk takes ~40s, and holding the state mutex across it made
        every peek pay that cost second-hand."""
        walking, release = threading.Event(), threading.Event()

        class SlowGateway:
            def list_purchase_orders(self, include_lines=True):
                walking.set()
                release.wait(5)
                return [_order("16192", "807", "3")]

        cache = PurchaseOrderSnapshotCache(
            gateway_factory=lambda: SlowGateway(), ttl_seconds=15
        )
        walker = threading.Thread(target=cache.get_orders, daemon=True)
        walker.start()
        self.assertTrue(walking.wait(5), "gateway walk never started")

        started = time.monotonic()
        self.assertIsNone(cache.peek_orders())
        elapsed = time.monotonic() - started
        release.set()
        walker.join(5)

        self.assertLess(elapsed, 0.5, f"peek_orders blocked for {elapsed:.2f}s on the walk")
        self.assertEqual([o["orderID"] for o in cache.peek_orders()], ["16192"])


class PurchaseOrderSnapshotApiTest(unittest.TestCase):
    def test_endpoint_returns_snapshot_metadata_and_filtered_orders(self):
        cache = Mock()
        cache.get_orders.return_value = {
            "orders": [_order("16192", "807", "3")],
            "meta": {"snapshot_at": "2026-07-15T16:00:00+00:00", "complete": True},
        }
        with patch("app.services.po_snapshot_service.get_po_snapshot_cache", return_value=cache):
            response = main.list_open_orders("807", "3", refresh=True)
        self.assertEqual(response["data"][0]["orderID"], "16192")
        self.assertTrue(response["meta"]["complete"])
        cache.get_orders.assert_called_once_with(
            vendor_id="807", shop_id="3", force_refresh=True
        )

    def test_endpoint_returns_503_when_complete_snapshot_cannot_refresh(self):
        cache = Mock()
        cache.get_orders.side_effect = LightspeedReadError("page 2 failed")
        with patch("app.services.po_snapshot_service.get_po_snapshot_cache", return_value=cache):
            with self.assertRaises(HTTPException) as raised:
                main.list_open_orders("807", "3")
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
