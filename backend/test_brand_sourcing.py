import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import to_json_safe
from app.services import bigquery_sync


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
        if query.strip().upper().startswith("CREATE TABLE"):
            return FakeQueryJob([])
        return FakeQueryJob(self.rows)


class BrandSourcingTest(unittest.TestCase):
    def setUp(self):
        bigquery_sync._active_vendor_lead_time_cache.clear()
        bigquery_sync._brand_sourcing_rules_cache.clear()
        bigquery_sync._brand_sourcing_rules_map_cache.clear()

    def test_active_vendor_query_uses_90_day_received_lead_time_window(self):
        client = FakeClient(rows=[
            {
                "vendor_id": "55",
                "active_po_count": 3,
                "active_sample_count": 3,
                "last_po_ordered_at": "2026-05-01T12:00:00",
                "location_lead_times": [
                    {"location_id": 3, "lead_time_days": 8, "po_count": 2},
                ],
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client), \
             patch("app.services.bigquery_sync.fetch_vendor_name_map", return_value={"55": "Shimano Canada"}), \
             patch("app.services.bigquery_sync.get_brand_sourcing_rules_map", return_value={
                 "Shimano": {"brand_name": "Shimano", "preferred_vendor_id": "55"}
             }):
            result = bigquery_sync.fetch_active_vendor_lead_times(active_days=90)

        rows = result["data"]
        self.assertEqual(rows[0]["vendor_id"], "55")
        self.assertEqual(rows[0]["vendor_name"], "Shimano Canada")
        self.assertEqual(rows[0]["configured_brands"], ["Shimano"])
        query_text = client.calls[-1]["query"]
        self.assertIn("DATE_SUB(CURRENT_DATE(), INTERVAL @active_days DAY)", query_text)
        self.assertIn("DATE(first_received_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @active_days DAY)", query_text)
        self.assertIn("PERCENTILE_CONT(lead_time_day, 0.5)", query_text)
        self.assertIn("first_received_at IS NOT NULL", query_text)
        self.assertNotIn("ANY_VALUE(vendor_name)", query_text)
        self.assertNotIn("v_master_snapshot_latest", query_text)
        self.assertEqual(result["meta"]["active_vendor_count"], 1)
        self.assertEqual(result["meta"]["lead_time_vendor_count"], 1)
        self.assertEqual(result["meta"]["warnings"], [])
        params = client.calls[-1]["job_config"].query_parameters
        self.assertEqual(params[0].name, "active_days")
        self.assertEqual(params[0].value, 90)

    def test_active_vendor_query_does_not_require_brand_rules_table(self):
        client = FakeClient(rows=[
            {
                "vendor_id": "99",
                "active_po_count": 1,
                "active_sample_count": 1,
                "last_po_ordered_at": None,
                "location_lead_times": [],
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client), \
             patch("app.services.bigquery_sync.fetch_vendor_name_map", side_effect=Exception("no names")), \
             patch("app.services.bigquery_sync.get_brand_sourcing_rules_map", side_effect=Exception("no rules")):
            result = bigquery_sync.fetch_active_vendor_lead_times(active_days=90)

        rows = result["data"]
        self.assertEqual(rows[0]["vendor_id"], "99")
        self.assertEqual(rows[0]["vendor_name"], "Vendor 99")
        self.assertEqual(rows[0]["configured_brands"], [])
        self.assertEqual(result["meta"]["active_vendor_count"], 1)
        self.assertEqual(result["meta"]["lead_time_vendor_count"], 0)
        self.assertEqual(len(result["meta"]["warnings"]), 2)

    def test_active_vendor_cache_reuses_recent_result(self):
        client = FakeClient(rows=[
            {
                "vendor_id": "55",
                "active_po_count": 3,
                "active_sample_count": 3,
                "last_po_ordered_at": None,
                "location_lead_times": [],
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client), \
             patch("app.services.bigquery_sync.fetch_vendor_name_map", return_value={}), \
             patch("app.services.bigquery_sync.get_brand_sourcing_rules_map", return_value={}):
            first = bigquery_sync.fetch_active_vendor_lead_times(active_days=90)
            second = bigquery_sync.fetch_active_vendor_lead_times(active_days=90)

        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)

    def test_brand_rule_map_returns_empty_when_table_is_unavailable(self):
        with patch("app.services.bigquery_sync.ensure_brand_sourcing_rules_table", side_effect=Exception("no create permission")):
            self.assertEqual(bigquery_sync.get_brand_sourcing_rules_map(), {})

    def test_brand_rule_upsert_clears_vendor_when_no_vendor_id_is_supplied(self):
        client = FakeClient()
        bigquery_sync._brand_sourcing_rules_cache["all"] = (["stale"], 1)

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            bigquery_sync.upsert_brand_sourcing_rule({
                "brand_name": "Shimano",
                "preferred_vendor_id": None,
                "preferred_vendor_name": None,
                "notes": "clear this mapping",
                "updated_by": "Test",
            })

        params = {param.name: param.value for param in client.calls[-1]["job_config"].query_parameters}
        self.assertEqual(params["brand_name"], "Shimano")
        self.assertIsNone(params["preferred_vendor_id"])
        self.assertIsNone(params["preferred_vendor_name"])
        self.assertFalse(params["active"])
        self.assertEqual(bigquery_sync._brand_sourcing_rules_cache, {})

    def test_brand_configuration_query_returns_unmapped_brands_when_rules_are_unavailable(self):
        client = FakeClient(rows=[
            {
                "brand_name": "Unmapped Brand",
                "item_count": 12,
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client), \
             patch("app.services.bigquery_sync.ensure_brand_sourcing_rules_table", side_effect=Exception("no rules")):
            rows = bigquery_sync.fetch_brand_sourcing_rules()

        self.assertEqual(rows[0]["brand_name"], "Unmapped Brand")
        self.assertFalse(rows[0]["active"])
        self.assertIsNone(rows[0]["preferred_vendor_id"])
        self.assertNotIn("replen_brand_sourcing_rules", client.calls[0]["query"])

    def test_json_safe_serializes_nested_timestamps(self):
        class TimestampLike:
            def isoformat(self):
                return "2026-05-22T10:00:00"

        payload = [{"updated_at": TimestampLike(), "nested": [{"created_at": TimestampLike()}]}]
        self.assertEqual(
            to_json_safe(payload),
            [{"updated_at": "2026-05-22T10:00:00", "nested": [{"created_at": "2026-05-22T10:00:00"}]}],
        )


if __name__ == "__main__":
    unittest.main()
