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

  flag — the attention state WITHIN a stage (or "none" when nothing needs action), bucketed
    into escalating 1-2d / 3-7d / 8+d tiers:
      overdue/overdue_mid/critical -> days past the classification date (Shopify ETA / PO
        expected date), or — for the pre-order stages with no such date — days sitting in stage
      no_eta           -> ordered PO with no expected date to judge lateness against
      ready_not_called -> received but the customer hasn't been contacted

Only ORDERED special orders can be overdue: an unplaced PO's expected date is speculative,
so lateness is judged solely against placed POs. See `_compute_stage_and_flag`.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.lightspeed_client import LightspeedClient
from app.services import bigquery_sync
from app.services import shopify_match
from app.services.shopify_client import ShopifyClient

# merchantOS (Lightspeed Retail web UI) deep-link views. The item view (matches
# build_lightspeed_item_url in main.py) and the purchase-order view (purchase.views.purchase,
# &tab=main) are confirmed against the live UI; the customer view still follows the same
# pattern and should be confirmed.
_MERCHANTOS = "https://us.merchantos.com/?name={view}&form_name=view&id={id}"

# Overdue thresholds (days "into trouble"). Three escalating tiers, tunable:
#   1..2 days  => "overdue"      3..7 days => "overdue_mid"      8+ days => "critical"
_OVERDUE_MID_MIN = 3
_OVERDUE_MAX = 7

# Pre-order stages (open_pool / unordered_po) are flagged by REAL age (time in stage): healthy
# for the first few days, then the three tiers ramp by actual days. With a 5-day grace the tiers
# land at 5-6d / 7-11d / 12d+.
_PREORDER_GRACE_DAYS = 5

# Flags that count as "overdue" (late against the classification date).
_OVERDUE_FLAGS = ("overdue", "overdue_mid", "critical")

# SpecialOrder.status keywords meaning the item has been checked in / received.
_RECEIVED_STATUS_KEYS = ("received", "ready", "arrived", "pickup", "checked in", "in stock")


def _status_is_received(status: str) -> bool:
    """True if the SpecialOrder.status indicates the item has been checked in/received."""
    sl = (status or "").strip().lower()
    return any(key in sl for key in _RECEIVED_STATUS_KEYS)


def _ls_url(view: str, entity_id: Optional[str], extra: str = "") -> Optional[str]:
    if not entity_id or str(entity_id) in ("", "0"):
        return None
    return _MERCHANTOS.format(view=view, id=entity_id) + extra


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


