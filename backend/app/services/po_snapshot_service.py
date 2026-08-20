"""Shared, read-only Lightspeed purchase-order snapshot for the buyer workbench.

The cache performs one complete paginated Lightspeed walk, then filters that
authoritative snapshot in memory for every vendor/shop selector. Expired or
explicitly refreshed snapshots fail closed if any Lightspeed page cannot load;
stale data is never silently presented as current.
"""

import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.services.lightspeed_gateway import LiveLightspeedReadGateway, LightspeedReadError


DEFAULT_PO_SNAPSHOT_TTL_SECONDS = 300


class PurchaseOrderSnapshotCache:
    def __init__(
        self,
        gateway_factory: Optional[Callable[[], Any]] = None,
        ttl_seconds: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        configured_ttl = ttl_seconds
        if configured_ttl is None:
            try:
                configured_ttl = int(os.getenv(
                    "LS_PO_SNAPSHOT_TTL_SECONDS", str(DEFAULT_PO_SNAPSHOT_TTL_SECONDS)
                ))
            except (TypeError, ValueError):
                configured_ttl = DEFAULT_PO_SNAPSHOT_TTL_SECONDS
        self.ttl_seconds = max(15, min(3600, int(configured_ttl)))
        self.gateway_factory = gateway_factory or LiveLightspeedReadGateway
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._orders: Optional[List[Dict[str, Any]]] = None
        self._snapshot_at_epoch: Optional[float] = None
        self._generation = 0

    def clear(self) -> None:
        with self._lock:
            self._orders = None
            self._snapshot_at_epoch = None
            self._generation += 1

    def _is_fresh(self, now: float) -> bool:
        return (
            self._orders is not None
            and self._snapshot_at_epoch is not None
            and now - self._snapshot_at_epoch < self.ttl_seconds
        )

    @staticmethod
    def _validate_complete_snapshot(orders: Any) -> List[Dict[str, Any]]:
        if not isinstance(orders, list):
            raise LightspeedReadError("Lightspeed purchase-order snapshot was not a list.")
        seen = set()
        for order in orders:
            if not isinstance(order, dict) or order.get("orderID") in (None, ""):
                raise LightspeedReadError("Lightspeed purchase-order snapshot contained an invalid order.")
            order_id = str(order["orderID"])
            if order_id in seen:
                raise LightspeedReadError(
                    f"Lightspeed purchase-order snapshot repeated order {order_id}."
                )
            seen.add(order_id)
        return orders

    def peek_orders(self) -> Optional[List[Dict[str, Any]]]:
        """The cached snapshot if it is already fresh, else None. Never triggers a load.

        A full walk costs ~40s against ~1,850 purchase orders. Callers who merely *benefit* from
        PO data (the special-order recommendation engine) must not be able to impose that on a
        user; they degrade gracefully instead, while the workbench and the startup warmer keep
        the snapshot populated for everyone.
        """
        with self._lock:
            if self._orders is not None and self._is_fresh(self.clock()):
                return deepcopy(self._orders)
        return None

    def get_orders(
        self,
        vendor_id: Optional[str] = None,
        shop_id: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        requested_generation = self._generation
        with self._lock:
            now = self.clock()
            refreshed_while_waiting = (
                force_refresh
                and self._orders is not None
                and self._generation != requested_generation
            )
            cache_hit = refreshed_while_waiting or (
                not force_refresh and self._is_fresh(now)
            )
            if not cache_hit:
                # Deliberately omit filters: one complete paginated snapshot is
                # shared by every vendor/shop workbench card.
                loaded = self.gateway_factory().list_purchase_orders(include_lines=False)
                orders = self._validate_complete_snapshot(loaded)
                self._orders = deepcopy(orders)
                self._snapshot_at_epoch = now
                self._generation += 1

            if self._orders is None or self._snapshot_at_epoch is None:
                raise LightspeedReadError("No complete Lightspeed PO snapshot is available.")

            filtered = [
                order for order in self._orders
                if (vendor_id is None or str(order.get("vendorID")) == str(vendor_id))
                and (shop_id is None or str(order.get("shopID")) == str(shop_id))
            ]
            snapshot_at = datetime.fromtimestamp(
                self._snapshot_at_epoch, tz=timezone.utc
            ).isoformat()
            return {
                "orders": deepcopy(filtered),
                "meta": {
                    "snapshot_at": snapshot_at,
                    "age_seconds": round(max(0.0, now - self._snapshot_at_epoch), 3),
                    "ttl_seconds": self.ttl_seconds,
                    "cache_hit": cache_hit,
                    "complete": True,
                    "includes_lines": False,
                    "total_order_count": len(self._orders),
                    "filtered_order_count": len(filtered),
                },
            }


_po_snapshot_cache: Optional[PurchaseOrderSnapshotCache] = None
_po_snapshot_cache_lock = threading.Lock()


def get_po_snapshot_cache() -> PurchaseOrderSnapshotCache:
    global _po_snapshot_cache
    if _po_snapshot_cache is None:
        with _po_snapshot_cache_lock:
            if _po_snapshot_cache is None:
                _po_snapshot_cache = PurchaseOrderSnapshotCache()
    return _po_snapshot_cache
