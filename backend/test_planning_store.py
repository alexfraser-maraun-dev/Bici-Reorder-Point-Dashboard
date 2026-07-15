import os
import tempfile
import unittest

from app.services.planning_store import PlanningConflict, PlanningStore


class PlanningStoreTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.store = PlanningStore(f"sqlite:///{self.path}")

    def tearDown(self):
        os.unlink(self.path)

    def _draft(self):
        return self.store.create_draft(
            {"vendor_id": "55", "vendor_name": "Vendor", "shop_id": "3", "run_id": "run-1"},
            [{
                "recommendation_id": "rec-1", "sku": "SKU-1", "item_id": "1",
                "description": "Example item", "brand": "Example", "category_top_level": "Parts",
                "location_id": "3", "quantity": 4, "landed_cost": 10.5,
                "source": "recommendation", "reconciliation": "new_po",
            }],
        )

    def test_draft_round_trip_and_versioned_edit(self):
        draft = self._draft()
        self.assertEqual(draft["version"], 1)
        self.assertEqual(draft["lines"][0]["quantity"], 4)
        self.assertEqual(draft["lines"][0]["description"], "Example item")
        updated = self.store.replace_lines(draft["draft_id"], 1, [{
            "sku": "SKU-1", "item_id": "1", "location_id": "3", "quantity": 6,
            "source": "manual",
        }])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["lines"][0]["quantity"], 6)
        with self.assertRaises(PlanningConflict):
            self.store.replace_lines(draft["draft_id"], 1, [])

    def test_workflow_transition_is_enforced(self):
        draft = self._draft()
        approved = self.store.transition(draft["draft_id"], 1, "approved")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaises(PlanningConflict):
            self.store.transition(draft["draft_id"], 2, "synchronized")

    def test_preview_actions_are_idempotent(self):
        draft = self._draft()
        ops = [{"action": "create_unsent_order"}, {"action": "add_order_line", "item_id": "1"}]
        first = self.store.record_actions(draft, ops)
        second = self.store.record_actions(draft, ops)
        self.assertEqual(first, second)
        conn = self.store._connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM po_actions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)

    def test_override_requires_reason_and_identity(self):
        record = self.store.create_override({
            "scope_type": "sku", "scope_id": "1", "location_id": "3",
            "measure": "units", "original_value": 4, "override_value": 6,
            "reason": "Known event", "created_by": "buyer@example.com",
        })
        self.assertEqual(record["override_value"], 6.0)
        with self.assertRaises(ValueError):
            self.store.create_override({"scope_type": "sku"})

    def test_planning_run_is_persisted_and_latest_can_be_restored(self):
        run = {
            "run_id": "run-1", "status": "complete", "created_at": "2026-07-15T08:00:00Z",
            "source_snapshot_at": "2026-07-15T08:00:00Z", "scope_type": "brand",
            "scope_value": "Shimano", "config": {"model": "auto"}, "recommendations": [],
        }
        self.store.save_planning_run(run)
        self.assertEqual(self.store.get_planning_run("run-1")["scope_value"], "Shimano")
        self.assertEqual(self.store.get_latest_planning_run()["run_id"], "run-1")

    def test_unreferenced_interactive_runs_have_bounded_retention(self):
        for index in range(14):
            self.store.save_planning_run({
                "run_id": f"run-{index}", "status": "complete",
                "created_at": f"2026-07-15T08:{index:02d}:00Z",
                "scope_type": "auto_replen", "config": {}, "recommendations": [],
            })
        conn = self.store._connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM planning_runs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 12)

    def test_buyer_can_select_and_clear_an_unsent_po_target(self):
        draft = self._draft()
        routed = self.store.set_target_order(draft["draft_id"], 1, "16192")
        self.assertEqual(routed["lightspeed_order_id"], "16192")
        self.assertEqual(routed["lines"][0]["reconciliation"], "append_to_open_po")
        cleared = self.store.set_target_order(draft["draft_id"], 2, None)
        self.assertIsNone(cleared["lightspeed_order_id"])
        self.assertEqual(cleared["lines"][0]["reconciliation"], "new_po")

    def test_legacy_draft_display_metadata_can_be_backfilled(self):
        draft = self.store.create_draft(
            {"vendor_id": "55", "shop_id": "3"},
            [{"item_id": "9", "location_id": "3", "quantity": 1, "source": "manual"}],
        )
        changed = self.store.backfill_line_display_metadata([{
            "item_id": "9", "description": "Rotor", "brand": "Shimano",
            "category_top_level": "Parts",
        }])
        self.assertEqual(changed, 1)
        restored = self.store.get_draft(draft["draft_id"])
        self.assertEqual(restored["lines"][0]["description"], "Rotor")


if __name__ == "__main__":
    unittest.main()