def _compute_flag(
    stage: str,
    classification_date: Optional[date],
    days_since_creation: Optional[int],
    contacted: bool,
    today: date,
) -> Dict[str, Any]:
    """
    The within-stage attention flag, returned as { flag, days_overdue }, in three escalating
    tiers. Two regimes:

      ordered    -> date-driven: days past `classification_date` (the customer-promised Shopify
                    ETA when present, else the PO's expected date); tiers at 1-2d / 3-7d / 8d+;
                    no date at all => `no_eta`.
      open_pool/  -> age-driven: days the SO has been sitting (days_since_creation), healthy for
      unordered_po   a grace window, then tiers at 5-6d / 7-11d / 12d+. `days_overdue` carries the
                     real age. (The Shopify ETA still shows in its column but doesn't drive these.)
    """
    if stage == "received":
        return {"flag": "none" if contacted else "ready_not_called", "days_overdue": None}

    if stage == "ordered":
        # Date-driven (Shopify ETA preferred, else PO date), no grace. No date => no_eta.
        # days_overdue = days past that date; tiers at 1-2d / 3-7d / 8d+.
        if classification_date is None:
            return {"flag": "no_eta", "days_overdue": None}
        days = (today - classification_date).days  # signed: negative = still on time
        if days > _OVERDUE_MAX:
            flag = "critical"
        elif days >= _OVERDUE_MID_MIN:
            flag = "overdue_mid"
        elif days >= 1:
            flag = "overdue"
        else:
            flag = "none"
        return {"flag": flag, "days_overdue": days}

    # open_pool / unordered_po: flagged by REAL age (time in stage). Healthy for the grace
    # window, then the tiers ramp by actual days; days_overdue carries the real age so the
    # badge and the 5-6d / 7-11d / 12d+ tile labels agree.
    if days_since_creation is None or days_since_creation < _PREORDER_GRACE_DAYS:
        return {"flag": "none", "days_overdue": None}
    into_trouble = days_since_creation - _PREORDER_GRACE_DAYS + 1
    if into_trouble > _OVERDUE_MAX:
        flag = "critical"
    elif into_trouble >= _OVERDUE_MID_MIN:
        flag = "overdue_mid"
    else:
        flag = "overdue"
    return {"flag": flag, "days_overdue": days_since_creation}


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
    POs, then the open pool. The flag here is the PO-date baseline; once the Shopify ETA is
    known, the dashboard re-runs `_compute_flag` with it as the preferred classification date.
    """
    if received:
        stage, stage_index = "received", 3
    elif has_po and po_ordered:
        stage, stage_index = "ordered", 2
    elif has_po:
        stage, stage_index = "unordered_po", 1
    else:
        stage, stage_index = "open_pool", 0

    # PO expected date only counts as a classification date once the PO is actually placed.
    classification_date = expected_date if stage == "ordered" else None
    fl = _compute_flag(stage, classification_date, days_since_creation, contacted, today)

    return {
        "procurement_stage": stage,
        "procurement_stage_index": stage_index,
        "flag": fl["flag"],
        "days_overdue": fl["days_overdue"],
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
        "customer_email": customer.get("email"),
        # Shopify enrichment (filled in by the dashboard merge; defaults for safety).
        "shopify_match": "none",
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_order_url": None,
        "shopify_expected_date": None,
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
        "is_overdue": triage["flag"] in _OVERDUE_FLAGS,
        # Deep links into Lightspeed
        "ls_item_url": _ls_url("item.views.item", item_id),
        "ls_customer_url": _ls_url("customer.views.customer", customer_id),
        # PO deep link: the Retail web UI purchase-order view (confirmed against the live UI).
        "ls_order_url": _ls_url("purchase.views.purchase", order_id, extra="&tab=main"),
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
        "overdue": sum(by_flag.get(f, 0) for f in _OVERDUE_FLAGS),
        "critical": by_flag.get("critical", 0),
        "no_eta": by_flag.get("no_eta", 0),
        "ready_not_called": by_flag.get("ready_not_called", 0),
    }


# Severity rank so flagged items bubble to the top within their stage.
_FLAG_RANK = {"critical": 6, "overdue_mid": 5, "overdue": 4, "no_eta": 3, "ready_not_called": 1, "none": 0}

# The open-SO population + ETAs are pulled live from the Shopify Admin API, so cache the pull
# briefly to keep each live SO refresh cheap (and stay under Shopify's API cost limits).
_shopify_cache: Dict[str, Any] = {"rows": None, "fetched_at": 0.0}
_SHOPIFY_TTL_SECONDS = 600


def invalidate_shopify_cache() -> None:
    """Drops the cached Shopify pull so the next read re-fetches live from Shopify. Called
    right after an ETA write so the change is reflected immediately, with no TTL lag."""
    _shopify_cache["rows"] = None
    _shopify_cache["fetched_at"] = 0.0


def _shopify_rows() -> List[Dict[str, Any]]:
    now = time.time()
    if _shopify_cache["rows"] is not None and (now - _shopify_cache["fetched_at"]) < _SHOPIFY_TTL_SECONDS:
        return _shopify_cache["rows"]
    # Sourced live from the Shopify Admin API (was Fivetran -> BigQuery). The row shape is
    # identical to bigquery_sync.get_shopify_special_orders(), which remains as a fallback.
    rows = ShopifyClient().get_open_special_orders()
    _shopify_cache["rows"] = rows
    _shopify_cache["fetched_at"] = now
    return rows


def _shopify_order_url(order_id: Optional[str]) -> Optional[str]:
    """Admin deep link, only when SHOPIFY_ADMIN_STORE_HANDLE is configured."""
    handle = os.getenv("SHOPIFY_ADMIN_STORE_HANDLE")
    if not handle or not order_id:
        return None
    return f"https://admin.shopify.com/store/{handle}/orders/{order_id}"


def _apply_shopify_match(o: Dict[str, Any], m: Dict[str, Any], today: date) -> None:
    """Writes one LS SO's Shopify match (ETA + order link) onto it and re-buckets its flag,
    preferring the Shopify ETA as the classification date when present (a blown customer promise
    outranks the PO timeline)."""
    o["shopify_match"] = m["shopify_match"]
    o["shopify_order_id"] = m["shopify_order_id"]
    o["shopify_order_name"] = m["shopify_order_name"]
    o["shopify_expected_date"] = m["shopify_expected_date"]
    o["shopify_order_url"] = _shopify_order_url(m["shopify_order_id"])

    shopify_eta = _parse_ls_date(m["shopify_expected_date"])
    po_eta = _parse_ls_date(o.get("expected_date"))
    stage = o["procurement_stage"]
    classification_date = shopify_eta or (po_eta if stage == "ordered" else None)
    fl = _compute_flag(stage, classification_date, o.get("days_since_creation"), o.get("contacted", False), today)
    o["flag"] = fl["flag"]
    o["days_overdue"] = fl["days_overdue"]
    o["is_overdue"] = fl["flag"] in _OVERDUE_FLAGS


def _enrich_with_shopify(
    index: Dict[str, Any],
    orders: List[Dict[str, Any]],
    completed_orders: List[Dict[str, Any]],
    today: date,
) -> List[Dict[str, Any]]:
    """
    Matches LS SOs to Shopify `SO`-tagged orders and returns the Shopify-only ("Unmatched")
    population. Open SOs claim their match first. Then recently-completed SOs adopt any Shopify
    order still left unmatched — the item is received in Lightspeed (SO completed / "Ready For
    Pickup") but its Shopify order hasn't been fulfilled yet — and join the displayed set so they
    surface under "Matched, received" rather than a false "Unmatched". `completed_orders` is
    appended to `orders` in place for the ones that adopt something.

    Never raises: with no Shopify data, every SO simply stays unmatched / PO-classified.
    """
    if not index["orders"]:
        return []
    consumed: set = set()

    for o in orders:
        m = shopify_match.match_special_order(o.get("customer_email"), o.get("system_sku"), index)
        consumed |= m.get("_candidates", set())
        _apply_shopify_match(o, m, today)

    for co in completed_orders:
        m = shopify_match.match_special_order(co.get("customer_email"), co.get("system_sku"), index)
        # Only keep a completed SO if it claims a Shopify order no open SO already did.
        if not (m.get("_candidates", set()) - consumed):
            continue
        consumed |= m.get("_candidates", set())
        _apply_shopify_match(co, m, today)
        orders.append(co)

    unmatched = shopify_match.shopify_only_orders(index, consumed)
    for u in unmatched:
        u["shopify_order_url"] = _shopify_order_url(u["order_id"])
    return unmatched


def _raw_so_system_sku(so: Dict[str, Any]) -> str:
    """The item systemSku off a raw SpecialOrder dict, normalized like the Shopify index keys."""
    return str(((so.get("SaleLine") or {}).get("Item") or {}).get("systemSku") or "").strip()


def get_special_order_dashboard(client: Optional[LightspeedClient] = None) -> Dict[str, Any]:
    """
    Live-fetches open special orders and their attached POs, then returns
    { "orders": [...normalized, sorted by days_overdue desc...], "summary": {...} }.
    """
    client = client or LightspeedClient()
    today = date.today()
    shop_names = {v: k for k, v in client.shop_id_map.items()}

    # Open SOs (always shown), recently-completed SOs (candidates to adopt a still-open Shopify
    # order), and the Shopify rows are all independent — fan them out concurrently.
    with ThreadPoolExecutor(max_workers=3) as executor:
        open_future = executor.submit(client.get_special_orders)
        completed_future = executor.submit(client.get_completed_special_orders)
        shopify_future = executor.submit(_shopify_rows)
        special_orders = open_future.result()
        completed_all = completed_future.result()
        shop_rows = shopify_future.result()

    index = shopify_match.build_shopify_index(shop_rows)
    candidate_skus = set(index["by_sku"].keys())
    # Narrow the completed pool to SOs whose SKU could match an open Shopify order — turns a wide
    # recency window into a handful before we pay for customer/PO resolution.
    completed_candidates = [so for so in completed_all if _raw_so_system_sku(so) in candidate_skus]

    # PO + customer lookups cover both the open SOs and the completed candidates.
    sos_to_resolve = special_orders + completed_candidates
    order_ids = [
        (so.get("OrderLine") or {}).get("orderID") or so.get("orderID")
        for so in sos_to_resolve
    ]
    customer_ids = [so.get("customerID") for so in sos_to_resolve]
    with ThreadPoolExecutor(max_workers=2) as executor:
        order_future = executor.submit(client.get_orders_by_ids, order_ids)
        customer_future = executor.submit(client.get_customers_by_ids, customer_ids)
        order_map = order_future.result()
        customer_map = customer_future.result()

    orders = [_normalize(so, order_map, customer_map, shop_names, today) for so in special_orders]
    completed_orders = [_normalize(so, order_map, customer_map, shop_names, today) for so in completed_candidates]

    # Enrich with the Shopify ETA; matched-completed SOs are appended to `orders`, and the
    # genuinely-orphaned Shopify orders come back as the "Unmatched" population.
    shopify_only = _enrich_with_shopify(index, orders, completed_orders, today)

    # Flagged items first, ranked by flag severity, then most-overdue / oldest within that.
    orders.sort(
        key=lambda o: (
            -_FLAG_RANK.get(o["flag"], 0),
            -(o["days_overdue"] or 0),
            -(o["days_since_creation"] or 0),
        )
    )

    return {"orders": orders, "summary": _summarize(orders), "shopify_only": shopify_only}
