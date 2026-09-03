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
        # Held only across the Lightspeed walk, never across a cache read. Keeping the
        # walk out of `_lock` is the whole point: a complete walk costs ~40s over ~1,850
        # POs, and holding the state mutex for that long made `peek_orders()` -- which is
        # documented never to trigger a load -- block for the entire walk anyway.
        self._load_lock = threading.Lock()
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

    def peek_orders(self, where=None) -> Optional[List[Dict[str, Any]]]:
        """The cached snapshot if it is already fresh, else None. Never triggers a load.

        A full walk costs ~40s against ~1,850 purchase orders. Callers who merely *benefit* from
        PO data (the special-order recommendation engine) must not be able to impose that on a
        user; they degrade gracefully instead, while the workbench and the startup warmer keep
        the snapshot populated for everyone.

        ``where`` narrows the copy to the orders a caller actually reads. The copy itself is not
        optional -- callers must not be able to mutate the shared snapshot -- but its *size* is:
        copying all ~1,850 orders for a consumer that only looks at drafts put a second full
        snapshot on the heap of a 512MB worker. Same reasoning as ``_render``, which has always
        copied only its filtered subset.
        """
        with self._lock:
            if self._orders is not None and self._is_fresh(self.clock()):
                orders = self._orders if where is None else [
                    order for order in self._orders if where(order)
                ]
                return deepcopy(orders)
        return None

    def _render(self, now: float, vendor_id: Optional[str], shop_id: Optional[str],
                cache_hit: bool) -> Dict[str, Any]:
        """Filter and package the current snapshot. Caller must hold ``_lock``."""
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
            # Copied so a caller cannot reach back into the shared snapshot; only the
            # filtered subset is copied, not the whole walk.
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

    def get_orders(
        self,
        vendor_id: Optional[str] = None,
        shop_id: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        requested_generation = self._generation

        # Fast path: a fresh snapshot answers without ever queueing behind a walk.
        if not force_refresh:
            with self._lock:
                now = self.clock()
                if self._is_fresh(now):
                    return self._render(now, vendor_id, shop_id, cache_hit=True)

        # Slow path. `_load_lock` serializes walkers so a burst of expired requests
        # triggers one walk, not N -- but readers holding `_lock` are never blocked by it.
        with self._load_lock:
            with self._lock:
                now = self.clock()
                refreshed_while_waiting = (
                    force_refresh
                    and self._orders is not None
                    and self._generation != requested_generation
                )
                if refreshed_while_waiting or (not force_refresh and self._is_fresh(now)):
                    return self._render(now, vendor_id, shop_id, cache_hit=True)

            # Deliberately omit filters: one complete paginated snapshot is shared by
            # every vendor/shop workbench card. Runs OUTSIDE `_lock`.
            loaded = self.gateway_factory().list_purchase_orders(include_lines=False)
            orders = self._validate_complete_snapshot(loaded)

            with self._lock:
                now = self.clock()
                self._orders = deepcopy(orders)
                self._snapshot_at_epoch = now
                self._generation += 1
                return self._render(now, vendor_id, shop_id, cache_hit=False)


_po_snapshot_cache: Optional[PurchaseOrderSnapshotCache] = None
_po_snapshot_cache_lock = threading.Lock()


def get_po_snapshot_cache() -> PurchaseOrderSnapshotCache:
    global _po_snapshot_cache
    if _po_snapshot_cache is None:
        with _po_snapshot_cache_lock:
            if _po_snapshot_cache is None:
                _po_snapshot_cache = PurchaseOrderSnapshotCache()
    return _po_snapshot_cache
