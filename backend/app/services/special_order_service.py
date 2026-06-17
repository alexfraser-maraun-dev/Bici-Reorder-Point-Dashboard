"""
Special Order dashboard service.

Walks the Lightspeed special-order graph live and produces normalized, dashboard-ready
records for the procurement cockpit's Special Order page:

    SpecialOrder --(OrderLine.orderID)--> Order   (the attached purchase order)

The "expected date" the overdue logic hangs on is the PO's `Order.arrivalDate`. A special
order is OVERDUE when its attached PO is LATE: the expected date has passed and receiving
on that PO has not started or completed. See `_compute_aging` for the exact rule, which is
intentionally tunable.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.lightspeed_client import LightspeedClient

# merchantOS (Lightspeed Retail web UI) deep-link views. The item view is known-good
# (matches build_lightspeed_item_url in main.py); the customer and order views follow the
# same pattern and should be confirmed against the live UI.
_MERCHANTOS = "https://us.merchantos.com/?name={view}&form_name=view&id={id}"

# Aging thresholds (days past the PO's expected date). Tunable — see plan.
_DUE_SOON_DAYS = 3       # within this many days of the expected date => "due soon"
_OVERDUE_MAX = 7         # 1..7 days late
_CRITICAL_MAX = 14       # 8..14 days late; 15+ => "stale"

# How many days an SO can sit without a PO before it's flagged (unordered_too_long).
_UNORDERED_TOO_LONG_DAYS = 3

# Canonical SO lifecycle stages in order (for the status path stepper).
# LS may return variants — we match case-insensitively.
_STATUS_STAGES = ["Not Ordered", "Ordered", "Ready For Pickup", "Received"]


def _status_stage_index(status: str) -> int:
    """Return 0-based index of status in _STATUS_STAGES, or -1 if unknown."""
    sl = status.strip().lower()
    for i, stage in enumerate(_STATUS_STAGES):
        if sl.startswith(stage.lower()) or stage.lower().startswith(sl.split()[0] if sl else ""):
            return i
    return -1


def _ls_url(view: str, entity_id: Optional[str]) -> Optional[str]:
    if not entity_id or str(entity_id) in ("", "0"):
        return None
    return _MERCHANTOS.format(view=view, id=entity_id)


def _coerce_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _parse_ls_date(value: Optional[str]) -> Optional[date]:
    """Parse a Lightspeed date (YYYY-MM-DD or full ISO-8601) to a date, or None."""
    if not value:
        return None
    text = str(value).strip()
    try:
        # Handles "2025-07-10" and "2025-07-10T01:01:02+00:00"
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _customer_name(customer: Dict[str, Any]) -> Optional[str]:
    if not customer:
        return None
    parts = [customer.get("first_name"), customer.get("last_name")]
    name = " ".join(p for p in parts if p).strip()
    return name or customer.get("company") or None


def _compute_aging(
    expected_date: Optional[date],
    po_complete: bool,
    received_started: bool,
    has_po: bool,
    today: date,
) -> Dict[str, Any]:
    """
    Returns { aging_bucket, is_overdue, days_overdue }.

    Overdue trigger (per user): the attached PO is late — expected date has passed AND
    receiving has neither started nor completed. Receiving state of the PO is authoritative;
    once any receipt is recorded (or the PO is complete) the SO is no longer "awaiting vendor"
    and drops out of overdue.
    """
    # Receiving already underway/finished -> not waiting on the vendor.
    if po_complete:
        return {"aging_bucket": "received", "is_overdue": False, "days_overdue": None}
    if received_started:
        return {"aging_bucket": "receiving", "is_overdue": False, "days_overdue": None}

    # No expected date to judge against (no PO, or PO with no arrival date).
    if expected_date is None:
        bucket = "no_po" if not has_po else "no_eta"
        return {"aging_bucket": bucket, "is_overdue": False, "days_overdue": None}

    days_overdue = (today - expected_date).days  # signed: negative = still early

    if days_overdue <= 0:
        bucket = "due_soon" if days_overdue >= -_DUE_SOON_DAYS else "on_track"
        return {"aging_bucket": bucket, "is_overdue": False, "days_overdue": days_overdue}

    if days_overdue <= _OVERDUE_MAX:
        bucket = "overdue"
    elif days_overdue <= _CRITICAL_MAX:
        bucket = "critical"
    else:
        bucket = "stale"
    return {"aging_bucket": bucket, "is_overdue": True, "days_overdue": days_overdue}


def _normalize(
    so: Dict[str, Any],
    order_map: Dict[str, Dict[str, Any]],
    customer_map: Dict[str, Dict[str, Any]],
    shop_names: Dict[str, str],
    today: date,
) -> Dict[str, Any]:
    sale_line = so.get("SaleLine") or {}
    item = sale_line.get("Item") or {}
    order_line = so.get("OrderLine") or {}
    customer = customer_map.get(str(so.get("customerID")), {})

    order_id = order_line.get("orderID") or so.get("orderID")
    order_id = str(order_id) if order_id and str(order_id) != "0" else None
    po = order_map.get(order_id, {}) if order_id else {}

    expected_raw = po.get("arrivalDate")
    expected_date = _parse_ls_date(expected_raw)
    aging = _compute_aging(
        expected_date=expected_date,
        po_complete=bool(po.get("complete")),
        received_started=bool(po.get("received_started")),
        has_po=order_id is not None,
        today=today,
    )

    item_id = item.get("itemID")
    customer_id = so.get("customerID")
    shop_id = str(so.get("shopID")) if so.get("shopID") is not None else None
    status = so.get("status") or "Unknown"
    contacted = _coerce_bool(so.get("contacted"))

    # SO record timestamp (last-modified; for unordered SOs this is effectively creation time).
    ts_raw = so.get("timeStamp")
    created_date = _parse_ls_date(ts_raw)
    days_since_creation = (today - created_date).days if created_date else None

    return {
        "special_order_id": so.get("specialOrderID"),
        "status": status,
        "status_stage": _status_stage_index(status),
        "unit_quantity": so.get("unitQuantity"),
        "shop_id": shop_id,
        "store": shop_names.get(shop_id) if shop_id else None,
        "timestamp": ts_raw,
        "created_date": created_date.isoformat() if created_date else None,
        "days_since_creation": days_since_creation,
        "contacted": contacted,
        "completed": _coerce_bool(so.get("completed")),
        # Customer
        "customer_id": customer_id,
        "customer_name": _customer_name(customer),
        "customer_phone": customer.get("phone"),
        # Item / product
        "item_id": item_id,
        "system_sku": item.get("systemSku"),
        "description": item.get("description") or sale_line.get("description"),
        # Attached purchase order
        "order_id": order_id,
        "vendor_id": po.get("vendor_id"),
        "vendor_name": po.get("vendor_name"),
        "expected_date": expected_date.isoformat() if expected_date else None,
        "po_complete": bool(po.get("complete")),
        "received_started": bool(po.get("received_started")),
        # Derived overdue / aging
        "is_overdue": aging["is_overdue"],
        "days_overdue": aging["days_overdue"],
        "aging_bucket": aging["aging_bucket"],
        "no_eta": aging["aging_bucket"] in ("no_eta", "no_po"),
        # "Ready, Not Called": received but customer not yet contacted
        "ready_not_called": (
            bool(po.get("complete")) or status.lower().startswith("ready")
        ) and not contacted,
        # SO has no PO attached and has been open too long — needs to be ordered
        "unordered_too_long": (
            order_id is None
            and days_since_creation is not None
            and days_since_creation > _UNORDERED_TOO_LONG_DAYS
        ),
        # Deep links into Lightspeed
        "ls_item_url": _ls_url("item.views.item", item_id),
        "ls_customer_url": _ls_url("customer.views.customer", customer_id),
        "ls_order_url": _ls_url("inventory.views.order", order_id),
    }


def _summarize(orders: List[Dict[str, Any]]) -> Dict[str, int]:
    buckets: Dict[str, int] = {}
    for o in orders:
        buckets[o["aging_bucket"]] = buckets.get(o["aging_bucket"], 0) + 1
    return {
        "total_open": len(orders),
        "overdue": sum(1 for o in orders if o["is_overdue"]),
        "critical": buckets.get("critical", 0) + buckets.get("stale", 0),
        "no_eta": buckets.get("no_eta", 0) + buckets.get("no_po", 0),
        "ready_not_called": sum(1 for o in orders if o["ready_not_called"]),
        "unordered_too_long": sum(1 for o in orders if o["unordered_too_long"]),
        "by_bucket": buckets,
    }


def get_special_order_dashboard(client: Optional[LightspeedClient] = None) -> Dict[str, Any]:
    """
    Live-fetches open special orders and their attached POs, then returns
    { "orders": [...normalized, sorted by days_overdue desc...], "summary": {...} }.
    """
    client = client or LightspeedClient()
    today = date.today()
    shop_names = {v: k for k, v in client.shop_id_map.items()}

    special_orders = client.get_special_orders()

    order_ids = [
        (so.get("OrderLine") or {}).get("orderID") or so.get("orderID")
        for so in special_orders
    ]
    order_map = client.get_orders_by_ids(order_ids)
    customer_map = client.get_customers_by_ids([so.get("customerID") for so in special_orders])

    orders = [_normalize(so, order_map, customer_map, shop_names, today) for so in special_orders]
    # Most-overdue first; records without a days_overdue sort to the bottom.
    orders.sort(key=lambda o: (o["days_overdue"] is None, -(o["days_overdue"] or 0)))

    return {"orders": orders, "summary": _summarize(orders)}
