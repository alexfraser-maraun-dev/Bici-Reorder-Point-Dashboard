"""
Special Order dashboard service.

Walks the Lightspeed special-order graph live and produces normalized, dashboard-ready
records for the procurement cockpit's Special Order page:

    SpecialOrder --(OrderLine.orderID)--> Order   (the attached purchase order)

Records are triaged on TWO axes:

  procurement_stage — the SO's position in the procurement flow, derived from the *PO's*
    real state rather than the SpecialOrder.status string (which flips to "Ordered" the
    moment a PO is attached, even if that PO was never actually placed with the vendor):
      open_pool     -> no PO attached yet
      unordered_po  -> PO attached but not yet placed with the vendor (no orderedDate)
      ordered       -> PO placed with the vendor (Order.orderedDate is set)
      received      -> the SO has been checked in (SpecialOrder.status says so)

  flag — the attention state WITHIN a stage (or "none" when nothing needs action):
      aged             -> open_pool / unordered_po sitting too long
      overdue/critical -> ordered PO past its expected (arrival) date; two-step on day 8
      no_eta           -> ordered PO with no expected date to judge lateness against
      ready_not_called -> received but the customer hasn't been contacted

Only ORDERED special orders can be overdue: an unplaced PO's expected date is speculative,
so lateness is judged solely against placed POs. See `_compute_stage_and_flag`.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.lightspeed_client import LightspeedClient

# merchantOS (Lightspeed Retail web UI) deep-link views. The item view is known-good
# (matches build_lightspeed_item_url in main.py); the customer and order views follow the
# same pattern and should be confirmed against the live UI.
_MERCHANTOS = "https://us.merchantos.com/?name={view}&form_name=view&id={id}"

# Days an SO can sit in the open-pool / unordered-PO stages before it's flagged "aged".
# Keep in sync with STALE_STAGE_DAYS in frontend/lib/special-order-triage.ts (tile labels).
_STALE_STAGE_DAYS = 5

# Overdue thresholds (days past an ordered PO's expected date). Tunable.
_OVERDUE_MAX = 7         # 1..7 days late => "overdue"; 8+ => "critical"

# SpecialOrder.status keywords meaning the item has been checked in / received.
_RECEIVED_STATUS_KEYS = ("received", "ready", "arrived", "pickup", "checked in", "in stock")


def _status_is_received(status: str) -> bool:
    """True if the SpecialOrder.status indicates the item has been checked in/received."""
    sl = (status or "").strip().lower()
    return any(key in sl for key in _RECEIVED_STATUS_KEYS)


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


def _compute_stage_and_flag(
    has_po: bool,
    po_ordered: bool,
    received: bool,
    expected_date: Optional[date],
    days_since_creation: Optional[int],
    contacted: bool,
    today: date,
) -> Dict[str, Any]:
    """
    Two-axis triage. Returns:
      { procurement_stage, procurement_stage_index, flag, days_overdue }

    Stage is a waterfall — "received" is terminal and authoritative, then ordered/unordered
    POs, then the open pool. The within-stage `flag` is the one thing (if any) that needs
    attention. Only ORDERED special orders can be overdue.
    """
    # --- Stage (procurement flow position) ---
    if received:
        stage, stage_index = "received", 3
    elif has_po and po_ordered:
        stage, stage_index = "ordered", 2
    elif has_po:
        stage, stage_index = "unordered_po", 1
    else:
        stage, stage_index = "open_pool", 0

    # --- Flag (attention state within the stage) ---
    flag = "none"
    days_overdue: Optional[int] = None

    if stage in ("open_pool", "unordered_po"):
        if days_since_creation is not None and days_since_creation > _STALE_STAGE_DAYS:
            flag = "aged"
    elif stage == "ordered":
        if expected_date is None:
            flag = "no_eta"
        else:
            days_overdue = (today - expected_date).days  # signed: negative = still early
            if days_overdue > _OVERDUE_MAX:
                flag = "critical"
            elif days_overdue >= 1:
                flag = "overdue"
    elif stage == "received":
        if not contacted:
            flag = "ready_not_called"

    return {
        "procurement_stage": stage,
        "procurement_stage_index": stage_index,
        "flag": flag,
        "days_overdue": days_overdue,
    }


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

    item_id = item.get("itemID")
    customer_id = so.get("customerID")
    shop_id = str(so.get("shopID")) if so.get("shopID") is not None else None
    status = so.get("status") or "Unknown"
    contacted = _coerce_bool(so.get("contacted"))

    # True creation time comes from the linked SaleLine (createTime). Fall back to the
    # SpecialOrder's timeStamp (last-modified) when the SaleLine/createTime is absent.
    created_raw = sale_line.get("createTime") or so.get("timeStamp")
    created_date = _parse_ls_date(created_raw)
    days_since_creation = (today - created_date).days if created_date else None

    # The attached PO's expected (arrival) date and the date it was actually placed with
    # the vendor. A present orderedDate is what distinguishes an "ordered" PO from a draft.
    expected_date = _parse_ls_date(po.get("arrivalDate"))
    ordered_date = _parse_ls_date(po.get("orderedDate"))

    triage = _compute_stage_and_flag(
        has_po=order_id is not None,
        po_ordered=ordered_date is not None,
        received=_status_is_received(status),
        expected_date=expected_date,
        days_since_creation=days_since_creation,
        contacted=contacted,
        today=today,
    )

    return {
        "special_order_id": so.get("specialOrderID"),
        "status": status,
        "unit_quantity": so.get("unitQuantity"),
        "shop_id": shop_id,
        "store": shop_names.get(shop_id) if shop_id else None,
        "timestamp": created_raw,
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
        "ordered_date": ordered_date.isoformat() if ordered_date else None,
        "po_ordered": ordered_date is not None,
        "po_complete": bool(po.get("complete")),
        "received_started": bool(po.get("received_started")),
        # Triage: procurement stage + within-stage attention flag
        "procurement_stage": triage["procurement_stage"],
        "procurement_stage_index": triage["procurement_stage_index"],
        "flag": triage["flag"],
        "days_overdue": triage["days_overdue"],
        "is_overdue": triage["flag"] in ("overdue", "critical"),
        # Deep links into Lightspeed
        "ls_item_url": _ls_url("item.views.item", item_id),
        "ls_customer_url": _ls_url("customer.views.customer", customer_id),
        "ls_order_url": _ls_url("inventory.views.order", order_id),
    }


_STAGES = ["open_pool", "unordered_po", "ordered", "received"]


def _summarize(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-stage totals + how many in each stage carry an attention flag, plus a flat
    flag breakdown for convenience tiles."""
    by_stage = {s: 0 for s in _STAGES}
    flagged_by_stage = {s: 0 for s in _STAGES}
    by_flag: Dict[str, int] = {}
    for o in orders:
        stage = o["procurement_stage"]
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if o["flag"] != "none":
            flagged_by_stage[stage] = flagged_by_stage.get(stage, 0) + 1
        by_flag[o["flag"]] = by_flag.get(o["flag"], 0) + 1
    return {
        "total_open": len(orders),
        "by_stage": by_stage,
        "flagged_by_stage": flagged_by_stage,
        "by_flag": by_flag,
        # Flat convenience counts
        "aged": by_flag.get("aged", 0),
        "overdue": by_flag.get("overdue", 0) + by_flag.get("critical", 0),
        "critical": by_flag.get("critical", 0),
        "no_eta": by_flag.get("no_eta", 0),
        "ready_not_called": by_flag.get("ready_not_called", 0),
    }


# Severity rank so flagged items bubble to the top within their stage.
_FLAG_RANK = {"critical": 5, "overdue": 4, "no_eta": 3, "aged": 3, "ready_not_called": 1, "none": 0}


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
    # Flagged items first, ranked by flag severity, then most-overdue / oldest within that.
    orders.sort(
        key=lambda o: (
            -_FLAG_RANK.get(o["flag"], 0),
            -(o["days_overdue"] or 0),
            -(o["days_since_creation"] or 0),
        )
    )

    return {"orders": orders, "summary": _summarize(orders)}
