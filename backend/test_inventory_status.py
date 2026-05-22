import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.replenishment_engine import calculate_inventory_status


class InventoryStatusTest(unittest.TestCase):
    def assert_status(self, expected, on_hand, on_order, reorder_point, desired_level):
        result = calculate_inventory_status(on_hand, on_order, reorder_point, desired_level)
        self.assertEqual(result["inventory_status"], expected)
        self.assertIn("inventory_status_label", result)
        self.assertIn("inventory_status_reason", result)
        self.assertEqual(result["inventory_position"], on_hand + on_order)

    def test_low_inventory_bands(self):
        self.assert_status("critical", 5, 0, 10, 60)
        self.assert_status("low", 7, 0, 10, 60)
        self.assert_status("warning", 11, 0, 10, 60)
        self.assert_status("healthy", 20, 0, 10, 60)

    def test_desired_level_and_high_inventory_bands(self):
        self.assert_status("on_target", 50, 0, 10, 60)
        self.assert_status("high", 72, 0, 10, 60)
        self.assert_status("overstock", 90, 0, 10, 60)

    def test_pipeline_changes_status_when_on_hand_is_low(self):
        self.assert_status("low", 7, 0, 10, 60)
        self.assert_status("incoming", 7, 43, 10, 60)

    def test_high_inventory_wins_over_shelf_risk(self):
        self.assert_status("overstock", 5, 85, 10, 60)

    def test_no_demand(self):
        self.assert_status("no_demand", 0, 0, 0, 0)

    def test_negative_qoh_is_floored_for_inventory_position(self):
        result = calculate_inventory_status(-5, 0, 10, 60)

        self.assertEqual(result["effective_on_hand"], 0)
        self.assertTrue(result["qoh_adjusted_for_math"])
        self.assertEqual(result["inventory_position"], 0)
        self.assertEqual(result["inventory_status"], "critical")
        self.assertIn("QOH is negative (-5)", result["inventory_status_reason"])

    def test_negative_qoh_with_on_order_uses_on_order_only(self):
        result = calculate_inventory_status(-5, 60, 10, 60)

        self.assertEqual(result["effective_on_hand"], 0)
        self.assertTrue(result["qoh_adjusted_for_math"])
        self.assertEqual(result["inventory_position"], 60)
        self.assertEqual(result["inventory_status"], "incoming")


if __name__ == "__main__":
    unittest.main()
