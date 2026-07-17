import unittest
from datetime import date

from app.services.demand_planning import (
    build_purchase_recommendation,
    classify_demand,
    exposure_adjusted_seasonal_profile,
    forecast_metrics,
    monthly_rollups,
    probabilistic_forecast,
    project_inventory_and_order,
    round_order_constraints,
    select_champion,
    week_start,
)


class ExposureAdjustedSeasonalityTest(unittest.TestCase):
    def test_unequal_year_exposure_does_not_create_false_seasonality(self):
        records = [
            {"sales_year": 2024, "week_of_year": 1, "units": 10, "observed": True},
            {"sales_year": 2024, "week_of_year": 2, "units": 10, "observed": True},
            {"sales_year": 2025, "week_of_year": 1, "units": 10, "observed": True},
        ]
        profile = exposure_adjusted_seasonal_profile(
            records, num_periods=2, smoothing_window=1, shrinkage=0
        )
        self.assertAlmostEqual(profile[1], 1.0)
        self.assertAlmostEqual(profile[2], 1.0)

    def test_unobserved_partial_period_is_ignored(self):
        records = [
            {"sales_year": 2024, "week_of_year": 1, "units": 10, "observed": True},
            {"sales_year": 2024, "week_of_year": 2, "units": 0, "observed": False},
        ]
        profile = exposure_adjusted_seasonal_profile(records, num_periods=2, smoothing_window=1)
        self.assertAlmostEqual(sum(profile.values()) / 2, 1.0)


class DemandClassificationTest(unittest.TestCase):
    def test_intermittent_and_dormant(self):
        intermittent = classify_demand([0, 0, 2, 0, 0, 2, 0, 0, 2, 0, 0, 2])
        self.assertIn(intermittent["demand_class"], {"intermittent", "lumpy"})
        dormant = classify_demand([2] * 13 + [0] * 13)
        self.assertEqual(dormant["lifecycle"], "dormant")


class ModelSelectionTest(unittest.TestCase):
    def test_backtest_publishes_metrics_and_baseline(self):
        series = [5.0] * 120
        selection = select_champion(series, {week: 1.0 for week in range(1, 53)})
        self.assertIn(selection["champion"], selection["candidates"])
        self.assertIn("seasonal_naive", selection["candidates"])
        self.assertIn("wape", selection["candidates"][selection["champion"]]["metrics"])
        self.assertIn("ets_damped", selection["candidates"])

    def test_metrics_report_signed_bias(self):
        metrics = forecast_metrics([10, 10], [12, 12], [8, 10, 12])
        self.assertGreater(metrics["bias"], 0)
        self.assertAlmostEqual(metrics["wape"], 0.2)

    def test_buyer_can_force_a_challenger_model(self):
        selection = select_champion([0, 3] * 30, forced_model="tsb")
        self.assertEqual(selection["champion"], "tsb")


