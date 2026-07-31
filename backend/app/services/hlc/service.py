"""Maps HLC shipment tracking onto Lightspeed purchase orders.

HLC's tracking endpoint is keyed by HLC order number, but the PO tracker is keyed
by Lightspeed order id, so the lookup is two hops:

    Lightspeed orderID  ==  HLC PoNumber  ->  HLC OrderNumber  ->  tracking rows

The join works because buyers type the Lightspeed PO number ("PO #16320" in the
UI) into HLC's PO field when placing a stocking order. Two things observed in
live data shape the mapping:

  * One PO can produce several HLC orders (a split shipment gets its own order
    number), so the map is one-to-many and boxes from every matching HLC order
    merge onto the single Lightspeed PO.
  * Roughly 10% of stocking orders have a blank PoNumber. Those are simply
    unmatchable and are skipped rather than guessed at.

The whole result is cached behind a TTL matching HLC's 15-minute tracking
refresh, since the underlying /Orders walk takes upwards of 10 seconds.
"""

import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.services.hlc import config
from app.services.hlc.client import HlcClient

_cache: Dict[str, Any] = {"data": None, "meta": None, "fetched_at": 0.0}
_lock = threading.Lock()


def _normalize_po_number(value: Any) -> Optional[str]:
    """HLC PoNumber -> Lightspeed order id.

    Dropship orders use '#'-prefixed numbers and some fields carry stray
    whitespace, so strip both before comparing. Returns None for anything that
    isn't a usable Lightspeed order id.
    """
    if value is None:
        return None
    text = str(value).strip().lstrip("#").strip()
    return text or None


def _build_tracking_map(client: Optional[HlcClient] = None) -> Dict[str, Any]:
    """Walk recent HLC orders, fetch tracking, and group boxes by Lightspeed PO."""
    client = client or HlcClient()
    date_from = (date.today() - timedelta(days=config.LOOKBACK_DAYS)).isoformat()
    orders = client.get_orders(date_from=date_from)

    # HLC order number -> Lightspeed PO. Only stocking orders carry a real PO
    # number; dropship ("Fulfillment") orders are excluded by type.
    po_by_order_number: Dict[str, str] = {}
    for order in orders:
        if order.get("OrderType") not in config.TRACKED_ORDER_TYPES:
            continue
        po_number = _normalize_po_number(order.get("PoNumber"))
        order_number = (order.get("OrderNumber") or "").strip()
        if po_number and order_number:
            po_by_order_number[order_number] = po_number

    rows, failed = client.get_tracking(list(po_by_order_number))

    # Group boxes by Lightspeed PO, deduping on box number — a PO split across
    # two HLC orders can otherwise repeat a box.
    grouped: Dict[str, Dict[str, Any]] = {}
    seen_boxes: Dict[str, set] = {}
    for row in rows:
        order_number = (row.get("OrderNumber") or "").strip()
        # PurchaseOrderNumber comes back populated when querying by order number,
        # but fall back to the map so a null never drops a box.
        po_number = _normalize_po_number(row.get("PurchaseOrderNumber")) or po_by_order_number.get(order_number)
        tracking_number = (row.get("TrackingNumber") or "").strip()
        if not po_number or not tracking_number:
            continue

        entry = grouped.setdefault(po_number, {"boxes": [], "hlc_order_numbers": []})
        # Record provenance before the dedupe check: when two HLC orders ship in
        # one physical box they both report that box, and the PO still legitimately
        # came from both orders.
        if order_number and order_number not in entry["hlc_order_numbers"]:
            entry["hlc_order_numbers"].append(order_number)

        box_number = (row.get("BoxNumber") or "").strip() or None
        dedupe_key = box_number or tracking_number
        box_keys = seen_boxes.setdefault(po_number, set())
        if dedupe_key in box_keys:
            continue
        box_keys.add(dedupe_key)

        entry["boxes"].append({
            "box_number": box_number,
            "tracking_number": tracking_number,
            "carrier": (row.get("Carrier") or "").strip() or None,
            # HLC spells this field "TrakingUrl" (sic).
            "tracking_url": (row.get("TrakingUrl") or "").strip() or None,
        })

    for po_number, entry in grouped.items():
        carriers = {b["carrier"] for b in entry["boxes"] if b["carrier"]}
        entry["carrier"] = carriers.pop() if len(carriers) == 1 else ("Mixed" if carriers else None)
        entry["box_count"] = len(entry["boxes"])

    meta = {
        "lookback_days": config.LOOKBACK_DAYS,
        "hlc_orders_scanned": len(orders),
        "hlc_orders_matched": len(po_by_order_number),
        "purchase_orders_with_tracking": len(grouped),
        "orders_failed": len(failed),
    }
    return {"data": grouped, "meta": meta}


def get_tracking_by_lightspeed_order(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Cached {lightspeed_order_id: tracking} map."""
    return _get_cached(force_refresh)["data"]


def get_tracking_meta() -> Optional[Dict[str, Any]]:
    """Counters from the last successful build, for the PO tracker's meta block."""
    meta = _cache.get("meta")
    if not meta:
        return None
    return {**meta, "cache_age_seconds": round(time.time() - _cache["fetched_at"], 1)}


def _get_cached(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    cached = _cache.get("data")
    if not force_refresh and cached is not None and now - _cache["fetched_at"] <= config.CACHE_TTL_SECONDS:
        return {"data": cached, "meta": _cache["meta"]}

    with _lock:
        cached = _cache.get("data")
        if not force_refresh and cached is not None and time.time() - _cache["fetched_at"] <= config.CACHE_TTL_SECONDS:
            return {"data": cached, "meta": _cache["meta"]}
        result = _build_tracking_map()
        _cache["data"] = result["data"]
        _cache["meta"] = result["meta"]
        _cache["fetched_at"] = time.time()
        return result


def warm_cache() -> None:
    """Build the cache on boot so the first PO tracker request doesn't pay the
    ~10s /Orders walk. Never fatal — the tracker renders without tracking."""
    try:
        _get_cached()
        print(f"hlc: tracking cache warmed ({get_tracking_meta()})")
    except Exception as e:
        print(f"hlc: tracking cache warm-up failed: {e}")


def reset_cache() -> None:
    """Test hook."""
    _cache["data"] = None
    _cache["meta"] = None
    _cache["fetched_at"] = 0.0
