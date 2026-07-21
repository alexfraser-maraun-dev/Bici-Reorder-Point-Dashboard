"""Item-search index, independent fallback, bounded caches, and rebuild reporting."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from google.cloud import bigquery
from google.api_core.exceptions import NotFound

from app.services.price_intelligence import repository, seeding


class SearchFastPathTests(unittest.TestCase):
    def setUp(self):
        repository._caches.clear()

    def test_fast_path_hits_the_index_not_the_snapshot(self):
        captured = []
        with patch.object(repository, "_rows",
                          side_effect=lambda q, params=None: captured.append(q) or []):
            repository.search_snapshot_items("tarmac")
        self.assertEqual(1, len(captured))
        sql = captured[0]
        self.assertIn(repository.T_ITEM_SEARCH, sql)
        self.assertIn("STRPOS(search_text, @term)", sql)
        # The fast path must NOT touch the expensive daily-series join.
        self.assertNotIn("v_master_snapshot_latest", sql)

    def test_falls_back_to_live_query_when_index_missing(self):
        calls = []

        def side_effect(q, params=None):
            calls.append(q)
            if len(calls) == 1:
                raise NotFound("pi_item_search not found")
            return []

        with patch.object(repository, "_rows", side_effect=side_effect):
            repository.search_snapshot_items("tarmac")
        self.assertEqual(2, len(calls))
        self.assertIn(repository.T_ITEM_SEARCH, calls[0])          # fast path tried
        self.assertIn("v_master_snapshot_latest", calls[1])        # live fallback
        self.assertIn("item_history", calls[1])
        self.assertNotIn("search_text", calls[1])                   # independent SQL path
        self.assertIn("STRPOS(LOWER(COALESCE(title, '')), @term)", calls[1])

    def test_repeat_search_served_from_cache(self):
        with patch.object(repository, "_rows",
                          return_value=[{"item_id": "1"}]) as rows:
            first = repository.search_snapshot_items("floor pump")
            second = repository.search_snapshot_items("floor pump")
        self.assertEqual(first, second)
        self.assertEqual(1, rows.call_count)  # second call hit the TTL cache

    def test_cache_is_bounded_and_evicts_oldest(self):
        with patch.object(repository, "_CACHE_MAX_ENTRIES", 2):
            repository._cache_set("a", 1)
            repository._cache_set("b", 2)
            repository._cache_set("c", 3)
        self.assertNotIn("a", repository._caches)
        self.assertEqual(["b", "c"], list(repository._caches))


class IndexBuilderTests(unittest.TestCase):
    def test_refresh_emits_create_or_replace_full_catalog(self):
        mock_client = MagicMock()
        mock_client.get_table.return_value.num_rows = 55_822
        mock_client.get_table.return_value.num_bytes = 10_000_000
        mock_client.get_table.return_value.modified = datetime.now(timezone.utc)
        with patch.object(repository, "get_bq_client", return_value=mock_client), \
             patch.object(repository, "invalidate_pi_caches") as inval:
            result = repository.refresh_item_search_index(trigger="test")
        sql = mock_client.query.call_args.args[0]
        self.assertIn("CREATE OR REPLACE TABLE", sql)
        self.assertIn(repository.T_ITEM_SEARCH, sql)
        self.assertIn("search_text", sql)
        self.assertIn("built_at", sql)
        self.assertIn("ARRAY_TO_STRING", sql)
        self.assertNotIn("CONCAT_WS", sql)
        self.assertIn("brand", sql)
        self.assertIn("system_sku", sql)
        self.assertIn("upc_normalized", sql)
        self.assertIn("v_master_snapshot_latest", sql)  # sourced from the same snapshot
        # A full-catalog build must not filter to a search term.
        self.assertNotIn("@term", sql)
        inval.assert_called_once()
        self.assertEqual("success", result["status"])
        self.assertEqual(55_822, result["row_count"])

    def test_built_at_none_when_index_absent(self):
        mock_client = MagicMock()
        mock_client.get_table.side_effect = NotFound("no table")
        with patch.object(repository, "get_bq_client", return_value=mock_client):
            self.assertIsNone(repository.item_search_built_at())

    def test_built_at_returns_timestamp(self):
        built = datetime(2026, 7, 21, tzinfo=timezone.utc)
        mock_client = MagicMock()
        mock_client.get_table.return_value.modified = built
        with patch.object(repository, "get_bq_client", return_value=mock_client):
            self.assertEqual(built, repository.item_search_built_at())

    @unittest.skipUnless(os.getenv("RUN_BIGQUERY_DRY_RUN") == "1", "live BQ dry-run disabled")
    def test_build_sql_is_accepted_by_bigquery(self):
        client = repository.get_bq_client()
        build_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        build_job = client.query(
            repository._item_search_build_query(), job_config=build_config,
        )
        self.assertGreater(build_job.total_bytes_processed or 0, 0)

        live_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            query_parameters=[
                bigquery.ScalarQueryParameter("term", "STRING", "tarmac"),
                bigquery.ScalarQueryParameter("exact", "STRING", "tarmac"),
            ],
        )
        live_job = client.query(
            repository._item_search_live_query(), job_config=live_config,
        )
        self.assertGreater(live_job.total_bytes_processed or 0, 0)


class SeedRebuildsIndexTests(unittest.TestCase):
    def test_manual_refresh_is_marked_queued_before_background_work(self):
        seeding._refresh_status["status"] = "idle"
        self.assertTrue(seeding.queue_refresh("manual"))
        self.assertEqual("queued", seeding._refresh_status["status"])
        self.assertFalse(seeding.queue_refresh("manual"))

    def test_refresh_tracked_products_rebuilds_index_last(self):
        order = []
        with patch.object(seeding.config, "SEED_MODE", "track_tag"), \
             patch.object(seeding, "refresh_from_track_tag",
                          side_effect=lambda: order.append("seed") or 1), \
             patch.object(seeding, "expand_tracked_matrices",
                          side_effect=lambda: order.append("expand")), \
             patch.object(seeding, "refresh_descriptive_fields",
                          side_effect=lambda: order.append("descriptive")), \
             patch.object(repository, "refresh_item_search_index",
                          side_effect=lambda trigger="unknown":
                          order.append("search_index") or {"status": "success"}):
            seeding.refresh_tracked_products()
        self.assertEqual(["seed", "expand", "descriptive", "search_index"], order)

    def test_index_failure_does_not_abort_seed(self):
        with patch.object(seeding.config, "SEED_MODE", "track_tag"), \
             patch.object(seeding, "refresh_from_track_tag", return_value=5), \
             patch.object(seeding, "expand_tracked_matrices"), \
             patch.object(seeding, "refresh_descriptive_fields"), \
             patch.object(repository, "refresh_item_search_index",
                          side_effect=RuntimeError("BQ down")):
            # The seed must still return its count even if the index rebuild throws.
            self.assertEqual(5, seeding.refresh_tracked_products())
        self.assertEqual("partial", seeding._refresh_status["status"])

    def test_nightly_can_surface_index_failure_to_scrape_run(self):
        with patch.object(seeding.config, "SEED_MODE", "track_tag"), \
             patch.object(seeding, "refresh_from_track_tag", return_value=5), \
             patch.object(seeding, "expand_tracked_matrices"), \
             patch.object(seeding, "refresh_descriptive_fields"), \
             patch.object(repository, "refresh_item_search_index",
                          side_effect=RuntimeError("BQ down")):
            with self.assertRaises(seeding.ItemSearchIndexRefreshError):
                seeding.refresh_tracked_products(raise_on_index_error=True)
        self.assertEqual("partial", seeding._refresh_status["status"])


if __name__ == "__main__":
    unittest.main()
