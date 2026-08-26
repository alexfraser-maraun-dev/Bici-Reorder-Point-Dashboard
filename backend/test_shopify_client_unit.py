"""Offline trust-boundary tests for strict Shopify reads."""

import unittest

from app.services.shopify_client import (
    BIKE_SALE_TAGS,
    SPECIAL_ORDER_TAG_QUERY,
    ShopifyClient,
    _classify_population,
)
from app.services.lightspeed_client import LightspeedClient, LightspeedReadError
from app.services.special_order_service import _source_statuses


def _client(configured=True):
    client = ShopifyClient()
    client._configured = lambda: configured
    return client


class SpecialOrderPopulationTest(unittest.TestCase):
    """The population definition. Nothing guarded this before, so widening it shipped unverified."""

    def test_the_query_covers_both_signals_and_parenthesises_the_bike_stack(self):
        q = SPECIAL_ORDER_TAG_QUERY
        self.assertIn("tag:SO", q)
        for tag in BIKE_SALE_TAGS:
            self.assertIn(f"tag:{tag}", q)
        # The bike tags must bind together as ONE arm of the OR. Without the inner parentheses an
        # order carrying only `bikesale` would join the population.
        self.assertIn("(tag:bikesale AND tag:bikenothere AND tag:orderconfirmed)", q)
        # And the OUTER parentheses matter because _RECENT_SO_QUERY appends `AND created_at:>...`
        # to a filter of this shape; without them the date bound would apply to one arm only.
        self.assertTrue(q.startswith("(") and q.endswith(")"), q)
        self.assertIn(q, ShopifyClient._OPEN_SO_QUERY)

    def test_the_late_match_fallback_is_deliberately_not_widened(self):
        # 392 of the 435 bike-stack orders are already fulfilled. The fallback pass exists to look
        # at fulfilled/archived orders, so widening it would add all of them as match candidates —
        # exactly the ambiguity _shopify_fallback_rows() was split out to avoid.
        self.assertIn('query: "tag:SO AND created_at', ShopifyClient._RECENT_SO_QUERY)
        self.assertNotIn("bikesale", ShopifyClient._RECENT_SO_QUERY)

    def test_provenance_classification(self):
        stack = ["bikesale", "BIKENOTHERE", "ORDERCONFIRMED"]
        self.assertEqual(_classify_population(stack), "bike_sale")
        # The stack wins over a hand-applied SO tag: it is the more specific signal, and the
        # workflow applies it automatically where SO is typed by a person.
        self.assertEqual(_classify_population(["SO"] + stack), "bike_sale")
        self.assertEqual(_classify_population(["SO"]), "so_tag")
        # Two of three is a different thing entirely — `bikesale` is on every bike order.
        self.assertEqual(_classify_population(["bikesale", "BIKENOTHERE"]), "so_tag")
        # Never raises: mislabelling beats dropping a real special order.
        self.assertEqual(_classify_population(None), "so_tag")

    def test_population_reaches_the_flattened_rows(self):
        client = _client(configured=True)
        client._graphql = lambda query, variables=None: {"orders": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"id": "gid://shopify/Order/1", "name": "#1", "email": "a@x.test",
                 "tags": ["bikesale", "BIKENOTHERE", "ORDERCONFIRMED"],
                 "displayFulfillmentStatus": "UNFULFILLED",
                 "displayFinancialStatus": "PARTIALLY_PAID",
                 "createdAt": "2026-08-22T00:00:00Z", "cancelledAt": None,
                 "closed": False, "test": False, "shippingAddress": None, "metafield": None,
                 "lineItems": {"nodes": [{"sku": "210000110432", "unfulfilledQuantity": 1}]}},
                {"id": "gid://shopify/Order/2", "name": "#2", "email": "b@x.test",
                 "tags": ["SO"], "displayFulfillmentStatus": "UNFULFILLED",
                 "displayFinancialStatus": "PAID",
                 "createdAt": "2026-08-22T00:00:00Z", "cancelledAt": None,
                 "closed": False, "test": False, "shippingAddress": None, "metafield": None,
                 "lineItems": {"nodes": [{"sku": "210000000001", "unfulfilledQuantity": 1}]}},
            ],
        }}
        rows = client.get_open_special_orders()
        self.assertEqual({r["order_id"]: r["population"] for r in rows},
                         {"1": "bike_sale", "2": "so_tag"})
        # A part-paid bike sale must survive: the special order is routinely raised before the
        # customer has paid in full, and financial status is deliberately never a filter.
        self.assertEqual(rows[0]["financial_status"], "PARTIALLY_PAID")


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
