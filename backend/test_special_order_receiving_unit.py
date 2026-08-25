"""Offline tests for individual-SO receipt versus PO-wide receiving progress."""

import unittest
from datetime import date

from app.services import so_scoreboard_service, so_sla_service, special_order_service


TODAY = date(2026, 8, 21)


def _raw_so(*, status="Ordered", completed="false", timestamp="2026-08-21T10:00:00+00:00"):
    return {
        "specialOrderID": "SO-1",
        "status": status,
        "completed": completed,
        "contacted": "false",
        "shopID": "3",
        "timeStamp": timestamp,
        "SaleLine": {
            "saleLineID": "SL-1",
            "createTime": "2026-08-01T09:00:00+00:00",
            "Item": {"itemID": "ITEM-1", "systemSku": "210000000001"},
        },
        "OrderLine": {"orderID": "PO-1", "orderLineID": "POL-1"},
    }


def _po(*, receiving_started=False, received_date=None, complete=False):
    return {
        "orderedDate": "2026-08-10",
        "arrivalDate": "2026-08-25",
        "createTime": "2026-08-05",
        "receivedDate": received_date,
        "complete": complete,
        "received_started": receiving_started,
        "vendor_id": "V-1",
        "vendor_name": "Vendor",
        "order_type": "Replenishment",
    }


def _normalize(so, po):
    return special_order_service._normalize(
        so,
        {"PO-1": po},
        {},
        {"3": "Bici Adanac"},
        TODAY,
        sourcing_ctx={},
    )


class SpecialOrderReceivingContractTest(unittest.TestCase):
    def test_negative_status_text_is_never_mistaken_for_receipt(self):
        for status in ("Not Ready", "Not in stock", "Backordered", "Already ordered"):
            self.assertFalse(special_order_service._status_is_received(status), status)

    def test_po_line_or_header_receiving_does_not_mark_the_so_received(self):
        line_progress = _normalize(_raw_so(), _po(receiving_started=True))
        self.assertFalse(line_progress["so_received"])
        self.assertEqual(line_progress["receiving_state"], "po_receiving")
        self.assertEqual(line_progress["procurement_stage"], "ordered")

        header_progress = _normalize(
            _raw_so(), _po(received_date="2026-08-20", receiving_started=False)
        )
        self.assertFalse(header_progress["so_received"])
        self.assertEqual(header_progress["receiving_state"], "po_receiving")
        self.assertEqual(header_progress["po_received_date"], "2026-08-20")
        self.assertIsNone(header_progress["so_received_date"])

    def test_complete_po_with_unreceived_so_is_an_explicit_exception(self):
        row = _normalize(
            _raw_so(completed="true"),
            _po(receiving_started=True, received_date="2026-08-20", complete=True),
        )
        # SpecialOrder.completed is lifecycle context, not proof of individual receipt.
        self.assertTrue(row["completed"])
        self.assertFalse(row["so_received"])
        self.assertEqual(row["receiving_state"], "po_complete_so_unreceived")
        self.assertEqual(row["procurement_stage"], "ordered")

    def test_individual_status_is_authoritative_and_has_its_own_date(self):
        row = _normalize(
            _raw_so(status="Ready for Pickup", timestamp="2026-08-21T10:00:00+00:00"),
            _po(receiving_started=True, received_date="2026-08-19", complete=True),
        )
        self.assertTrue(row["so_received"])
        self.assertEqual(row["receiving_state"], "so_received")
        self.assertEqual(row["procurement_stage"], "received")
        self.assertEqual(row["so_received_date"], "2026-08-21")
        self.assertEqual(row["po_received_date"], "2026-08-19")

    def test_split_or_backorder_state_is_actionable_vendor_followup(self):
        for state in ("po_receiving", "po_complete_so_unreceived"):
            row = _normalize(
                _raw_so(),
                _po(
                    receiving_started=True,
                    received_date="2026-08-20",
                    complete=state == "po_complete_so_unreceived",
                ),
            )
            result = so_sla_service.build_escalations([row], {}, TODAY)["orders"][0]
            self.assertEqual(result["receiving_state"], state)
            self.assertEqual(result["work_state"], "vendor_followup")
            self.assertIn("vendor_followup", result["queue_states"])
            self.assertTrue(result["actionable"])
            self.assertEqual(result["action_owner"], "procurement")
            self.assertEqual(result["action_due_date"], TODAY.isoformat())
            # Wording is state-specific now: a complete PO missing this line reads as a
            # backorder, a part-received one as a split shipment. Both must name the ETA update.
            expected = "backordered" if state == "po_complete_so_unreceived" else "this shipment"
            self.assertIn(expected, result["next_action"])
            self.assertIn("procurement ETA update", result["next_action"])

    def test_live_on_time_score_uses_individual_receipt_not_po_header_date(self):
        promise = [{
            "special_order_id": "SO-1",
            "promise_date": "2026-08-20",
            "revision_index": 0,
        }]

        unreceived = _normalize(
            _raw_so(),
            _po(receiving_started=True, received_date="2026-08-19"),
        )
        unreceived["shopify_expected_date"] = "2026-08-20"
        open_score = so_scoreboard_service.build_scoreboard(
            [unreceived], {}, promise, TODAY
        )["promise"]
        self.assertEqual(open_score["met"], 0)
        self.assertEqual(open_score["breached_outstanding"], 1)

        received = _normalize(
            _raw_so(status="Ready for Pickup", timestamp="2026-08-21T10:00:00+00:00"),
            _po(receiving_started=True, received_date="2026-08-19"),
        )
        received["shopify_expected_date"] = "2026-08-20"
        settled_score = so_scoreboard_service.build_scoreboard(
            [received], {}, promise, TODAY
        )["promise"]
        self.assertEqual(settled_score["met"], 0)
        self.assertEqual(settled_score["missed"], 1)


if __name__ == "__main__":
    unittest.main()
