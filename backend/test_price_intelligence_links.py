"""Bulk Match-tab decisions stay guarded while using one BigQuery mutation."""
import unittest
from unittest.mock import MagicMock, patch

from app.services.price_intelligence import repository


class BulkLinkDecisionTests(unittest.TestCase):
    def test_bulk_confirm_guards_conflicts_and_duplicate_store_matches(self):
        client = MagicMock()
        selected = [
            {"link_id": "l1", "item_id": "i1", "competitor_id": "c1",
             "competitor_title": "Road Bike", "status": "pending",
             "a1": "Blue", "a2": "56", "a3": None},
            {"link_id": "l2", "item_id": "i1", "competitor_id": "c1",
             "competitor_title": "Road Bike", "status": "pending",
             "a1": "Blue", "a2": "56", "a3": None},
            {"link_id": "l3", "item_id": "i2", "competitor_id": "c1",
             "competitor_title": "Road Bike - Red / 56", "status": "pending",
             "a1": "Blue", "a2": "56", "a3": None},
        ]
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=[selected, []]), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            result = repository.decide_links_bulk(
                ["l1", "l2", "l3"], "confirmed", decided_by="Buyer")

        self.assertEqual(
            ["confirmed", "skipped", "rejected"],
            [row["status"] for row in result["results"]],
        )
        self.assertEqual(["l1"], result["confirmed_link_ids"])
        self.assertEqual(1, client.query.call_count)
        sql = client.query.call_args.args[0]
        self.assertIn("UPDATE", sql)
        self.assertIn("UNNEST(@decided_ids)", sql)
        params = {
            param.name: param.values if hasattr(param, "values") else param.value
            for param in client.query.call_args.kwargs["job_config"].query_parameters
        }
        self.assertEqual(["l1"], params["confirmed_ids"])
        self.assertEqual(["l1", "l3"], params["decided_ids"])

    def test_existing_confirmed_link_skips_without_dml(self):
        selected = [{
            "link_id": "new", "item_id": "i1", "competitor_id": "c1",
            "competitor_title": "Road Bike", "status": "pending",
            "a1": None, "a2": None, "a3": None,
        }]
        existing = [{"link_id": "old", "item_id": "i1", "competitor_id": "c1"}]
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", side_effect=[selected, existing]), \
             patch.object(repository, "get_bq_client", return_value=client):
            result = repository.decide_links_bulk(["new"], "confirmed")

        self.assertEqual("skipped", result["results"][0]["status"])
        self.assertTrue(result["results"][0]["can_replace"])
        client.query.assert_not_called()

    def test_bulk_reject_uses_one_dml_for_large_selection(self):
        link_ids = [f"l{i}" for i in range(50)]
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            result = repository.decide_links_bulk(link_ids, "rejected")

        self.assertEqual(50, len(result["results"]))
        self.assertTrue(all(row["status"] == "rejected" for row in result["results"]))
        self.assertEqual(1, client.query.call_count)


if __name__ == "__main__":
    unittest.main()