class ProbabilityAndInventoryTest(unittest.TestCase):
    def test_quantiles_are_monotonic(self):
        forecast = probabilistic_forecast([4, 4], [-2, -1, 0, 2, 5])
        for point in forecast:
            self.assertLessEqual(point["p50"], point["p80"])
            self.assertLessEqual(point["p80"], point["p90"])
            self.assertLessEqual(point["p90"], point["p95"])

    def test_receipts_are_applied_in_their_week_not_immediately(self):
        forecast = [{"p50": 5, "p90": 7}] * 4
        result = project_inventory_and_order(
            date(2026, 7, 14), forecast, on_hand=6,
            scheduled_receipts=[{"week_start": "2026-07-27", "quantity": 10}],
            lead_time_weeks=2, review_period_weeks=1,
        )
        self.assertEqual(result["projection"][0]["scheduled_receipts"], 0)
        self.assertEqual(result["projection"][1]["scheduled_receipts"], 10)
        self.assertEqual(result["need_by_week"], "2026-07-20")

    def test_incoming_is_netted_across_the_full_po_protection_horizon(self):
        forecast = [{"p50": 8, "p90": 10}] * 8
        result = project_inventory_and_order(
            date(2026, 7, 14), forecast, on_hand=0,
            scheduled_receipts=[
                # After the two-week lead time, but inside the six-week
                # protection horizon (two lead + four coverage).
                {"week_start": "2026-08-17", "quantity": 15},
                # Outside the protection horizon; it must not reduce this PO.
                {"week_start": "2026-09-07", "quantity": 20},
            ],
            lead_time_weeks=2,
            order_coverage_weeks=4,
        )
        self.assertEqual(result["protection_horizon_weeks"], 6)
        self.assertEqual(result["incoming_within_protection"], 15)
        self.assertEqual(result["target_protection_demand"], 60)
        self.assertEqual(result["unconstrained_quantity"], 45)

    def test_case_pack_and_moq_round_after_unconstrained_need(self):
        result = round_order_constraints(7.2, case_pack=6, moq=18)
        self.assertEqual(result["unconstrained_quantity"], 8)
        self.assertEqual(result["rounded_quantity"], 18)
        self.assertEqual(result["constraint_extra_units"], 10)

    def test_week_start_is_monday(self):
        self.assertEqual(week_start("2026-07-14").isoformat(), "2026-07-13")

    def test_service_target_changes_inventory_math(self):
        forecast = [{"p50": 2, "p80": 3, "p90": 5, "p95": 8}] * 3
        p80 = project_inventory_and_order(date(2026, 7, 14), forecast, 0, [], 1, 1, service_quantile=.8)
        p95 = project_inventory_and_order(date(2026, 7, 14), forecast, 0, [], 1, 1, service_quantile=.95)
        self.assertGreater(p95["rounded_quantity"], p80["rounded_quantity"])


class RecommendationTest(unittest.TestCase):
    def _item(self, **overrides):
        item = {
            "item_id": "1", "sku": "SKU-1", "description": "Item",
            "category": "Nutrition", "location_id": "3", "location": "Bici Adanac",
            "vendor_id": "55", "vendor": "Vendor", "on_hand": 2,
            "lead_time_days": 14, "landed_cost": 10.0, "selling_price": 20.0,
            "case_pack": 2, "moq": 0,
        }
        item.update(overrides)
        return item

    def test_recommendation_contains_financials_lineage_and_quantiles(self):
        rec = build_purchase_recommendation(
            self._item(), [3.0] * 60, {week: 1.0 for week in range(1, 53)},
            date(2026, 7, 14), horizon_weeks=8, run_id="run-1",
        )
        self.assertEqual(rec["run_id"], "run-1")
        self.assertEqual(len(rec["forecast"]), 8)
        self.assertIsNotNone(rec["purchase_commitment_spend"])
        self.assertFalse(rec["blocked"])

    def test_missing_cost_blocks_recommendation(self):
        rec = build_purchase_recommendation(
            self._item(landed_cost=None), [2.0] * 30, None,
            date(2026, 7, 14), horizon_weeks=4,
        )
        self.assertTrue(rec["blocked"])
        self.assertIsNone(rec["purchase_commitment_spend"])
        self.assertIn("missing_landed_cost", rec["reason_codes"])

    def test_monthly_rollup_keeps_missing_cost_visible(self):
        priced = build_purchase_recommendation(
            self._item(), [2.0] * 30, None, date(2026, 7, 14), horizon_weeks=4,
        )
        unpriced = build_purchase_recommendation(
            self._item(item_id="2", sku="SKU-2", landed_cost=None), [1.0] * 30,
            None, date(2026, 7, 14), horizon_weeks=4,
        )
        rollups = monthly_rollups([priced, unpriced])
        self.assertTrue(rollups[0]["missing_cogs"])
        self.assertGreater(rollups[0]["units"], 0)


if __name__ == "__main__":
    unittest.main()
