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

import copy
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.lightspeed_client import LightspeedClient
from app.services import bigquery_sync
from app.services import shopify_match
from app.services.shopify_client import ShopifyClient

# merchantOS (Lightspeed Retail web UI) deep-link views. The item view (matches
# build_lightspeed_item_url in main.py), the purchase-order view (purchase.views.purchase,
# &tab=main) and the workorder view (workbench.views.beta_workorder, &tab=details) are
# confirmed against the live UI; the customer view still follows the same pattern and
# should be confirmed.
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
_UNRECEIVED_STATUS_KEYS = (
    "not received", "not ready", "not arrived", "not checked in", "not in stock",
    "backorder", "backordered", "back order",
)


def _status_is_received(status: str) -> bool:
    """True if the SpecialOrder.status indicates the item has been checked in/received."""
    sl = (status or "").strip().lower()

    def has_phrase(key: str) -> bool:
        # Word boundaries matter: a substring check would find "ready" inside "already" and
        # could turn "already ordered" into a false receipt.
        return bool(re.search(rf"(?<!\w){re.escape(key)}(?!\w)", sl))

    if any(has_phrase(key) for key in _UNRECEIVED_STATUS_KEYS):
        return False
    return any(has_phrase(key) for key in _RECEIVED_STATUS_KEYS)


def derive_receiving_state(*, so_received: bool, po_receiving_started: bool,
                           po_received_date: Optional[str], po_complete: bool) -> str:
    """Keep individual-SO receipt separate from purchase-order receiving context.

    Lightspeed's PO header and OrderLines describe the whole purchase order. Another line can
    be checked in while this special-order item remains outstanding, so neither ``checkedIn`` /
    ``numReceived`` on another line nor ``Order.receivedDate`` proves this SO arrived. The
    SpecialOrder's own status is authoritative for ``so_received``.
    """
    if so_received:
        return "so_received"
    if po_complete:
        return "po_complete_so_unreceived"
    if po_receiving_started or bool(po_received_date):
        return "po_receiving"
    return "not_started"


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


# Most "Available from" vendors to surface per SO tile — the fastest few, so the buyer sees the
# best sourcing options without the tile turning into a vendor dump.
_MAX_AVAILABLE_VENDORS = 3


def _value_count(value: Any) -> Optional[int]:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return None


def _safe(fn, default, health: Optional[Dict[str, Any]] = None,
          source: Optional[str] = None):
    """Runs a sourcing-data fetch, returning `default` on any failure so a BigQuery hiccup
    degrades the related field rather than failing the whole SO dashboard. When a health
    collector is supplied, the degradation is returned to the UI instead of masquerading as a
    genuine empty result."""
    try:
        value = fn()
        if health is not None and source:
            health[source] = {
                "status": "ok",
                "checked_at": datetime.utcnow().isoformat() + "Z",
                "record_count": _value_count(value),
            }
        return value
    except Exception as e:
        print(f"[special_orders] sourcing fetch failed: {e}")
        if health is not None and source:
            health[source] = {
                "status": "unavailable",
                "checked_at": datetime.utcnow().isoformat() + "Z",
                "record_count": _value_count(default),
                "message": "Source unavailable; related fields may be incomplete.",
            }
        return default


