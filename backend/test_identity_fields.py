import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import build_lightspeed_item_url, get_replenishment_data
from app.services.replenishment_engine import process_recommendations


def item_row(item_id, sku, vendor_id, vendor="Shimano", brand="Shimano", location_id=3):
    return {
        "item_id": item_id,
        "sku": sku,
        "description": f"Item {sku}",
        "brand": brand,
        "category": "Components",
        "vendor": vendor,
        "vendor_id": vendor_id,
        "location_id": location_id,
        "total_units_sold_14": 14,
        "total_units_sold_30": 30,
        "total_units_sold_60": 60,
        "days_out_of_stock_14": 0,
        "days_out_of_stock_30": 0,
        "days_out_of_stock_60": 0,
        "current_qoh": 0,
        "on_order": 0,
        "current_reorder_point": 0,
        "current_desired_level": 0,
    }


class IdentityFieldsTest(unittest.TestCase):
    def process(self, items, lead_times):
        return process_recommendations(
            items,
            lead_times,
            safety_days=0,
            override_forecast=60,
            growth_multiplier=1,
            recent_30d_weight=0.5,
            adjustment_mode="shrink",
        )

    def test_vendor_id_and_location_determine_lead_time(self):
        rows = self.process(
            [item_row(1001, "210000030636", 55), item_row(1002, "210000030637", 55)],
            [{"vendor_id": 55, "location_id": 3, "lead_time_days": 9}],
        )

        self.assertEqual([row["lead_time"] for row in rows], [9, 9])

    def test_same_brand_with_different_vendor_ids_can_have_different_lead_times(self):
        rows = self.process(
            [
                item_row(1001, "210000030636", 55, brand="Shimano"),
                item_row(1002, "210000030637", 99, brand="Shimano"),
            ],
            [
                {"vendor_id": 55, "location_id": 3, "lead_time_days": 7},
                {"vendor_id": 99, "location_id": 3, "lead_time_days": 18},
            ],
        )

        self.assertEqual([row["lead_time"] for row in rows], [7, 18])

    def test_vendor_name_does_not_fallback_for_lead_time(self):
        rows = self.process(
            [item_row(1001, "210000030636", 55, vendor="Shimano")],
            [{"vendor_name": "shimano", "location_id": 3, "lead_time_days": 6}],
        )

        self.assertEqual(rows[0]["lead_time"], 14.0)

    def test_recommendation_payload_carries_explicit_id_fields(self):
        rows = self.process(
            [item_row(1001, "210000030636", 55, vendor="Shimano Canada")],
            [{"vendor_id": 55, "location_id": 3, "lead_time_days": 8}],
        )

        self.assertEqual(rows[0]["lightspeed_item_id"], 1001)
        self.assertEqual(rows[0]["system_id"], 1001)
        self.assertEqual(rows[0]["sku"], "210000030636")
        self.assertEqual(rows[0]["vendor_id"], 55)
        self.assertEqual(rows[0]["vendor"], "Shimano Canada")

    def test_lightspeed_url_uses_internal_item_id_directly(self):
        self.assertEqual(
            build_lightspeed_item_url("79637"),
            "https://us.merchantos.com/?name=item.views.item&form_name=view&id=79637&tab=details",
        )

    def test_negative_qoh_is_visible_but_not_used_as_deficit(self):
        row = item_row(1001, "NEG-QOH", 55)
        row["current_qoh"] = -5
        rows = self.process([row], [{"vendor_id": 55, "location_id": 3, "lead_time_days": 14}])

        self.assertEqual(rows[0]["on_hand"], -5)
        self.assertEqual(rows[0]["effective_on_hand"], 0)
        self.assertTrue(rows[0]["qoh_adjusted_for_math"])
        self.assertEqual(rows[0]["inventory_position"], 0)
        self.assertEqual(rows[0]["recommended_desired_level"], 60)
        self.assertEqual(rows[0]["qty_to_order"], 60)
        self.assertEqual(rows[0]["days_stock"], 0)

    def test_override_recalculation_uses_effective_qoh(self):
        class FakeFrame:
            def __init__(self, rows):
                self.rows = rows

            def to_dict(self, orient):
                if orient != "records":
                    raise ValueError(f"Unexpected orient: {orient}")
                return self.rows

        row = item_row(1001, "NEG-QOH", 55)
        row["current_qoh"] = -5

        with patch("app.services.bigquery_sync.fetch_tagged_items_metrics", return_value=FakeFrame([row])), \
             patch("app.services.bigquery_sync.fetch_lead_times", return_value=FakeFrame([{"vendor_id": 55, "location_id": 3, "lead_time_days": 14}])), \
             patch("app.main.get_sku_overrides", return_value={"NEG-QOH_Bici Adanac": {"manual_desired_level": 40}}), \
             patch("app.main.log_recommendation_run"), \
             patch("app.main.log_velocity_snapshots"):
            response = get_replenishment_data(forecast_period=60, safety_days=0)

        rec = response["data"]["Bici Adanac"][0]
        self.assertEqual(rec["on_hand"], -5)
        self.assertEqual(rec["effective_on_hand"], 0)
        self.assertTrue(rec["qoh_adjusted_for_math"])
        self.assertEqual(rec["inventory_position"], 0)
        self.assertEqual(rec["recommended_desired_level"], 40)
        self.assertEqual(rec["qty_to_order"], 40)

    def test_default_api_uses_40_40_20_demand_weighting(self):
        class FakeFrame:
            def __init__(self, rows):
                self.rows = rows

            def to_dict(self, orient):
                if orient != "records":
                    raise ValueError(f"Unexpected orient: {orient}")
                return self.rows

        row = item_row(1001, "WEIGHTED", 55)
        row["total_units_sold_14"] = 28
        row["total_units_sold_30"] = 44
        row["total_units_sold_60"] = 74

        with patch("app.services.bigquery_sync.fetch_tagged_items_metrics", return_value=FakeFrame([row])), \
             patch("app.services.bigquery_sync.fetch_lead_times", return_value=FakeFrame([{"vendor_id": 55, "location_id": 3, "lead_time_days": 14}])), \
             patch("app.main.get_sku_overrides", return_value={}), \
             patch("app.main.log_recommendation_run"), \
             patch("app.main.log_velocity_snapshots"):
            response = get_replenishment_data(forecast_period=60, safety_days=0)

        rec = response["data"]["Bici Adanac"][0]
        self.assertEqual(rec["weight_14d"], 0.4)
        self.assertEqual(rec["weight_15_30d"], 0.4)
        self.assertEqual(rec["weight_31_60d"], 0.2)
        self.assertAlmostEqual(rec["raw_daily_sales"], 1.4)
        self.assertEqual(rec["recommended_desired_level"], 84)

    def test_legacy_recent_30d_weight_still_uses_old_two_window_behavior(self):
        rows = process_recommendations(
            [item_row(1001, "LEGACY", 55)],
            [{"vendor_id": 55, "location_id": 3, "lead_time_days": 8}],
            safety_days=0,
            override_forecast=60,
            recent_30d_weight=0.25,
            adjustment_mode="shrink",
        )

        self.assertEqual(rows[0]["weight_14d"], 0)
        self.assertEqual(rows[0]["weight_15_30d"], 0.25)
        self.assertEqual(rows[0]["weight_31_60d"], 0.75)
        self.assertEqual(rows[0]["recommended_desired_level"], 60)

    def test_momentum_statuses_are_classified(self):
        cases = [
            ("surging", 28, 36, 42),
            ("rising", 12, 28, 44),
            ("spiky", 2, 3, 4),
            ("cooling", 7, 31, 91),
            ("flat", 14, 30, 60),
            ("insufficient_data", 0, 0, 0),
        ]

        for expected_status, units_14, units_30, units_60 in cases:
            with self.subTest(expected_status=expected_status):
                row = item_row(1001, expected_status, 55)
                row["total_units_sold_14"] = units_14
                row["total_units_sold_30"] = units_30
                row["total_units_sold_60"] = units_60
                rows = process_recommendations(
                    [row],
                    [{"vendor_id": 55, "location_id": 3, "lead_time_days": 8}],
                    safety_days=0,
                    override_forecast=60,
                    weight_14d=0.4,
                    weight_15_30d=0.4,
                    weight_31_60d=0.2,
                    adjustment_mode="shrink",
                )

                self.assertEqual(rows[0]["momentum_status"], expected_status)
                self.assertIn("momentum_label", rows[0])
                self.assertIn("momentum_reason", rows[0])

    def test_invalid_demand_weights_are_rejected(self):
        with self.assertRaises(ValueError):
            process_recommendations(
                [item_row(1001, "BAD-WEIGHTS", 55)],
                [{"vendor_id": 55, "location_id": 3, "lead_time_days": 8}],
                weight_14d=0.5,
                weight_15_30d=0.5,
                weight_31_60d=0.5,
            )


if __name__ == "__main__":
    unittest.main()
