"""Offline API-boundary tests for special-order actor and batch semantics."""

import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks, Request
from fastapi.exceptions import HTTPException

from app import main
from app.services import bigquery_sync


def _request(email="real.user@example.com"):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-user-email", email.encode("utf-8"))],
    })


class SpecialOrderApiBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.cache = main._special_orders_cache
        main._special_orders_cache = {"data": {"orders": []}, "fetched_at": 0.0}

    def tearDown(self):
        main._special_orders_cache = self.cache

    def test_manual_match_uses_proxy_actor_not_spoofable_body_identity(self):
        payload = {
            "special_order_id": "SO-1",
            "shopify_order_id": "99",
            "updated_by": "spoof@example.com",
        }
        with patch.object(main, "_save_so_match_decisions") as save:
            main.match_special_order_manual(payload, _request())
        decisions, actor = save.call_args.args
        self.assertEqual(actor, "real.user@example.com")
        self.assertEqual(decisions[0]["action"], "link")

    def test_match_decision_batch_validates_before_one_save(self):
        payload = {"decisions": [
            {"special_order_id": "SO-1", "shopify_order_id": "1", "action": "unlink"},
            {"special_order_id": "SO-1", "shopify_order_id": "2", "action": "unlink"},
        ]}
        with patch.object(main, "_save_so_match_decisions") as save:
            result = main.save_special_order_match_decisions(payload, _request())
        self.assertEqual(result["saved"], 2)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[1], "real.user@example.com")

    def test_eta_audit_uses_proxy_actor(self):
        logs = []

        class Shopify:
            def set_order_eta(self, _order_id, _eta):
                return None

            def delete_order_eta(self, _order_id):
                return None

        with patch("app.services.shopify_client.ShopifyClient", return_value=Shopify()), \
             patch("app.services.special_order_service.invalidate_shopify_cache"), \
             patch.object(main, "_refresh_special_orders_after_write"), \
             patch("app.services.bigquery_sync.log_shopify_eta_writeback", side_effect=logs.append):
            main.update_special_order_eta({
                "shopify_order_id": "99",
                "eta": "2026-09-01",
                "updated_by": "spoof@example.com",
            }, _request())
        self.assertEqual(logs[-1]["triggered_by"], "real.user@example.com")

    def test_live_window_zero_reports_full_population(self):
        view = {
            "orders": [
                {"special_order_id": "live", "days_since_creation": 1},
                {"special_order_id": "old", "days_since_creation": 900},
            ],
            "summary": {}, "shopify_only": [], "meta": {}, "fetched_at": "now",
        }
        with patch.object(main, "_sla_view", return_value=view):
            result = main.get_special_order_escalations(
                BackgroundTasks(), refresh=False, live_only_days=0
            )
        self.assertEqual(len(result["orders"]), 2)
        self.assertIsNone(result["meta"]["live_only_days"])
        self.assertEqual(result["meta"]["total_before_window"], 2)
        self.assertEqual(result["meta"]["total_after_window"], 2)

    def test_service_promise_is_workorder_scoped_and_uses_proxy_actor(self):
        main._special_orders_cache["data"]["orders"] = [{
            "special_order_id": "SO-1", "source": "workorder", "workorder_id": "WO-1",
        }]
        calls = []

        class Store:
            def active_service_promises(self):
                return {}

            def set_service_promise(self, special_order_id, promise_date, recorded_by=None):
                calls.append((special_order_id, promise_date, recorded_by))
                return {
                    "promise_date": promise_date,
                    "promise_source": "service_manual",
                }

        with patch("app.services.planning_store.PlanningStore", return_value=Store()):
            result = main.update_service_parts_promise(
                "SO-1", {"promise_date": "2026-09-20", "updated_by": "spoof@example.com"},
                _request(),
            )
        self.assertEqual(result["service_promise_date"], "2026-09-20")
        self.assertEqual(calls, [("SO-1", "2026-09-20", "real.user@example.com")])

    def test_shopify_lookup_returns_502_when_search_source_is_unavailable(self):
        class Shopify:
            def search_orders(self, *_args, **_kwargs):
                raise RuntimeError("transport down")

        with patch("app.services.shopify_client.ShopifyClient", return_value=Shopify()):
            with self.assertRaises(HTTPException) as raised:
                main.lookup_shopify_order("#123")
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("unavailable", raised.exception.detail.lower())

    def test_activity_separates_po_receiving_from_individual_so_receipt(self):
        class Store:
            def list_so_promises(self, _special_order_id):
                return []

            def list_so_activity(self, _special_order_id):
                return []

            def list_so_acks(self):
                return {}

        row = {
            "special_order_id": "SO-1",
            "created_date": "2026-08-01",
            "po_received_date": "2026-08-20",
            "so_received": False,
            "so_received_date": None,
            "receiving_state": "po_receiving",
        }
        main._special_orders_cache["data"]["orders"] = [row]
        with patch("app.services.planning_store.PlanningStore", return_value=Store()):
            activity = main.get_special_order_activity("SO-1")["activity"]

        self.assertFalse(any(event["type"] == "received" for event in activity))
        po_event = next(event for event in activity if event["type"] == "po_receiving")
        self.assertEqual(po_event["timestamp"], "2026-08-20")
        self.assertEqual(po_event["details"]["receiving_state"], "po_receiving")
        self.assertFalse(po_event["details"]["so_received"])
        self.assertEqual(
            po_event["details"]["exception"], "backorder_or_split_shipment"
        )
        self.assertIn("remains unreceived", po_event["label"])

        row.update({
            "so_received": True,
            "so_received_date": "2026-08-21",
            "receiving_state": "so_received",
        })
        with patch("app.services.planning_store.PlanningStore", return_value=Store()):
            activity = main.get_special_order_activity("SO-1")["activity"]
        receipt = next(event for event in activity if event["type"] == "received")
        self.assertEqual(receipt["timestamp"], "2026-08-21")
        self.assertEqual(receipt["label"], "Special order checked in")


class BigQueryMatchBatchTest(unittest.TestCase):
    def test_one_load_job_contains_the_complete_decision_batch(self):
        class Job:
            def result(self):
                return None

        class Client:
            def __init__(self):
                self.rows = None

            def load_table_from_json(self, rows, _table, job_config=None):
                self.rows = rows
                return Job()

        client = Client()
        decisions = [
            {"special_order_id": "SO-1", "shopify_order_id": "1", "action": "unlink"},
            {"special_order_id": "SO-1", "shopify_order_id": "2", "action": "unlink"},
        ]
        with patch.object(bigquery_sync, "ensure_so_match_overrides_table"), \
             patch.object(bigquery_sync, "get_bq_client", return_value=client):
            bigquery_sync.save_so_match_overrides(decisions, created_by="real.user@example.com")
        self.assertEqual(len(client.rows), 2)
        self.assertTrue(all(r["created_by"] == "real.user@example.com" for r in client.rows))


if __name__ == "__main__":
    unittest.main()
