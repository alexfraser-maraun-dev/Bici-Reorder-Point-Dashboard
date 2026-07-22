"""Matrix-track feature: self-sync semantics, rollup/coverage query shape, and the
subscribe/untrack control flow. Live BigQuery isn't exercised here (same posture as
test_market_queries_*): we mock the client / _rows and assert on the emitted SQL and
call flow, which is where the correctness-critical logic lives."""
import unittest
from unittest.mock import MagicMock, patch

from app.services.price_intelligence import repository, seeding, router


class ExpandTrackedMatricesTests(unittest.TestCase):
    def test_noop_without_subscriptions(self):
        with patch.object(repository, "get_active_matrix_ids", return_value=[]), \
             patch.object(seeding, "get_bq_client") as client:
            self.assertEqual(0, seeding.expand_tracked_matrices())
            client.assert_not_called()

    def test_merge_is_source_scoped_and_partitioned_by_source(self):
        mock_client = MagicMock()
        mock_client.query.return_value.result.return_value = MagicMock(num_dml_affected_rows=4)
        with patch.object(repository, "get_active_matrix_ids", return_value=["123", "456"]), \
             patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "invalidate_pi_caches"), \
             patch.object(seeding, "get_bq_client", return_value=mock_client):
            affected = seeding.expand_tracked_matrices()
        self.assertEqual(4, affected)
        sql = mock_client.query.call_args.args[0]
        # Sourced only from the subscribed matrices.
        self.assertIn("u.item_matrix_id IN UNNEST(@matrix_ids)", sql)
        # New variants are inserted as matrix-owned rows.
        self.assertIn("'matrix_sub'", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        # Archival is partitioned by source (never fights tag/top-revenue) and scoped
        # to still-subscribed matrices; pinned rows are never archived.
        self.assertIn("WHEN NOT MATCHED BY SOURCE", sql)
        self.assertIn("COALESCE(T.source, '') = 'matrix_sub'", sql)
        self.assertIn("COALESCE(T.pinned, FALSE) = FALSE", sql)
        self.assertIn("T.item_matrix_id IN UNNEST(@matrix_ids)", sql)
        # The MATCHED branch must not flip pinned or reassign source.
        matched = sql.split("WHEN NOT MATCHED")[0]
        self.assertNotIn("T.pinned = TRUE", matched)
        self.assertNotIn("T.source", matched)
        # Array param carries the subscribed ids.
        params = mock_client.query.call_args.kwargs["job_config"].query_parameters
        self.assertEqual(["123", "456"], list(params[0].values))

    def test_refresh_runs_sync_between_seed_and_descriptive(self):
        order = []
        with patch.object(seeding.config, "SEED_MODE", "track_tag"), \
             patch.object(seeding, "refresh_from_track_tag",
                          side_effect=lambda: order.append("seed") or 1), \
             patch.object(repository, "dedupe_tracked_products",
                          side_effect=lambda: order.append("dedupe") or 0), \
             patch.object(seeding, "expand_tracked_matrices",
                          side_effect=lambda: order.append("expand")), \
             patch.object(seeding, "refresh_descriptive_fields",
                          side_effect=lambda: order.append("descriptive")), \
             patch.object(repository, "refresh_item_search_index",
                          return_value={"status": "success"}):
            seeding.refresh_tracked_products()
        self.assertEqual(["seed", "dedupe", "expand", "descriptive"], order)


class MatrixQueryShapeTests(unittest.TestCase):
    def _capture(self, fn, *args):
        repository._caches.clear()
        captured = []
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "_rows",
                          side_effect=lambda query, params=None: captured.append(query) or []):
            fn(*args)
        return "\n".join(captured)

    def test_rollup_is_matrix_grain_guarded_and_non_propagating(self):
        sql = self._capture(repository.get_tracked_matrices_with_market, 7)
        # Same price_scope guard as the per-variant market query.
        self.assertIn("price_scope", sql)
        self.assertIn("item_matrix_id IS NOT NULL", sql)
        # Aggregated from each variant's own store_rep observations, grouped to matrix.
        self.assertIn("store_rep", sql)
        self.assertIn("GROUP BY mid", sql)
        self.assertIn("variants_total", sql)
        self.assertIn("variants_with_market", sql)
        # Subscription state comes from the registry table.
        self.assertIn("subscribed", sql)
        self.assertIn(repository.T_TRACKED_MATRICES, sql)

    def test_coverage_is_scoped_to_matrix_and_guarded(self):
        sql = self._capture(repository.get_matrix_coverage, "123", 45)
        self.assertIn("t.item_matrix_id = @mid", sql)
        self.assertIn("price_scope", sql)
        self.assertIn("variants_carried", sql)
        self.assertIn("variants_undercut", sql)
        self.assertIn("current_retail", sql)


class ArchiveMatrixSubTests(unittest.TestCase):
    def test_archive_touches_only_unpinned_matrix_sub_rows(self):
        mock_client = MagicMock()
        with patch.object(repository, "ensure_pi_tables"), \
             patch.object(repository, "invalidate_pi_caches"), \
             patch.object(repository, "get_bq_client", return_value=mock_client):
            repository.archive_matrix_sub_variants("123")
        sql = mock_client.query.call_args.args[0]
        self.assertIn("SET archived = TRUE", sql)
        self.assertIn("source = 'matrix_sub'", sql)
        self.assertIn("COALESCE(pinned, FALSE) = FALSE", sql)


class SubscribeMatrixRouterTests(unittest.TestCase):
    def test_subscribe_expands_then_persists(self):
        order = []
        with patch.object(seeding, "add_manual_tracked_products_for_matrix",
                          side_effect=lambda mid: order.append("expand") or 7), \
             patch.object(repository, "upsert_tracked_matrix",
                          side_effect=lambda *a, **k: order.append("persist")):
            res = router._subscribe_matrix(
                {"item_matrix_id": "123", "matrix_description": "Synapse Carbon 2"})
        self.assertEqual(["expand", "persist"], order)
        self.assertEqual(7, res["variants"])
        self.assertEqual("123", res["item_matrix_id"])

    def test_subscribe_empty_warns_without_persisting(self):
        # Search can offer a matrix the live snapshot no longer has: subscribing must
        # not 404 or create an empty subscription — it returns a plain warning.
        with patch.object(seeding, "add_manual_tracked_products_for_matrix",
                          side_effect=ValueError("No snapshot items found for matrix 10783")), \
             patch.object(repository, "upsert_tracked_matrix") as up:
            res = router._subscribe_matrix({"item_matrix_id": "10783"})
        self.assertEqual("empty", res["status"])
        self.assertEqual(0, res["variants"])
        self.assertIn("warning", res)
        up.assert_not_called()

    def test_subscribe_requires_matrix_id(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            router._subscribe_matrix({})
        self.assertEqual(400, ctx.exception.status_code)

    def test_untrack_deactivates_and_archives(self):
        calls = []
        with patch.object(repository, "set_tracked_matrix_active",
                          side_effect=lambda mid, active: calls.append(("deactivate", mid, active))), \
             patch.object(repository, "archive_matrix_sub_variants",
                          side_effect=lambda mid: calls.append(("archive", mid))):
            router.untrack_matrix("123")
        self.assertEqual([("deactivate", "123", False), ("archive", "123")], calls)


if __name__ == "__main__":
    unittest.main()
