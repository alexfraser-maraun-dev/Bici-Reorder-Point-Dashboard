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
    def test_active_vendor_query_uses_120_day_active_po_window(self):
        client = FakeClient(rows=[
            {
                "vendor_id": "55",
                "vendor_name": "Shimano Canada",
                "active_po_count": 3,
                "last_po_ordered_at": "2026-05-01T12:00:00",
                "configured_brands": ["Shimano"],
                "location_lead_times": [
                    {"location_id": 3, "lead_time_days": 8, "po_count": 2},
                ],
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            rows = bigquery_sync.fetch_active_vendor_lead_times(active_days=120)

        self.assertEqual(rows[0]["vendor_id"], "55")
        self.assertEqual(rows[0]["configured_brands"], ["Shimano"])
        query_text = client.calls[-1]["query"]
        self.assertIn("DATE_SUB(CURRENT_DATE(), INTERVAL @active_days DAY)", query_text)
        self.assertIn("PERCENTILE_CONT(lead_time_day, 0.5)", query_text)
        params = client.calls[-1]["job_config"].query_parameters
        self.assertEqual(params[0].name, "active_days")
        self.assertEqual(params[0].value, 120)

    def test_brand_rule_upsert_clears_vendor_when_no_vendor_id_is_supplied(self):
        client = FakeClient()

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

    def test_brand_configuration_query_returns_unmapped_brands(self):
        client = FakeClient(rows=[
            {
                "brand_name": "Unmapped Brand",
                "item_count": 12,
                "preferred_vendor_id": None,
                "preferred_vendor_name": None,
                "active": False,
                "notes": None,
                "created_at": None,
                "updated_at": None,
                "updated_by": None,
            }
        ])

        with patch("app.services.bigquery_sync.get_bq_client", return_value=client):
            rows = bigquery_sync.fetch_brand_sourcing_rules()

        self.assertEqual(rows[0]["brand_name"], "Unmapped Brand")
        self.assertFalse(rows[0]["active"])
        self.assertIn("LEFT JOIN rules", client.calls[-1]["query"])

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
