"""Mapping HLC tracking onto Lightspeed POs, and the PO tracker's fail-soft merge.

The fixtures reproduce shapes taken from live HLC data: a PO split across two HLC
orders, blank PO numbers, and '#'-prefixed dropship orders.
"""

import unittest
from unittest.mock import patch

from app.services.hlc import service as hlc_service


class FakeHlcClient:
    """Mirrors HlcClient's surface without a network."""

    def __init__(self, orders, tracking_rows=None, failed=None, orders_error=None):
        self.orders = orders
        self.tracking_rows = tracking_rows or []
        self.failed = failed or []
        self.orders_error = orders_error
        self.tracking_requested = None

    def get_orders(self, date_from=None, date_to=None, po_numbers=None, order_numbers=None):
        if self.orders_error:
            raise self.orders_error
        return self.orders

    def get_tracking(self, order_numbers):
        self.tracking_requested = list(order_numbers)
        rows = [r for r in self.tracking_rows if r["OrderNumber"] in set(order_numbers)]
        return rows, self.failed


def _order(order_number, po_number, order_type="Season"):
    return {"OrderNumber": order_number, "PoNumber": po_number, "OrderType": order_type}


def _row(order_number, box, tracking, carrier="Fedex", po_number="16320"):
    return {
        "OrderNumber": order_number,
        "BoxNumber": box,
        "TrackingNumber": tracking,
        "Carrier": carrier,
        "TrakingUrl": f"https://track/{tracking}",
        "PurchaseOrderNumber": po_number,
    }


class TrackingMapTest(unittest.TestCase):
    def setUp(self):
        hlc_service.reset_cache()

    def tearDown(self):
        hlc_service.reset_cache()

    def test_boxes_group_under_the_lightspeed_po(self):
        client = FakeHlcClient(
            orders=[_order("LSO3622161", "16320")],
            tracking_rows=[
                _row("LSO3622161", "CNT-001717195", "875062277623"),
                _row("LSO3622161", "CNT-001717196", "875062286087"),
                _row("LSO3622161", "CNT-001717660", "875104162786"),
            ],
        )
        result = hlc_service._build_tracking_map(client)["data"]

        self.assertEqual(list(result), ["16320"])
        entry = result["16320"]
        self.assertEqual(entry["box_count"], 3)
        self.assertEqual(entry["carrier"], "Fedex")
        self.assertEqual(entry["hlc_order_numbers"], ["LSO3622161"])
        self.assertEqual(entry["boxes"][0]["tracking_url"], "https://track/875062277623")

    def test_one_po_split_across_two_hlc_orders_merges(self):
        """Observed live for POs 15986, 16143 and 16252."""
        client = FakeHlcClient(
            orders=[_order("LSO3602176", "15986"), _order("LSO3602192", "15986")],
            tracking_rows=[
                _row("LSO3602176", "CNT-1", "111", po_number="15986"),
                _row("LSO3602192", "CNT-2", "222", po_number="15986"),
            ],
        )
        entry = hlc_service._build_tracking_map(client)["data"]["15986"]

        self.assertEqual(entry["box_count"], 2)
        self.assertEqual(sorted(entry["hlc_order_numbers"]), ["LSO3602176", "LSO3602192"])

    def test_two_orders_sharing_one_physical_box_count_it_once(self):
        """Live case: PO 15986's orders LSO3602176 and LSO3602192 both report box
        CNT-001699008 because they shipped together. That's one box, from two
        orders — counting it twice would overstate the shipment."""
        client = FakeHlcClient(
            orders=[_order("LSO1", "16320"), _order("LSO2", "16320")],
            tracking_rows=[
                _row("LSO1", "CNT-SAME", "999"),
                _row("LSO2", "CNT-SAME", "999"),
            ],
        )
        entry = hlc_service._build_tracking_map(client)["data"]["16320"]

        self.assertEqual(entry["box_count"], 1)
        # ...but both orders stay on the record.
        self.assertEqual(sorted(entry["hlc_order_numbers"]), ["LSO1", "LSO2"])

    def test_mixed_carriers_are_labelled(self):
        client = FakeHlcClient(
            orders=[_order("LSO1", "16320")],
            tracking_rows=[
                _row("LSO1", "CNT-1", "111", carrier="Fedex"),
                _row("LSO1", "CNT-2", "222", carrier="Nationex"),
            ],
        )
        self.assertEqual(hlc_service._build_tracking_map(client)["data"]["16320"]["carrier"], "Mixed")

    def test_dropship_and_blank_po_orders_are_skipped(self):
        client = FakeHlcClient(
            orders=[
                _order("LSO_DROP", "#238074", order_type="Fulfillment"),
                _order("LSO_BLANK", ""),
                _order("LSO_NONE", None),
                _order("LSO_GOOD", "16320"),
                _order("LSO_BOOK", "16305", order_type="Booking"),
            ],
            tracking_rows=[_row("LSO_GOOD", "CNT-1", "111")],
        )
        result = hlc_service._build_tracking_map(client)

        self.assertEqual(sorted(client.tracking_requested), ["LSO_BOOK", "LSO_GOOD"])
        self.assertEqual(list(result["data"]), ["16320"])
        self.assertEqual(result["meta"]["hlc_orders_scanned"], 5)
        self.assertEqual(result["meta"]["hlc_orders_matched"], 2)

    def test_null_purchase_order_number_falls_back_to_the_order_map(self):
        client = FakeHlcClient(
            orders=[_order("LSO1", "16320")],
            tracking_rows=[_row("LSO1", "CNT-1", "111", po_number=None)],
        )
        self.assertEqual(list(hlc_service._build_tracking_map(client)["data"]), ["16320"])

    def test_rows_without_a_tracking_number_are_dropped(self):
        client = FakeHlcClient(
            orders=[_order("LSO1", "16320")],
            tracking_rows=[_row("LSO1", "CNT-1", "")],
        )
        self.assertEqual(hlc_service._build_tracking_map(client)["data"], {})

    def test_failed_orders_are_counted_in_meta(self):
        client = FakeHlcClient(
            orders=[_order("LSO1", "16320")],
            tracking_rows=[],
            failed=["LSO1"],
        )
        self.assertEqual(hlc_service._build_tracking_map(client)["meta"]["orders_failed"], 1)


