"""Offline trust-boundary tests for strict Shopify reads."""

import unittest

from app.services.shopify_client import ShopifyClient
from app.services.lightspeed_client import LightspeedClient, LightspeedReadError
from app.services.special_order_service import _source_statuses


def _client(configured=True):
    client = ShopifyClient()
    client._configured = lambda: configured
    return client


class ShopifyClientStrictReadTest(unittest.TestCase):
    def test_strict_interactive_search_distinguishes_unavailable_from_no_results(self):
        client = _client(configured=False)
        self.assertEqual(client.search_orders("#123"), [])
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            client.search_orders("#123", strict=True)

    def test_strict_dashboard_pull_raises_while_fail_soft_mode_remains_available(self):
        client = _client(configured=True)

        def explode(*_args, **_kwargs):
            raise RuntimeError("transport down")

        client._graphql = explode
        self.assertEqual(client.get_open_special_orders(), [])
        with self.assertRaisesRegex(RuntimeError, "fetch failed"):
            client.get_open_special_orders(strict=True)
        self.assertEqual(client.get_recent_special_orders(), [])
        with self.assertRaises(RuntimeError):
            client.get_recent_special_orders(strict=True)

    def test_strict_search_raises_on_graphql_failure(self):
        client = _client(configured=True)
        client._graphql = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down"))
        with self.assertRaisesRegex(RuntimeError, "search failed"):
            client.search_orders("customer@example.com", strict=True)

    def test_source_health_distinguishes_partial_from_unavailable(self):
        health = {
            "shopify_open_special_orders": {"status": "ok", "checked_at": "2026-08-20T01:00:00Z", "record_count": 4},
            "shopify_recent_fallback": {"status": "unavailable", "checked_at": "2026-08-20T01:00:01Z", "record_count": 0},
        }
        self.assertEqual(_source_statuses(health)["shopify"]["status"], "stale")
        health["shopify_open_special_orders"]["status"] = "unavailable"
        self.assertEqual(_source_statuses(health)["shopify"]["status"], "unavailable")

    def test_completed_lightspeed_pull_can_be_strict_for_source_health(self):
        client = object.__new__(LightspeedClient)
        client._legacy_request = lambda *_args, **_kwargs: None
        self.assertEqual(client.get_completed_special_orders(), [])
        with self.assertRaises(LightspeedReadError):
            client.get_completed_special_orders(strict=True)

    def test_workorder_pull_can_be_strict_for_source_health(self):
        client = object.__new__(LightspeedClient)
        client._legacy_request = lambda *_args, **_kwargs: None
        self.assertEqual(client.get_workorders_by_sale_line_ids(["123"]), {})
        with self.assertRaisesRegex(LightspeedReadError, "Workorder-item read failed"):
            client.get_workorders_by_sale_line_ids(["123"], strict=True)

    def test_auxiliary_lightspeed_pulls_can_be_strict_for_source_health(self):
        client = object.__new__(LightspeedClient)
        client._legacy_request = lambda *_args, **_kwargs: None
        client.order_type_default = lambda: None

        self.assertEqual(client.get_orders_by_ids(["123"]), {})
        with self.assertRaisesRegex(LightspeedReadError, "Purchase-order read failed"):
            client.get_orders_by_ids(["123"], strict=True)

        self.assertEqual(client.get_customers_by_ids(["456"]), {})
        with self.assertRaisesRegex(LightspeedReadError, "Customer read failed"):
            client.get_customers_by_ids(["456"], strict=True)


if __name__ == "__main__":
    unittest.main()
