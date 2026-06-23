"""
Covers the brand-level "Available from" sourcing that feeds each Special Order tile:
  - fetch_brand_vendor_sourcing(): the empirical brand->vendors query (dedup + noise threshold).
  - _compute_available_vendors(): per-SO assembly (store lead time, median fallback, fastest-first,
    cap at 3).
Run:  python -m unittest test_available_vendors
"""
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services import bigquery_sync
from app.services.special_order_service import _compute_available_vendors, _MAX_AVAILABLE_VENDORS


class FakeQueryJob:
    def __init__(self, rows=None):
        self.rows = rows or []

    def result(self):
        return self.rows


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def query(self, query, job_config=None):
        self.calls.append({"query": query, "job_config": job_config})
        return FakeQueryJob(self.rows)


class FetchBrandVendorSourcingTest(unittest.TestCase):
    def setUp(self):
        bigquery_sync._brand_vendor_sourcing_cache.clear()

    def test_groups_vendors_by_brand_and_query_shape(self):
        client = FakeClient(rows=[
            {"brand_name": "Shimano", "vendor_id": 401, "vendor_name": "Shimano", "distinct_items": 731},
            {"brand_name": "Shimano", "vendor_id": 388, "vendor_name": "HLC (Cycles Lambert)", "distinct_items": 42},
            {"brand_name": "Sapim", "vendor_id": 900, "vendor_name": "Orange Sport Supply", "distinct_items": 5},
        ])
        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            result = bigquery_sync.fetch_brand_vendor_sourcing(lookback_days=365, min_distinct_items=3)

        self.assertEqual(
            [v["vendor_id"] for v in result["Shimano"]], ["401", "388"]
        )
        self.assertEqual(result["Shimano"][1]["vendor_name"], "HLC (Cycles Lambert)")
        self.assertEqual([v["vendor_id"] for v in result["Sapim"]], ["900"])

        q = client.calls[-1]["query"]
        self.assertIn("SELECT DISTINCT", q)  # item->brand dedup prevents the join fan-out
        self.assertIn("COUNT(DISTINCT p.item_id) >= @min_distinct_items", q)  # noise threshold
        self.assertIn("DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)", q)  # recency window
        params = {p.name: p.value for p in client.calls[-1]["job_config"].query_parameters}
        self.assertEqual(params["lookback_days"], 365)
        self.assertEqual(params["min_distinct_items"], 3)

    def test_missing_vendor_name_falls_back_to_id_label(self):
        client = FakeClient(rows=[
            {"brand_name": "X", "vendor_id": 77, "vendor_name": None, "distinct_items": 4},
        ])
        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            result = bigquery_sync.fetch_brand_vendor_sourcing()
        self.assertEqual(result["X"][0]["vendor_name"], "Vendor 77")

    def test_cache_reuses_recent_result(self):
        client = FakeClient(rows=[
            {"brand_name": "X", "vendor_id": 1, "vendor_name": "A", "distinct_items": 9},
        ])
        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            first = bigquery_sync.fetch_brand_vendor_sourcing()
            second = bigquery_sync.fetch_brand_vendor_sourcing()
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)


class ComputeAvailableVendorsTest(unittest.TestCase):
    def _sourcing(self):
        return {
            "Shimano": [
                {"vendor_id": "401", "vendor_name": "Shimano", "distinct_items": 731},
                {"vendor_id": "388", "vendor_name": "HLC (Cycles Lambert)", "distinct_items": 42},
            ]
        }

    def test_prefers_store_lead_then_sorts_fastest_first(self):
        loc = {("401", "3"): 5.0, ("388", "3"): 2.0}
        out = _compute_available_vendors("Shimano", "3", self._sourcing(), loc, {})
        self.assertEqual([v["vendor_id"] for v in out], ["388", "401"])  # 2d before 5d
        self.assertEqual([v["lead_time_days"] for v in out], [2, 5])
        self.assertTrue(all(v["lead_time_source"] == "store" for v in out))

    def test_falls_back_to_vendor_median_when_store_has_no_sample(self):
        # No (vendor, store=20) entries; the per-vendor median is used and marked accordingly.
        out = _compute_available_vendors("Shimano", "20", self._sourcing(), {}, {"401": 6.0, "388": 2.0})
        by_id = {v["vendor_id"]: v for v in out}
        self.assertEqual(by_id["388"]["lead_time_days"], 2)
        self.assertEqual(by_id["388"]["lead_time_source"], "vendor_median")

    def test_unknown_brand_returns_empty(self):
        self.assertEqual(_compute_available_vendors(None, "3", self._sourcing(), {}, {}), [])
        self.assertEqual(_compute_available_vendors("Nope", "3", self._sourcing(), {}, {}), [])

    def test_caps_at_max_and_unknown_lead_times_rank_last(self):
        sourcing = {"B": [{"vendor_id": str(i), "vendor_name": f"V{i}", "distinct_items": 5} for i in range(5)]}
        # Only some vendors have a lead time; vendors without one must rank after those with one.
        loc = {("0", "3"): 9.0, ("1", "3"): 3.0}
        out = _compute_available_vendors("B", "3", sourcing, loc, {})
        self.assertEqual(len(out), _MAX_AVAILABLE_VENDORS)
        self.assertEqual([out[0]["vendor_id"], out[1]["vendor_id"]], ["1", "0"])  # 3d, 9d, then unknowns
        self.assertIsNone(out[2]["lead_time_days"])


if __name__ == "__main__":
    unittest.main()