class PoWatchMergeTest(unittest.TestCase):
    """The PO tracker must survive HLC being off, broken or slow."""

    def _watchlist(self):
        return {
            "orders": [{
                "order_id": "16320", "expected_date": None, "days_late": None,
                "status": "ordered", "triage": "on_track", "flags": [],
            }],
            "meta": {},
        }

    def _run(self):
        from app.services import po_watch_service
        with patch.object(po_watch_service, "_build_watchlist", return_value=self._watchlist()), \
             patch.object(po_watch_service, "get_planning_store") as store:
            store.return_value.list_po_acks.return_value = {}
            po_watch_service._watch_cache.clear()
            return po_watch_service.get_po_watchlist(ordered_within_days=30, force_refresh=True)

    def test_tracking_key_is_always_present_when_hlc_is_disabled(self):
        with patch("app.services.hlc.config.HLC_ENABLED", False):
            payload = self._run()
        self.assertIsNone(payload["orders"][0]["tracking"])
        self.assertIsNone(payload["meta"]["hlc_tracking"])

    def test_hlc_failure_leaves_the_watchlist_intact(self):
        with patch("app.services.hlc.config.HLC_ENABLED", True), \
             patch("app.services.hlc.service.get_tracking_by_lightspeed_order",
                   side_effect=RuntimeError("HLC down")):
            payload = self._run()

        self.assertEqual(len(payload["orders"]), 1)
        self.assertIsNone(payload["orders"][0]["tracking"])
        self.assertEqual(payload["meta"]["hlc_tracking"], {"error": "unavailable"})

    def test_tracking_is_attached_to_the_matching_po(self):
        tracking = {"16320": {"carrier": "Fedex", "box_count": 3, "boxes": [], "hlc_order_numbers": ["LSO3622161"]}}
        with patch("app.services.hlc.config.HLC_ENABLED", True), \
             patch("app.services.hlc.service.get_tracking_by_lightspeed_order", return_value=tracking), \
             patch("app.services.hlc.service.get_tracking_meta", return_value={"orders_failed": 0}):
            payload = self._run()

        self.assertEqual(payload["orders"][0]["tracking"]["box_count"], 3)
        self.assertEqual(payload["meta"]["hlc_tracking"], {"orders_failed": 0})


if __name__ == "__main__":
    unittest.main()
