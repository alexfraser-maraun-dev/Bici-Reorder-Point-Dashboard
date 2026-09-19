"""Inventory-intelligence extraction plumbing and query safety."""
import unittest
from unittest.mock import patch

from app.services.price_intelligence import repository, router, scrape_runner


class StockObservationPlumbingTests(unittest.TestCase):
    def test_extraction_fields_carry_optional_stock_without_touching_in_stock(self):
        fields = scrape_runner._extraction_fields({
            "price_scope": "variant",
            "stock_status": "low_stock",
            "reported_quantity": 2,
            "quantity_kind": "exact",
        })
        self.assertEqual("low_stock", fields["stock_status"])
        self.assertEqual(2, fields["reported_quantity"])
        self.assertEqual("exact", fields["quantity_kind"])
        self.assertNotIn("in_stock", fields)

    def test_stock_metadata_does_not_enter_existing_market_math(self):
        repository._caches.clear()
        captured = []
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows",
                          side_effect=lambda query, params=None: captured.append(query) or []):
            repository.get_tracked_products_with_market(days=7)
        sql = "\n".join(captured)
        self.assertIn("MIN(IF(in_stock, price, NULL))", sql)
        self.assertNotIn("reported_quantity", sql)
        self.assertNotIn("stock_status", sql)


class StockAnalyticsQueryTests(unittest.TestCase):
    def setUp(self):
        repository._caches.clear()

    def tearDown(self):
        repository._caches.clear()

    def test_summary_is_partition_bounded_daily_and_observation_weighted(self):
        captured = []
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows",
                          side_effect=lambda query, params=None: captured.append(query) or []):
            self.assertEqual([], repository.get_stock_intelligence())
        sql = captured[0]
        self.assertIn("INTERVAL 365 DAY", sql)
        self.assertIn("America/Vancouver", sql)
        self.assertIn("PARTITION BY competitor_key, match_item_id, observed_day", sql)
        self.assertIn("WHEN 'link' THEN 0 WHEN 'url' THEN 1", sql)
        self.assertIn("observed_nights_30d", sql)
        self.assertIn("restocks_90d", sql)
        self.assertIn("INTERVAL 48 HOUR", sql)
        self.assertIn("price_scope = 'product' AND t.item_matrix_id IS NULL", sql)
        self.assertIn("gmb_benchmark", sql)

    def test_summary_uses_the_five_minute_repository_cache(self):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=[{"item_id": "1"}]) as rows:
            self.assertEqual([{"item_id": "1"}], repository.get_stock_intelligence())
            self.assertEqual([{"item_id": "1"}], repository.get_stock_intelligence())
        rows.assert_called_once()

    def test_history_is_lazy_scoped_and_clamps_lookback(self):
        captured = []
        captured_params = []
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows",
                          side_effect=lambda query, params=None:
                          (captured.append(query), captured_params.extend(params or []), [])[2]):
            result = repository.get_stock_history("7", "store-1", days=999)
        self.assertEqual(365, result["days"])
        self.assertIn("o.match_item_id = @item_id", captured[0])
        self.assertIn("competitor_key = @competitor_key", captured[0])
        values = {p.name: p.value for p in captured_params}
        self.assertEqual("7", values["item_id"])
        self.assertEqual("store-1", values["competitor_key"])
        self.assertEqual(365, values["days"])

    def test_router_exposes_summary_and_history(self):
        with patch.object(repository, "get_stock_intelligence", return_value=[{"item_id": "1"}]):
            self.assertEqual([{"item_id": "1"}], router.stock_intelligence())
        with patch.object(repository, "get_stock_history", return_value={"points": []}) as history:
            self.assertEqual({"points": []}, router.stock_history("1", "c1", 60))
        history.assert_called_once_with("1", "c1", days=60)


if __name__ == "__main__":
    unittest.main()
