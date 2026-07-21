"""Item-search speedup: fast-path over the pi_item_search index, live fallback when
the index isn't built, per-query TTL cache, and the CREATE OR REPLACE builder. Live
BigQuery isn't exercised (same posture as the other PI tests) — we mock _rows / the
client and assert on the emitted SQL and control flow."""
import unittest
from unittest.mock import MagicMock, patch

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
        self.assertIn("search_text LIKE @like", sql)
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

    def test_repeat_search_served_from_cache(self):
        with patch.object(repository, "_rows",
                          return_value=[{"item_id": "1"}]) as rows:
            first = repository.search_snapshot_items("floor pump")
            second = repository.search_snapshot_items("floor pump")
        self.assertEqual(first, second)
        self.assertEqual(1, rows.call_count)  # second call hit the TTL cache


class IndexBuilderTests(unittest.TestCase):
    def test_refresh_emits_create_or_replace_full_catalog(self):
        mock_client = MagicMock()
        with patch.object(repository, "get_bq_client", return_value=mock_client), \
             patch.object(repository, "invalidate_pi_caches") as inval:
            repository.refresh_item_search_index()
        sql = mock_client.query.call_args.args[0]
        self.assertIn("CREATE OR REPLACE TABLE", sql)
        self.assertIn(repository.T_ITEM_SEARCH, sql)
        self.assertIn("search_text", sql)
        self.assertIn("built_at", sql)
        self.assertIn("v_master_snapshot_latest", sql)  # sourced from the same snapshot
        # A full-catalog build must not filter to a search term.
        self.assertNotIn("LIKE @like", sql)
        inval.assert_called_once()

    def test_built_at_none_when_index_absent(self):
        with patch.object(repository, "_rows", side_effect=NotFound("no table")):
            self.assertIsNone(repository.item_search_built_at())

    def test_built_at_returns_timestamp(self):
        with patch.object(repository, "_rows", return_value=[{"built_at": "2026-07-21T00:00:00Z"}]):
            self.assertEqual("2026-07-21T00:00:00Z", repository.item_search_built_at())


class SeedRebuildsIndexTests(unittest.TestCase):
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
                          side_effect=lambda: order.append("search_index")):
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


if __name__ == "__main__":
    unittest.main()
