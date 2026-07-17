import unittest
from datetime import date, timedelta

from app.services.demand_planning import week_start
from app.services.planning_service import (
    clear_run_cache,
    create_planning_run,
    get_forecast,
    get_recommendations,
    get_run_cache_info,
)


class PlanningServiceTests(unittest.TestCase):
    def setUp(self):
        clear_run_cache()

    def _rows(self):
        end = week_start(date(2026, 7, 14)) - timedelta(days=7)
        rows = []
        for index in range(110):
            rows.append({
                "item_id": "10",
                "location_id": "3",
                "week_start": (end - timedelta(days=(109 - index) * 7)).isoformat(),
                "raw_units_sold": 2 + (index % 4),
                "category_path": "Nutrition",
            })
        return rows

    def test_run_has_lineage_forecast_and_financial_rollups(self):
        run = create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "sku": "SKU-10",
                "description": "Gel", "category": "Nutrition", "vendor_id": "55",
                "vendor": "Fuel Co", "current_qoh": 0, "landed_cost": 2,
                "selling_price": 4, "case_pack": 12,
            }],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 14),
            persist=False,
        )
        self.assertEqual(run["status"], "complete")
        rec = run["recommendations"][0]
        self.assertEqual(rec["run_id"], run["run_id"])
        self.assertEqual(len(rec["forecast"]), 52)
        self.assertEqual(run["config"]["order_coverage_weeks"], 8)
        self.assertEqual(rec["order_coverage_weeks"], 8)
        self.assertFalse(rec["blocked"])
        self.assertGreaterEqual(rec["recommended_quantity"] % 12, 0)
        self.assertTrue(run["monthly_rollups"])
        self.assertEqual(get_forecast(run["run_id"], "10", "3")["sku"], "SKU-10")

    def test_missing_cost_remains_blocking(self):
        run = create_planning_run(
            items=[{"item_id": "10", "location_id": "3", "category": "Nutrition", "vendor_id": "55"}],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 14),
            persist=False,
        )
        rec = get_recommendations(run["run_id"])[0]
        self.assertTrue(rec["blocked"])
        self.assertIsNone(rec["purchase_commitment_spend"])
        self.assertIn("missing_landed_cost", rec["reason_codes"])

    def test_on_order_is_scheduled_not_immediately_added(self):
        run = create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "category": "Nutrition",
                "vendor_id": "55", "landed_cost": 2, "on_order": 20, "lead_time_days": 21,
            }],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 14),
            horizon_weeks=13,
            persist=False,
        )
        rec = run["recommendations"][0]
        self.assertEqual(rec["scheduled_receipts"][0]["quantity"], 20)
        self.assertEqual(rec["scheduled_receipts"][0]["confidence"], "estimated")
        self.assertEqual(rec["inventory_projection"][0]["scheduled_receipts"], 0)

    def test_completed_runs_do_not_accumulate_in_process_memory(self):
        first = create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "category": "Nutrition",
                "vendor_id": "55", "landed_cost": 2,
            }],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 14),
            persist=False,
        )
        second = create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "category": "Nutrition",
                "vendor_id": "55", "landed_cost": 2,
            }],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 21),
            persist=False,
        )

        cache = get_run_cache_info()
        self.assertEqual(cache["size"], 1)
        self.assertEqual(cache["max_size"], 1)
        self.assertEqual(cache["run_ids"], [second["run_id"]])
        self.assertNotIn(first["run_id"], cache["run_ids"])

    def test_legacy_review_period_maps_to_po_coverage(self):
        run = create_planning_run(
            items=[{
                "item_id": "10", "location_id": "3", "category": "Nutrition",
                "vendor_id": "55", "landed_cost": 2,
            }],
            weekly_history=self._rows(),
            as_of_date=date(2026, 7, 14),
            config={"review_period_weeks": 6},
            persist=False,
        )
        self.assertEqual(run["config"]["order_coverage_weeks"], 6)
        self.assertEqual(run["recommendations"][0]["order_coverage_weeks"], 6)


if __name__ == "__main__":
    unittest.main()
