"""Safe purchase-order boundary around the Lightspeed R-Series client.

Application code depends on this gateway instead of making raw mutation calls.
The live read adapter is fail-closed and paginated by ``LightspeedClient``. The
fake adapter is the only adapter used by automated PO tests. Live mutation
methods remain behind the process-level gate in ``lightspeed_client``.
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol

from app.services.lightspeed_client import (
    LightspeedClient,
    LightspeedReadError,
    LightspeedWriteBlocked,
)


class LightspeedGateway(Protocol):
    shop_id_map: Dict[str, str]

    def list_purchase_orders(
        self, vendor_id: Optional[str] = None, shop_id: Optional[str] = None,
        include_lines: bool = True,
    ) -> List[Dict[str, Any]]: ...

    def create_unsent_order(self, vendor_id: str, shop_id: str) -> Dict[str, Any]: ...

    def add_order_line(
        self, order_id: str, item_id: str, quantity: int, unit_cost: Optional[float] = None
    ) -> Dict[str, Any]: ...

    def update_order_line(self, order_line_id: str, quantity: int) -> Dict[str, Any]: ...


class LiveLightspeedReadGateway:
    """Production read adapter. It intentionally exposes no mutation methods."""

    def __init__(self, client: Optional[LightspeedClient] = None):
        self.client = client or LightspeedClient()
        self.shop_id_map = self.client.shop_id_map

    def list_purchase_orders(
        self, vendor_id: Optional[str] = None, shop_id: Optional[str] = None,
        include_lines: bool = True,
    ) -> List[Dict[str, Any]]:
        return self.client.get_open_orders(
            vendor_id=vendor_id, shop_id=shop_id, include_lines=include_lines
        )


class LiveLightspeedWriteGateway(LiveLightspeedReadGateway):
    """Live mutation adapter; every call is guarded again by LightspeedClient."""

    def create_unsent_order(self, vendor_id: str, shop_id: str) -> Dict[str, Any]:
        # Deliberately omit orderedDate: BICI may prepare an unsent PO but must
        # never place/send it to the vendor automatically.
        result = self.client.create_order(vendor_id, shop_id, ordered_date=None)
        if not result:
            raise LightspeedReadError("Lightspeed did not return the created unsent order.")
        return result

    def add_order_line(
        self, order_id: str, item_id: str, quantity: int, unit_cost: Optional[float] = None
    ) -> Dict[str, Any]:
        result = self.client.add_order_line(order_id, item_id, quantity, unit_cost)
        if not result:
            raise LightspeedReadError("Lightspeed did not return the created order line.")
        return result

    def update_order_line(self, order_line_id: str, quantity: int) -> Dict[str, Any]:
        result = self.client.update_order_line(order_line_id, quantity)
        if not result:
            raise LightspeedReadError("Lightspeed did not return the updated order line.")
        return result


class FakeLightspeedGateway:
    """In-memory gateway for tests and demos. It never performs network I/O."""

    shop_id_map = {"Victoria": "2", "Bici Adanac": "3", "Langford": "20"}

    def __init__(self, orders: Optional[List[Dict[str, Any]]] = None):
        self.orders = deepcopy(orders or [])
        self.operations: List[Dict[str, Any]] = []
        self._next_order = 1000
        self._next_line = 5000
        for order in self.orders:
            self._normalize(order)

    @staticmethod
    def _normalize(order: Dict[str, Any]) -> None:
        lines = order.get("OrderLine")
        if lines is None:
            lines = (order.get("OrderLines") or {}).get("OrderLine", [])
        if isinstance(lines, dict):
            lines = [lines]
        order["OrderLine"] = lines or []
        order["po_state"] = LightspeedClient.classify_purchase_order(order)

    def list_purchase_orders(
        self, vendor_id: Optional[str] = None, shop_id: Optional[str] = None,
        include_lines: bool = True,
    ) -> List[Dict[str, Any]]:
        result = []
        for order in self.orders:
            if vendor_id is not None and str(order.get("vendorID")) != str(vendor_id):
                continue
            if shop_id is not None and str(order.get("shopID")) != str(shop_id):
                continue
            copied = deepcopy(order)
            if not include_lines:
                copied["OrderLine"] = []
            result.append(copied)
        return result

    def create_unsent_order(self, vendor_id: str, shop_id: str) -> Dict[str, Any]:
        self._next_order += 1
        order = {
            "orderID": str(self._next_order),
            "vendorID": str(vendor_id),
            "shopID": str(shop_id),
            "complete": "false",
            "archived": "false",
            "orderedDate": None,
            "OrderLine": [],
            "po_state": "unsent",
        }
        self.orders.append(order)
        self.operations.append({"action": "create_unsent_order", "orderID": order["orderID"]})
        return deepcopy(order)

    def add_order_line(
        self, order_id: str, item_id: str, quantity: int, unit_cost: Optional[float] = None
    ) -> Dict[str, Any]:
        order = self._find_order(order_id)
        self._next_line += 1
        line = {
            "orderLineID": str(self._next_line),
            "itemID": str(item_id),
            "quantity": int(quantity),
            "price": unit_cost,
            "numReceived": 0,
        }
        order["OrderLine"].append(line)
        self.operations.append({"action": "add_order_line", **deepcopy(line)})
        return deepcopy(line)

    def update_order_line(self, order_line_id: str, quantity: int) -> Dict[str, Any]:
        for order in self.orders:
            for line in order.get("OrderLine", []):
                if str(line.get("orderLineID")) == str(order_line_id):
                    line["quantity"] = int(quantity)
                    self.operations.append({
                        "action": "update_order_line",
                        "orderLineID": str(order_line_id),
                        "quantity": int(quantity),
                    })
                    return deepcopy(line)
        raise KeyError(f"Unknown fake order line {order_line_id}")

    def _find_order(self, order_id: str) -> Dict[str, Any]:
        for order in self.orders:
            if str(order.get("orderID")) == str(order_id):
                return order
        raise KeyError(f"Unknown fake order {order_id}")


__all__ = [
    "FakeLightspeedGateway",
    "LightspeedGateway",
    "LightspeedReadError",
    "LightspeedWriteBlocked",
    "LiveLightspeedReadGateway",
    "LiveLightspeedWriteGateway",
]