def _source_statuses(health: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Collapse detailed fetches into the four source indicators used by the worklist."""
    groups = {
        "lightspeed": (
            "lightspeed_open_special_orders", "lightspeed_completed",
            "lightspeed_purchase_orders", "lightspeed_customers",
        ),
        "shopify": ("shopify_open_special_orders", "shopify_recent_fallback"),
        "bigquery": (
            "bigquery_item_brands", "bigquery_brand_vendor_sourcing",
            "bigquery_vendor_lead_times", "bigquery_match_overrides",
        ),
        "workorders": ("lightspeed_workorders",),
    }
    out: Dict[str, Dict[str, Any]] = {}
    for group, names in groups.items():
        entries = [health[name] for name in names if name in health]
        if not entries:
            continue
        unavailable = [entry for entry in entries if entry.get("status") != "ok"]
        if not unavailable:
            group_status = "ok"
        elif len(unavailable) == len(entries):
            group_status = "unavailable"
        else:
            group_status = "stale"  # partially available; some enrichment may be incomplete
        counts = [entry.get("record_count") for entry in entries
                  if isinstance(entry.get("record_count"), int)]
        out[group] = {
            "status": group_status,
            "checked_at": max(
                (str(entry.get("checked_at") or "") for entry in entries), default=""
            ) or None,
            "record_count": sum(counts) if counts else None,
            "message": (
                "One or more source reads failed; related fields may be incomplete."
                if unavailable else None
            ),
        }
    return out


def _compute_available_vendors(
    brand: Optional[str],
    shop_id: Optional[str],
    sourcing_map: Dict[str, List[Dict[str, Any]]],
    lt_by_vendor_loc: Dict[Any, float],
    lt_by_vendor: Dict[str, float],
) -> List[Dict[str, Any]]:
    """The brand's candidate vendors, each annotated with its lead time at THIS SO's store
    (falling back to the vendor's median across stores), fastest first, capped at
    `_MAX_AVAILABLE_VENDORS`. Empty when the brand is unknown or has no qualifying vendors."""
    if not brand:
        return []
    out: List[Dict[str, Any]] = []
    for c in sourcing_map.get(brand, []):
        vid = str(c["vendor_id"])
        lead = None
        source = None
        if shop_id is not None and (vid, str(shop_id)) in lt_by_vendor_loc:
            lead = lt_by_vendor_loc[(vid, str(shop_id))]
            source = "store"
        elif vid in lt_by_vendor:
            lead = lt_by_vendor[vid]
            source = "vendor_median"
        out.append({
            "vendor_id": vid,
            "vendor_name": c["vendor_name"],
            "lead_time_days": int(round(lead)) if lead is not None else None,
            "lead_time_source": source,
            "distinct_items": c.get("distinct_items"),
        })
    # Fastest first; vendors with a known lead time rank ahead of unknown ones, which keep their
    # most-established-first (distinct-items) order from the sourcing map.
    out.sort(key=lambda v: (v["lead_time_days"] is None, v["lead_time_days"] or 0))
    return out[:_MAX_AVAILABLE_VENDORS]


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
    sourcing_ctx: Optional[Dict[str, Any]] = None,
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
    so_received = _status_is_received(status)
    contacted = _coerce_bool(so.get("contacted"))

    # Service-bench linkage: an SO raised from a workorder reaches its Workorder via
    # WorkorderItem.saleLineID (see get_workorders_by_sale_line_ids — the sale-level join
    # misses un-invoiced workorders). Empty map (fetch failed / no scope) just means no badge.
    sale_line_id = so.get("saleLineID") or sale_line.get("saleLineID")
    workorder = (sourcing_ctx or {}).get("workorder_map", {}).get(str(sale_line_id)) or {}
    workorder_id = workorder.get("workorder_id")

    # True creation time comes from the linked SaleLine (createTime). Fall back to the
    # SpecialOrder's timeStamp (last-modified) when the SaleLine/createTime is absent.
    created_raw = sale_line.get("createTime") or so.get("timeStamp")
    created_date = _parse_ls_date(created_raw)
    days_since_creation = (today - created_date).days if created_date else None

    # The attached PO's expected (arrival) date and the date it was actually placed with
    # the vendor. A present orderedDate is what distinguishes an "ordered" PO from a draft.
    expected_date = _parse_ls_date(po.get("arrivalDate"))
    ordered_date = _parse_ls_date(po.get("orderedDate"))
    po_created_date = _parse_ls_date(po.get("createTime"))
    po_received_date = _parse_ls_date(po.get("receivedDate"))
    # SpecialOrder.timeStamp is the best available timestamp for the individual SO's status
    # transition. It is deliberately used only when that status says the SO itself is received;
    # the PO header date may belong to a different line in a split shipment.
    so_received_date = _parse_ls_date(so.get("timeStamp")) if so_received else None
    po_complete = bool(po.get("complete"))
    po_receiving_started = bool(po.get("received_started"))
    receiving_state = derive_receiving_state(
        so_received=so_received,
        po_receiving_started=po_receiving_started,
        po_received_date=po_received_date.isoformat() if po_received_date else None,
        po_complete=po_complete,
    )
    days_po_open = (today - po_created_date).days if po_created_date else None

    # Two clocks, deliberately kept separate (verified against live data 2026-08-19):
    #   days_since_creation -> total elapsed since the customer asked. Answers "will we miss
    #                          the promise?"
    #   days_in_stage       -> dwell in the CURRENT step. Answers "is this step stalling?"
    # Neither substitutes for the other. An SO can be 92 days old on a PO drafted 2 days ago
    # (a long pre-allocation wait), or 3 days old on a 48-day-old draft (a stalled draft).
    # Flagging on stage dwell alone would have read the first case as healthy.
    if order_id is not None and ordered_date is None:
        days_in_stage = days_po_open        # sitting on a drafted-but-unplaced PO
    else:
        days_in_stage = days_since_creation

    # Where this special order derives from, for the Source badge/filter. A workorder wins over
    # a Shopify match: the bench is where the request actually originated. `shopify` is applied
    # later by _apply_shopify_match once a match resolves; until then a Shopify-born SO reads
    # "neither", which is correct -- nothing links it yet.
    source = "workorder" if workorder_id else "neither"

    triage = _compute_stage_and_flag(
        has_po=order_id is not None,
        po_ordered=ordered_date is not None,
        received=so_received,
        expected_date=expected_date,
        days_since_creation=days_since_creation,
        contacted=contacted,
        today=today,
    )

    # Brand-level "Available from" sourcing: which vendors can supply this SKU's brand, and how
    # fast each one is to this SO's store. Resolved from the in-memory maps the dashboard built.
    ctx = sourcing_ctx or {}
    brand = (ctx.get("brand_map") or {}).get(str(item_id)) if item_id is not None else None
    available_vendors = _compute_available_vendors(
        brand,
        shop_id,
        ctx.get("sourcing_map") or {},
        ctx.get("lt_by_vendor_loc") or {},
        ctx.get("lt_by_vendor") or {},
    )

    # Lead time for the SO's ACTUAL vendor at this store, when a PO is already attached. For an
    # unallocated SO there is no vendor yet, so the SLA falls back to the fastest qualifying
    # vendor in available_vendors (already sorted fastest-first).
    vendor_lead_time_days = None
    if po.get("vendor_id") is not None and shop_id is not None:
        vid = str(po["vendor_id"])
        lt_loc = ctx.get("lt_by_vendor_loc") or {}
        lt_v = ctx.get("lt_by_vendor") or {}
        lead = lt_loc.get((vid, str(shop_id)))
        if lead is None:
            lead = lt_v.get(vid)
        vendor_lead_time_days = int(round(lead)) if lead is not None else None

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
        # Individual receipt is authoritative from SpecialOrder.status. `completed` remains a
        # separate lifecycle flag because a completed/cancelled SO is not necessarily received.
        "so_received": so_received,
        "so_received_date": so_received_date.isoformat() if so_received_date else None,
        # Customer
        "customer_id": customer_id,
        "customer_name": _customer_name(customer),
        "customer_phone": customer.get("phone"),
        "customer_email": customer.get("email"),
        # Shopify enrichment (filled in by the dashboard merge; defaults for safety).
        "shopify_match": "none",
        "shopify_match_basis": None,
        "shopify_order_id": None,
        "shopify_order_name": None,
        "shopify_order_url": None,
        "shopify_expected_date": None,
        "shopify_fulfillment_status": None,
        "shopify_financial_status": None,
        "shopify_candidates": [],
        # Manual-link audit: who linked it, when, and whether a hand-made link has since broken.
        "link_provenance": None,
        "link_broken": None,
        "matched_via_closed_order": False,
        # Item / product
        "item_id": item_id,
        "system_sku": item.get("systemSku"),
        # UPC for B2B product research; empty string from Lightspeed -> None.
        "upc": item.get("upc") or None,
        "description": item.get("description") or sale_line.get("description"),
        # Brand + brand-level "Available from" vendors (with per-store lead times).
        "brand": brand,
        "available_vendors": available_vendors,
        # Attached purchase order
        "order_id": order_id,
        "vendor_id": po.get("vendor_id"),
        "vendor_name": po.get("vendor_name"),
        "vendor_lead_time_days": vendor_lead_time_days,
        # The PO's "Order Type v2" custom field ("Replenishment" | "Booking"). Lightspeed
        # only records a value when the non-default choice is picked, so this is the stored
        # value where there is one and the field's default otherwise. None only when the SO
        # has no PO attached at all.
        "order_type": po.get("order_type"),
        "expected_date": expected_date.isoformat() if expected_date else None,
        "ordered_date": ordered_date.isoformat() if ordered_date else None,
        "po_created_date": po_created_date.isoformat() if po_created_date else None,
        # PO-wide context only. This can reflect another line's receipt in a split shipment and
        # must never be presented as the individual SO's received date.
        "po_received_date": po_received_date.isoformat() if po_received_date else None,
        "po_ref_num": po.get("refNum"),
        "days_po_open": days_po_open,
        "po_ordered": ordered_date is not None,
        "po_complete": po_complete,
        "received_started": po_receiving_started,
        "receiving_state": receiving_state,
        # Triage: procurement stage + within-stage attention flag
        "procurement_stage": triage["procurement_stage"],
        "procurement_stage_index": triage["procurement_stage_index"],
        "flag": triage["flag"],
        "days_overdue": triage["days_overdue"],
        "days_in_stage": days_in_stage,
        "is_overdue": triage["flag"] in _OVERDUE_FLAGS,
        # Derivation: workorder | shopify | neither. Upgraded to "shopify" by the match step.
        "source": source,
        # Identity keys. sale_line_id joins to WorkorderItem; order_line_id is the field the
        # Lightspeed allocation write-back sets to attach this SO to a PO line.
        "sale_line_id": str(sale_line_id) if sale_line_id else None,
        "order_line_id": str(order_line.get("orderLineID")) if order_line.get("orderLineID") else None,
        # Attached service workorder (via WorkorderItem.saleLineID), when the SO came off
        # the bench — plus the bench's notes, so the buyer can see what service already
        # knows about the part without leaving the dashboard.
        "workorder_id": workorder_id,
        "workorder_status": workorder.get("status"),
        "workorder_note": workorder.get("note"),
        "workorder_internal_note": workorder.get("internal_note"),
        "workorder_hook_in": workorder.get("hook_in"),
        "workorder_eta_out": workorder.get("eta_out"),
        "workorder_time_in": workorder.get("time_in"),
        # Deep links into Lightspeed
        "ls_item_url": _ls_url("item.views.item", item_id),
        "ls_customer_url": _ls_url("customer.views.customer", customer_id),
        # PO deep link: the Retail web UI purchase-order view (confirmed against the live UI).
        "ls_order_url": _ls_url("purchase.views.purchase", order_id, extra="&tab=main"),
        # Workorder deep link: the beta workorder view, details tab (confirmed against the
        # live UI — the older `workbench.views.workorder` guess did not resolve).
        "workorder_url": _ls_url("workbench.views.beta_workorder", workorder_id, extra="&tab=details"),
    }


_STAGES = ["open_pool", "unordered_po", "ordered", "received"]


def triage_thresholds() -> Dict[str, Any]:
    """The tier boundaries, shipped to the frontend so labels are derived rather than retyped.

    `lib/special-order-triage.ts` previously hardcoded "5-6d" / "7-11d" / "12d+" and
    "1-2d" / "3-7d" / "8d+", which are arithmetic on the constants below. Changing a constant
    silently made every tile label wrong. Three copies of these numbers already existed across
    the codebase; this is the one that stops a fourth.

    Boundaries mirror `_compute_flag` exactly:
      ordered   -> days past the classification date: 1..(mid-1) / mid..max / >max
      pre-order -> real age, healthy below the grace window, then the same ramp offset by it
    """
    return {
        "grace_days": _PREORDER_GRACE_DAYS,
        "overdue_mid_min": _OVERDUE_MID_MIN,
        "overdue_max": _OVERDUE_MAX,
        "ordered": {
            "overdue": [1, _OVERDUE_MID_MIN - 1],
            "overdue_mid": [_OVERDUE_MID_MIN, _OVERDUE_MAX],
            "critical_from": _OVERDUE_MAX + 1,
        },
        "preorder": {
            "healthy_below": _PREORDER_GRACE_DAYS,
            "overdue": [_PREORDER_GRACE_DAYS, _PREORDER_GRACE_DAYS + _OVERDUE_MID_MIN - 2],
            "overdue_mid": [_PREORDER_GRACE_DAYS + _OVERDUE_MID_MIN - 1,
                            _PREORDER_GRACE_DAYS + _OVERDUE_MAX - 1],
            "critical_from": _PREORDER_GRACE_DAYS + _OVERDUE_MAX,
        },
    }


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
    # Strict at this layer: the orchestration wrapper catches the failure, keeps Lightspeed
    # rows visible, and marks Shopify unavailable in meta.sources. Returning [] here would look
    # exactly like a healthy source with zero special orders.
    rows = ShopifyClient().get_open_special_orders(strict=True)
    _shopify_cache["rows"] = rows
    _shopify_cache["fetched_at"] = now
    return rows


# The fallback population changes far more slowly than the open one (it is mostly historical),
# and costs a separate paginated pull, so it gets its own longer TTL.
_shopify_fallback_cache: Dict[str, Any] = {"rows": None, "fetched_at": 0.0}
_SHOPIFY_FALLBACK_TTL_SECONDS = 3600


def _shopify_fallback_rows() -> List[Dict[str, Any]]:
    """`SO`-tagged Shopify orders including fulfilled/archived, for the late-match second pass.

    Kept apart from `_shopify_rows()` on purpose: 728 of ~850 orders in the window are already
    fulfilled. Folding them into the primary index would manufacture ambiguity and fill the
    "unmatched Shopify orders" list with orders that are genuinely finished.
    """
    now = time.time()
    cached = _shopify_fallback_cache["rows"]
    if cached is not None and (now - _shopify_fallback_cache["fetched_at"]) < _SHOPIFY_FALLBACK_TTL_SECONDS:
        return cached
    rows = ShopifyClient().get_recent_special_orders(strict=True)
    _shopify_fallback_cache["rows"] = rows
    _shopify_fallback_cache["fetched_at"] = now
    return rows


def shopify_order_url(order_id: Optional[str]) -> Optional[str]:
    """Admin deep link, only when SHOPIFY_ADMIN_STORE_HANDLE is configured."""
    handle = os.getenv("SHOPIFY_ADMIN_STORE_HANDLE")
    if not handle or not order_id:
        return None
    return f"https://admin.shopify.com/store/{handle}/orders/{order_id}"


def _apply_shopify_match(o: Dict[str, Any], m: Dict[str, Any], today: date) -> None:
    """Writes one LS SO's Shopify match (ETA + order link) onto it and re-buckets its flag.
    For the ordered stage the Shopify ETA is preferred over the PO date as the classification
    date (the customer promise is the date that matters); the pre-order and received stages
    stay age-/contact-driven — `_compute_flag` ignores dates for them."""
    o["shopify_match"] = m["shopify_match"]
    o["shopify_match_basis"] = m.get("shopify_match_basis")
    o["shopify_order_id"] = m["shopify_order_id"]
    o["shopify_order_name"] = m["shopify_order_name"]
    o["shopify_expected_date"] = m["shopify_expected_date"]
    o["shopify_fulfillment_status"] = m.get("shopify_fulfillment_status")
    o["shopify_financial_status"] = m.get("shopify_financial_status")
    o["shopify_order_url"] = shopify_order_url(m["shopify_order_id"])
    o["shopify_candidates"] = m.get("shopify_candidates") or []
    # Who linked this and when (manual links only), and whether a hand-made link has broken.
    o["link_provenance"] = m.get("_link_provenance")
    o["link_broken"] = m.get("_link_broken")
    # True when the match came from the fallback population -- the Shopify order is fulfilled or
    # archived, so it is NOT in the unmatched list and must never be offered as a link target.
    o["matched_via_closed_order"] = bool(m.get("_matched_via_closed_order"))

    # Source attribution. A definite Shopify link makes this a retail SO -- unless it already
    # came off the service bench, in which case the workorder remains the true origin. An
    # ambiguous candidate is NOT a derivation: it stays "neither" until someone resolves it.
    if m["shopify_match"] not in ("none", "ambiguous") and o.get("source") != "workorder":
        o["source"] = "shopify"

    shopify_eta = _parse_ls_date(m["shopify_expected_date"])
    po_eta = _parse_ls_date(o.get("expected_date"))
    stage = o["procurement_stage"]
    classification_date = (shopify_eta or po_eta) if stage == "ordered" else None
    fl = _compute_flag(stage, classification_date, o.get("days_since_creation"), o.get("contacted", False), today)
    o["flag"] = fl["flag"]
    o["days_overdue"] = fl["days_overdue"]
    o["is_overdue"] = fl["flag"] in _OVERDUE_FLAGS


_EMPTY_OVERRIDES = {"links": {}, "blocked": set(), "provenance": {}}


def _manual_link_index(index: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    A SEPARATE index over the Shopify orders a human manually linked that the open
    `SO`-tagged population doesn't contain — a fulfilled order, an untagged one, or one
    older than the pull window. Without this, such a link silently lapsed back to
    auto-matching (`resolve` could only honour ids present in the main index).

    Kept separate on purpose: folding these orders into the main index would make them
    eligible for *automatic* matching by other SOs, and would float any unclaimed one into
    the "Unmatched" Shopify population — a years-old fulfilled order has no business
    appearing there. Empty (no fetch at all) when every link already resolves.
    """
    links = (overrides.get("links") or {}).values()
    missing = sorted({oid for oid in links if oid not in index["orders"]})
    if not missing:
        return shopify_match.build_shopify_index([])
    rows = _safe(lambda: ShopifyClient().get_orders_by_ids(missing), [])
    return shopify_match.build_shopify_index(rows)


def _enrich_with_shopify(
    index: Dict[str, Any],
    orders: List[Dict[str, Any]],
    completed_orders: List[Dict[str, Any]],
    today: date,
    overrides: Optional[Dict[str, Any]] = None,
    fallback_index: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Matches LS SOs to Shopify `SO`-tagged orders and returns the Shopify-only ("Unmatched")
    population. Open SOs claim their match first. Then recently-completed SOs adopt any Shopify
    order still left unmatched — the item is received in Lightspeed (SO completed / "Ready For
    Pickup") but its Shopify order hasn't been fulfilled yet — and join the displayed set so they
    surface under "Matched, received" rather than a false "Unmatched". `completed_orders` is
    appended to `orders` in place for the ones that adopt something.

    `overrides` carries the human decisions: `links` (SO -> Shopify order forced matches,
    basis 'manual') and `blocked` (SO, order) pairs invisible to auto-matching. A link may
    point at an order outside this population entirely (see `_manual_link_index`).

    Only DEFINITE matches consume a Shopify order. An ambiguous SO's candidates still
    surface in the returned Shopify-only population (marked `ambiguous_candidate`) so an
    order is never silently hidden just because two SOs could plausibly claim it.

    Never raises: with no Shopify data, every SO simply stays unmatched / PO-classified.
    """
    if not index["orders"]:
        return []
    ov = overrides or _EMPTY_OVERRIDES
    links: Dict[str, str] = ov.get("links") or {}
    blocked_by_so: Dict[str, set] = {}
    for so_id, oid in ov.get("blocked") or set():
        blocked_by_so.setdefault(so_id, set()).add(oid)

    consumed: set = set()        # definitively claimed -> excluded from "Unmatched"
    ambiguous_ids: set = set()   # candidates of some ambiguous SO -> "possible match"
    # Orders linked by hand that live outside the open population (fulfilled/untagged/old).
    external = _manual_link_index(index, ov)

    provenance: Dict[str, Any] = ov.get("provenance") or {}

    def resolve(o: Dict[str, Any]) -> Dict[str, Any]:
        so_id = str(o.get("special_order_id"))
        manual_oid = links.get(so_id)
        if manual_oid and manual_oid in index["orders"]:
            m = shopify_match.manual_match(index, manual_oid)
            m["_link_provenance"] = provenance.get(so_id)
            return m
        if manual_oid and manual_oid in external["orders"]:
            m = shopify_match.manual_match(external, manual_oid)
            m["_link_provenance"] = provenance.get(so_id)
            return m
        m = shopify_match.match_special_order(
            o.get("customer_email"),
            o.get("system_sku"),
            index,
            customer_phone=o.get("customer_phone"),
            customer_name=o.get("customer_name"),
            blocked=frozenset(blocked_by_so.get(so_id, set())),
        )
        if manual_oid:
            # Someone linked this by hand and Shopify no longer returns that order at all
            # (deleted, or re-created with a new id). It used to lapse silently back to
            # auto-matching, so a broken link looked exactly like a never-linked one and the
            # human decision vanished without trace. Flag it instead.
            m["_link_broken"] = manual_oid
            m["_link_provenance"] = provenance.get(so_id)
            return m

        # Second pass: a Lightspeed special order is routinely created before, or long after,
        # its Shopify order, and a Shopify order can be FULFILLED for its other lines while the
        # special-order line is still outstanding. Either way the order has left the open
        # population and the first pass cannot see it (verified: SO 43605 created 2026-04-30,
        # its SO-tagged order #233420 created 2026-05-27 and now fulfilled).
        #
        # Only the identity-backed tiers are honoured here. SKU-only matching across ~850
        # historical orders would be near-meaningless, so a fallback result that is not
        # definite, or that rests on sku_only, is discarded rather than guessed at.
        if m["shopify_match"] == "none" and fallback_index and fallback_index.get("orders"):
            fb = shopify_match.match_special_order(
                o.get("customer_email"),
                o.get("system_sku"),
                fallback_index,
                customer_phone=o.get("customer_phone"),
                customer_name=o.get("customer_name"),
                blocked=frozenset(blocked_by_so.get(so_id, set())),
            )
            if fb["shopify_match"] == "matched" and fb.get("shopify_match_basis") != "sku_only":
                fb["_matched_via_closed_order"] = True
                return fb
        return m

    for o in orders:
        m = resolve(o)
        if m["shopify_match"] == "matched":
            consumed |= m["_candidates"]
        elif m["shopify_match"] == "ambiguous":
            ambiguous_ids |= m["_candidates"]
        _apply_shopify_match(o, m, today)

    for co in completed_orders:
        m = resolve(co)
        # A completed SO only joins the display when it DEFINITELY claims a Shopify order
        # no open SO already did; ambiguous completed SOs would just add noise.
        if m["shopify_match"] != "matched" or m["_candidates"] <= consumed:
            continue
        consumed |= m["_candidates"]
        _apply_shopify_match(co, m, today)
        orders.append(co)

    unmatched = shopify_match.shopify_only_orders(index, consumed)
    for u in unmatched:
        u["shopify_order_url"] = shopify_order_url(u["order_id"])
        u["ambiguous_candidate"] = u["order_id"] in ambiguous_ids
    return unmatched


def _raw_so_system_sku(so: Dict[str, Any]) -> str:
    """The item systemSku off a raw SpecialOrder dict, normalized like the Shopify index keys."""
    return str(((so.get("SaleLine") or {}).get("Item") or {}).get("systemSku") or "").strip()


def _raw_so_item_id(so: Dict[str, Any]) -> Optional[str]:
    """The item itemID off a raw SpecialOrder dict, used to resolve the SKU's brand."""
    item_id = ((so.get("SaleLine") or {}).get("Item") or {}).get("itemID")
    return str(item_id) if item_id is not None else None


def _sort_orders(orders: List[Dict[str, Any]]) -> None:
    """Flagged items first, ranked by flag severity, then most-overdue / oldest within that."""
    orders.sort(
        key=lambda o: (
            -_FLAG_RANK.get(o["flag"], 0),
            -(o["days_overdue"] or 0),
            -(o["days_since_creation"] or 0),
        )
    )


# Snapshot of the last full build's normalized-but-unenriched rows, so a Shopify-side write
# (ETA edit, manual match/unmatch) can rebuild the dashboard by re-running just the Shopify
# pull + matching over these rows instead of paying the whole Lightspeed walk again.
_pre_enrichment: Dict[str, Any] = {"orders": None, "completed": None, "meta": None}


def get_special_order_dashboard(client: Optional[LightspeedClient] = None) -> Dict[str, Any]:
    """
    Live-fetches open special orders and their attached POs, then returns
    { "orders": [...normalized, sorted by days_overdue desc...], "summary": {...} }.
    """
    client = client or LightspeedClient()
    today = date.today()
    shop_names = {v: k for k, v in client.shop_id_map.items()}
    source_health: Dict[str, Any] = {}

    # Open SOs (always shown), recently-completed SOs (candidates to adopt a still-open Shopify
    # order), and the Shopify rows are all independent — fan them out concurrently.
    with ThreadPoolExecutor(max_workers=4) as executor:
        open_future = executor.submit(client.get_special_orders)
        completed_future = executor.submit(
            _safe, lambda: client.get_completed_special_orders(strict=True), [],
            source_health, "lightspeed_completed"
        )
        shopify_future = executor.submit(
            _safe, _shopify_rows, [], source_health, "shopify_open_special_orders"
        )
        # Fallback population for the late-match second pass. Degrades to empty rather than
        # failing the dashboard, since it only ever adds matches.
        fallback_future = executor.submit(
            _safe, _shopify_fallback_rows, [], source_health, "shopify_recent_fallback"
        )
        special_orders = open_future.result()
        source_health["lightspeed_open_special_orders"] = {
            "status": "ok",
            "checked_at": datetime.utcnow().isoformat() + "Z",
            "record_count": len(special_orders),
        }
        completed_all = completed_future.result()
        shop_rows = shopify_future.result()
        fallback_rows = fallback_future.result()

    index = shopify_match.build_shopify_index(shop_rows)
    fallback_index = shopify_match.build_shopify_index(fallback_rows) if fallback_rows else None
    candidate_skus = set(index["by_sku"].keys())
    # Narrow the completed pool to SOs whose SKU could match an open Shopify order — turns a wide
    # recency window into a handful before we pay for customer/PO resolution.
    completed_candidates = [so for so in completed_all if _raw_so_system_sku(so) in candidate_skus]

    # PO + customer + workorder lookups cover both the open SOs and the completed candidates.
    # The brand->vendor sourcing map, lead times, and manual match overrides are independent of
    # Lightspeed, so fan them all out together; each auxiliary fetch degrades to empty rather
    # than failing the dashboard.
    sos_to_resolve = special_orders + completed_candidates
    order_ids = [
        (so.get("OrderLine") or {}).get("orderID") or so.get("orderID")
        for so in sos_to_resolve
    ]
    customer_ids = [so.get("customerID") for so in sos_to_resolve]
    item_ids = [_raw_so_item_id(so) for so in sos_to_resolve]
    sale_line_ids = [
        so.get("saleLineID") or (so.get("SaleLine") or {}).get("saleLineID")
        for so in sos_to_resolve
    ]
    with ThreadPoolExecutor(max_workers=7) as executor:
        order_future = executor.submit(
            _safe, lambda: client.get_orders_by_ids(order_ids, strict=True), {},
            source_health, "lightspeed_purchase_orders"
        )
        customer_future = executor.submit(
            _safe, lambda: client.get_customers_by_ids(customer_ids, strict=True), {},
            source_health, "lightspeed_customers"
        )
        workorder_future = executor.submit(
            _safe, lambda: client.get_workorders_by_sale_line_ids(sale_line_ids, strict=True), {},
            source_health, "lightspeed_workorders"
        )
        brand_future = executor.submit(
            _safe, lambda: bigquery_sync.fetch_item_brands(item_ids), {},
            source_health, "bigquery_item_brands"
        )
        sourcing_future = executor.submit(
            _safe, bigquery_sync.fetch_brand_vendor_sourcing, {},
            source_health, "bigquery_brand_vendor_sourcing"
        )
        leadtime_future = executor.submit(
            _safe, bigquery_sync.build_lead_time_lookup, ({}, {}),
            source_health, "bigquery_vendor_lead_times"
        )
        overrides_future = executor.submit(
            _safe, lambda: bigquery_sync.fetch_so_match_overrides(strict=True), _EMPTY_OVERRIDES,
            source_health, "bigquery_match_overrides"
        )
        order_map = order_future.result()
        customer_map = customer_future.result()
        workorder_map = workorder_future.result()
        brand_map = brand_future.result()
        sourcing_map = sourcing_future.result()
        lt_by_vendor_loc, lt_by_vendor = leadtime_future.result()
        overrides = overrides_future.result()

    sourcing_ctx = {
        "brand_map": brand_map,
        "sourcing_map": sourcing_map,
        "lt_by_vendor_loc": lt_by_vendor_loc,
        "lt_by_vendor": lt_by_vendor,
        "workorder_map": workorder_map,
    }
    orders = [_normalize(so, order_map, customer_map, shop_names, today, sourcing_ctx) for so in special_orders]
    completed_orders = [_normalize(so, order_map, customer_map, shop_names, today, sourcing_ctx) for so in completed_candidates]

    # Snapshot the pre-enrichment rows for the cheap post-write rebuild (re_enrich_dashboard).
    _pre_enrichment["orders"] = copy.deepcopy(orders)
    _pre_enrichment["completed"] = copy.deepcopy(completed_orders)

    # Enrich with the Shopify ETA; matched-completed SOs are appended to `orders`, and the
    # genuinely-orphaned Shopify orders come back as the "Unmatched" population.
    shopify_only = _enrich_with_shopify(index, orders, completed_orders, today, overrides,
                                        fallback_index=fallback_index)

    _sort_orders(orders)
    data_status = (
        "degraded"
        if any(entry.get("status") != "ok" for entry in source_health.values())
        else "ok"
    )
    dashboard_meta = {
        "data_status": data_status,
        "source_health": source_health,
        "sources": _source_statuses(source_health),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    _pre_enrichment["meta"] = copy.deepcopy(dashboard_meta)
    return {
        "orders": orders,
        "summary": _summarize(orders),
        "shopify_only": shopify_only,
        "meta": dashboard_meta,
    }


def re_enrich_dashboard() -> Optional[Dict[str, Any]]:
    """
    Cheap rebuild after a Shopify-side write (ETA edit, manual match/unmatch): re-runs the
    Shopify pull + matching + flag re-bucketing over the last full build's normalized
    Lightspeed rows, skipping the expensive Lightspeed walk entirely. The caller should have
    invalidated `_shopify_cache` first if the write changed Shopify data.

    Returns the fresh dashboard payload, or None when no prior full build exists (cold
    process) — the caller then falls back to a full rebuild.
    """
    pre_orders = _pre_enrichment.get("orders")
    if pre_orders is None:
        return None
    today = date.today()
    orders = copy.deepcopy(pre_orders)
    completed_orders = copy.deepcopy(_pre_enrichment.get("completed") or [])
    index = shopify_match.build_shopify_index(_shopify_rows())
    # Manual decisions are authoritative. If the ledger cannot be read after a write, abort
    # this cheap rebuild rather than briefly presenting every saved link as if it disappeared.
    # The caller preserves/invalidates the cache and the next full build reports source health.
    overrides = bigquery_sync.fetch_so_match_overrides(strict=True)
    # The fallback population is cached for an hour, so re-enriching after a write reuses it
    # rather than paying a second paginated Shopify pull.
    fb_rows = _safe(_shopify_fallback_rows, [])
    fallback_index = shopify_match.build_shopify_index(fb_rows) if fb_rows else None
    shopify_only = _enrich_with_shopify(index, orders, completed_orders, today, overrides,
                                        fallback_index=fallback_index)
    _sort_orders(orders)
    # Re-enrichment only refreshes Shopify/matching data. Preserve the last full walk's health
    # and label this as partial so a user does not mistake it for a fresh Lightspeed pull.
    prior_meta = copy.deepcopy(_pre_enrichment.get("meta") or {})
    source_health = prior_meta.get("source_health") or {}
    source_health["shopify_open_special_orders"] = {
        "status": "ok",
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "record_count": len(index.get("orders") or {}),
    }
    prior_meta.update({
        "source_health": source_health,
        "sources": _source_statuses(source_health),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "partial_refresh": True,
    })
    return {
        "orders": orders,
        "summary": _summarize(orders),
        "shopify_only": shopify_only,
        "meta": prior_meta,
    }
