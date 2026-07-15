import os
import unittest
from unittest.mock import patch

from app.services.lightspeed_client import (
    LightspeedClient,
    LightspeedReadError,
    LightspeedWriteBlocked,
)
from app.services.lightspeed_gateway import FakeLightspeedGateway, LiveLightspeedReadGateway


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "fixture"

    def json(self):
        return self._payload


class LightspeedSafetyTest(unittest.TestCase):
    def test_mutation_is_blocked_before_network(self):
        client = LightspeedClient()
        with patch.dict(os.environ, {
            "LIGHTSPEED_WRITES_ENABLED": "false",
            "LIGHTSPEED_WRITE_APPROVAL_TOKEN": "",
            "LIGHTSPEED_WRITE_SHOP_ALLOWLIST": "",
        }, clear=False), patch("app.services.lightspeed_client.requests.request") as request:
            with self.assertRaises(LightspeedWriteBlocked):
                client._request("POST", "/Order.json", json={"vendorID": "4", "shopID": "3"})
            request.assert_not_called()

    def test_read_gateway_has_no_write_surface(self):
        gateway = LiveLightspeedReadGateway(client=LightspeedClient())
        self.assertFalse(hasattr(gateway, "create_unsent_order"))
        self.assertFalse(hasattr(gateway, "add_order_line"))

    def test_legacy_rop_sync_is_blocked_before_authentication_or_reads(self):
        client = LightspeedClient()
        rec = {"system_id": "37", "sku": "TEST", "location": "Bici Adanac"}
        with patch.dict(os.environ, {"LIGHTSPEED_WRITES_ENABLED": "false"}, clear=False), \
             patch("app.services.lightspeed_client.requests.get") as get, \
             patch("app.services.lightspeed_client.requests.post") as post:
            with self.assertRaises(LightspeedWriteBlocked):
                client.sync_recommendation(rec)
        get.assert_not_called()
        post.assert_not_called()


class PaginationTest(unittest.TestCase):
    def test_loads_every_cursor_page_and_classifies_orders(self):
        client = LightspeedClient()
        first = _Response({
            "@attributes": {"next": "https://api.example.test/orders?after=1"},
            "Order": [{
                "orderID": "1", "complete": "false", "archived": "false",
                "orderedDate": None, "OrderLines": {"OrderLine": []},
            }],
        })
        second = _Response({
            "@attributes": {"next": ""},
            "Order": [{
                "orderID": "2", "complete": "false", "archived": "false",
                "orderedDate": "2026-01-01", "OrderLines": {"OrderLine": {
                    "orderLineID": "20", "itemID": "8", "quantity": "2", "numReceived": "1",
                }},
            }],
        })
        with patch.object(client, "_request", return_value=first), \
             patch.object(client, "_request_absolute", return_value=second) as next_request:
            orders = client.get_open_orders()
        self.assertEqual([o["orderID"] for o in orders], ["1", "2"])
        self.assertEqual(orders[0]["po_state"], "unsent")
        self.assertEqual(orders[1]["po_state"], "partially_received")
        self.assertEqual(len(orders[1]["OrderLine"]), 1)
        next_request.assert_called_once_with("GET", "https://api.example.test/orders?after=1")

    def test_failed_second_page_is_fail_closed(self):
        client = LightspeedClient()
        first = _Response({"@attributes": {"next": "https://api.example.test/p2"}, "Order": []})
        with patch.object(client, "_request", return_value=first), \
             patch.object(client, "_request_absolute", return_value=None):
            with self.assertRaises(LightspeedReadError):
                client.get_open_orders()


class FakeGatewayTest(unittest.TestCase):
    def test_fake_writes_stay_in_memory_and_create_unsent_order(self):
        gateway = FakeLightspeedGateway()
        order = gateway.create_unsent_order("9", "3")
        line = gateway.add_order_line(order["orderID"], "44", 5, 12.5)
        self.assertEqual(order["po_state"], "unsent")
        self.assertIsNone(order["orderedDate"])
        self.assertEqual(line["quantity"], 5)
        self.assertEqual(len(gateway.operations), 2)


if __name__ == "__main__":
    unittest.main()
