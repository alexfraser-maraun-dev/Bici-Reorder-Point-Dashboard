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
                "location_id": "3", "quantity": 4, "landed_cost": 10.5,
                "source": "recommendation", "reconciliation": "new_po",
            }],
        )

    def test_draft_round_trip_and_versioned_edit(self):
        draft = self._draft()
        self.assertEqual(draft["version"], 1)
        self.assertEqual(draft["lines"][0]["quantity"], 4)
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


if __name__ == "__main__":
    unittest.main()
