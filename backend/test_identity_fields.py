import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import build_lightspeed_item_url
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
        "total_units_sold_30": 30,
        "total_units_sold_60": 60,
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


if __name__ == "__main__":
    unittest.main()
