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


class StagingTableTests(unittest.TestCase):
    """MERGE staging tables must be unique per call — a shared `<table>_temp`
    name lets concurrent writers WRITE_TRUNCATE each other's staged rows."""

    def _run_merge(self, client):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_bq_client", return_value=client):
            repository._merge_upsert(
                "ds.pi_product_links", [{"match_key": "k", "status": "pending"}],
                "match_key", update_cols=["status"],
                insert_cols=["match_key", "status"])

    def _staged_name(self, client):
        return client.load_table_from_json.call_args.args[1]

    def test_staging_names_unique_across_calls(self):
        c1, c2 = MagicMock(), MagicMock()
        self._run_merge(c1)
        self._run_merge(c2)
        n1, n2 = self._staged_name(c1), self._staged_name(c2)
        self.assertNotEqual(n1, n2)
        for name in (n1, n2):
            self.assertTrue(name.startswith("ds.pi_product_links_tmp_"))

    def test_staging_table_deleted_even_when_merge_fails(self):
        client = MagicMock()
        client.query.side_effect = RuntimeError("merge failed")
        with self.assertRaises(RuntimeError):
            self._run_merge(client)
        client.delete_table.assert_called_once_with(
            self._staged_name(client), not_found_ok=True)

    def test_insert_product_links_and_verdicts_use_unique_staging(self):
        client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "invalidate_pi_caches"):
            repository.insert_product_links([{"match_key": "k", "fuzzy_score": 0.9}])
            first = self._staged_name(client)
            repository.update_link_verdicts([{"link_id": "l1", "llm_verdict": "match"}])
            second = self._staged_name(client)
        self.assertNotEqual(first, second)
        self.assertIn("_tmp_", first)
        self.assertIn("_tmp_", second)
        self.assertEqual(2, client.delete_table.call_count)


class TrackedJoinDedupeTests(unittest.TestCase):
    """Reads that join pi_tracked_products must collapse duplicate item_id rows
    (concurrent manual-pin MERGEs can insert two) — otherwise URLs are scraped
    twice, the Match queue shows phantom links, and the pending badge inflates."""

    def _captured_sql(self, fn, *args, **kwargs):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=[]) as mock_rows:
            fn(*args, **kwargs)
        return mock_rows.call_args.args[0]

    def test_tracked_joins_are_deduped(self):
        for fn in (repository.get_tracked_urls,
                   repository.get_product_links,
                   repository.count_pending_links):
            sql = self._captured_sql(fn)
            self.assertIn("PARTITION BY item_id", sql,
                          f"{fn.__name__} joins pi_tracked_products without dedupe")


if __name__ == "__main__":
    unittest.main()
