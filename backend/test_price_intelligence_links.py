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


class CrawlStateRepositoryTests(unittest.TestCase):
    def test_mark_competitor_scraped_writes_crawl_state_in_same_update(self):
        client = MagicMock()
        state = {"cursor": 41, "cap_hit": True, "products_seen": 9000}
        with patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "_cache_lock"), \
             patch.object(repository, "_caches", {}):
            repository.mark_competitor_scraped("c1", "success (cap hit — rotating)", state)
        sql = client.query.call_args.args[0]
        self.assertIn("crawl_state_json = @crawl_state", sql)
        params = {p.name: p.value
                  for p in client.query.call_args.kwargs["job_config"].query_parameters}
        self.assertEqual(state, __import__("json").loads(params["crawl_state"]))

    def test_mark_competitor_scraped_without_state_leaves_cursor_untouched(self):
        client = MagicMock()
        with patch.object(repository, "get_bq_client", return_value=client), \
             patch.object(repository, "_cache_lock"), \
             patch.object(repository, "_caches", {}):
            repository.mark_competitor_scraped("c1", "failed: boom")
        self.assertNotIn("crawl_state_json", client.query.call_args.args[0])

    def test_latest_observation_map_prefix_filter(self):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=[]) as mock_rows:
            repository.get_latest_observation_map(diff_key_prefix="cat:c1:")
        sql = mock_rows.call_args.args[0]
        self.assertIn("STARTS_WITH(diff_key, @prefix)", sql)
        params = {p.name: p.value for p in mock_rows.call_args.kwargs["params"]}
        self.assertEqual("cat:c1:", params["prefix"])

    def test_latest_observation_map_no_prefix_unfiltered(self):
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows", return_value=[]) as mock_rows:
            repository.get_latest_observation_map()
        self.assertNotIn("STARTS_WITH", mock_rows.call_args.args[0])


class CleanupReanchorTests(unittest.TestCase):
    """The hygiene sweep must repair the queue, not just empty it.

    Live shape: Steed's Tucker III L/XL and S/M listings were both stored against
    our M/L item (combined sizes used to agree on overlap). Both are correct
    matches pointed at the wrong variant of the same matrix, so rejecting them
    would throw away real coverage AND tombstone the (item, store) pair.
    """

    MODEL = "Sweet Protection Tucker III 2Vi Mips Helmet"

    def _tracked(self, item_id, colour, size):
        return {"item_id": item_id, "title": self.MODEL, "brand": "Sweet Protection",
                "sku": f"s{item_id}", "upc_normalized": None, "item_matrix_id": "t1",
                "matrix_description": self.MODEL, "attribute_1": colour,
                "attribute_2": size, "attribute_3": None}

    def _link(self, link_id, title, status="pending"):
        # Every row is stored against our Matte Black / M/L item.
        return {"link_id": link_id, "item_id": "357", "competitor_id": "steed",
                "status": status, "competitor_title": title,
                "competitor_url": f"https://steedcycles.com/{link_id}",
                "variant_options_json": None, "source": "attr",
                "confidence": 0.97, "fuzzy_score": 100.0, "llm_verdict": None,
                "item_attribute_1": "Matte Black", "item_attribute_2": "M/L",
                "item_attribute_3": None}

    def _sweep(self, links):
        tracked = [
            self._tracked("357", "Matte Black", "M/L"),
            self._tracked("356", "Matte Black", "S/M"),
            self._tracked("358", "Matte Black", "L/XL"),
            self._tracked("355", "Satin White", "L/XL"),
        ]
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "get_product_links",
                          side_effect=[[], links]), \
             patch.object(repository, "get_tracked_products", return_value=tracked):
            return repository.cleanup_mismatched_links(apply=False)

    def test_a_wrong_size_link_is_repointed_not_rejected(self):
        report = self._sweep([
            self._link("a", f"{self.MODEL} - Matte Black / L/XL (59-61cm)"),
            self._link("b", f"{self.MODEL} - Matte Black / S/M (53-56cm)"),
        ])
        self.assertEqual(2, report["reanchored_exact"])
        self.assertEqual(0, report["attr_reject_pending"])
        moves = {s["to_item_id"] for s in report["reanchor_samples"]}
        self.assertEqual({"358", "356"}, moves)

    def test_an_uncertain_colourway_moves_as_partial_rather_than_rejecting(self):
        """We stock no Bronco White. Rejecting would tombstone the pair, which is
        too strong for a colourway we simply cannot name — it goes to review."""
        report = self._sweep([
            self._link("c", f"{self.MODEL} - Bronco White / L/XL (59-61cm)"),
        ])
        self.assertEqual(1, report["reanchored_partial"])
        self.assertEqual(0, report["attr_reject_pending"])
        self.assertEqual("attr_partial", report["reanchor_samples"][0]["as"])

    def test_a_link_with_no_matching_sibling_is_still_rejected(self):
        report = self._sweep([
            self._link("d", f"{self.MODEL} - Neon Yellow / XXL"),
        ])
        self.assertEqual(0, report["reanchored_exact"] + report["reanchored_partial"])
        self.assertEqual(1, report["attr_reject_pending"])

    def test_a_correctly_anchored_link_is_left_alone(self):
        report = self._sweep([
            self._link("e", f"{self.MODEL} - Matte Black / M/L (56-59cm)"),
        ])
        self.assertEqual(0, report["reanchored_exact"] + report["reanchored_partial"])
        self.assertEqual(0, report["attr_reject_pending"])


if __name__ == "__main__":
    unittest.main()
