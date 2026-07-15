import unittest

from app.services.lightspeed_gateway import FakeLightspeedGateway
from app.services.po_service import reconcile_recommendations, preview_draft


def _order(order_id, state, item_id=None, quantity=0):
    ordered = state in {"ordered", "partially_received"}
    received = 1 if state == "partially_received" else 0
    lines = []
    if item_id is not None:
        lines.append({
            "orderLineID": f"line-{order_id}", "itemID": str(item_id),
            "quantity": quantity, "numReceived": received,
        })
    return {
        "orderID": str(order_id), "vendorID": "55", "shopID": "3",
        "complete": "false", "archived": "false",
        "orderedDate": "2026-01-01" if ordered else None,
        "OrderLine": lines,
    }


def _rec(item_id="100", qty=4):
    return {
        "system_id": str(item_id), "sku": f"SKU-{item_id}",
        "vendor_id": "55", "vendor": "Vendor 55", "location": "Bici Adanac",
        "location_id": "3", "qty_to_order": qty, "unit_cost": 10.0,
    }


class ReconciliationSafetyTest(unittest.TestCase):
    def test_only_unsent_po_is_appendable(self):
        gateway = FakeLightspeedGateway([
            _order("ordered", "ordered", item_id="100", quantity=9),
            _order("draft", "unsent", item_id="100", quantity=2),
        ])
        drafts = reconcile_recommendations([_rec()], gateway)
        line = drafts[0]["lines"][0]
        self.assertEqual(line["reconciliation"], "append_to_open_po")
        self.assertEqual(line["target_lightspeed_order_id"], "draft")

    def test_ordered_po_never_becomes_append_target(self):
        gateway = FakeLightspeedGateway([_order("placed", "ordered", item_id="100", quantity=2)])
        draft = reconcile_recommendations([_rec()], gateway)[0]
        self.assertEqual(draft["lines"][0]["reconciliation"], "new_po")
        self.assertIsNone(draft["lightspeed_order_id"])


class PreviewTest(unittest.TestCase):
    def test_preview_performs_no_operations(self):
        gateway = FakeLightspeedGateway([_order("draft", "unsent", item_id="100", quantity=2)])
        draft = reconcile_recommendations([_rec()], gateway)[0]
        preview = preview_draft(draft, gateway)
        self.assertFalse(preview["writes_performed"])
        self.assertEqual(gateway.operations, [])
        op = preview["operations"][0]
        self.assertEqual(op["action"], "update_order_line")
        self.assertEqual(op["resulting_quantity"], 6)

    def test_preview_new_po_is_explicitly_unsent(self):
        gateway = FakeLightspeedGateway([_order("placed", "ordered")])
        draft = reconcile_recommendations([_rec("200", 3)], gateway)[0]
        preview = preview_draft(draft, gateway)
        self.assertEqual(preview["operations"][0], {
            "action": "create_unsent_order", "vendor_id": "55", "shop_id": "3",
            "ordered_date": None,
        })
        self.assertEqual(preview["operations"][1]["order_id"], "$NEW_ORDER_ID")
        self.assertEqual(preview["read_only_inbound_orders"][0]["state"], "ordered")


if __name__ == "__main__":
    unittest.main()
