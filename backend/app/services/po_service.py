"""
Purchase-order reconciliation service.

Bridges the replenishment engine's recommendations to actionable purchase-order
drafts, reconciling each suggested buy against purchase orders that are already
open (created but not yet received) in Lightspeed so that repeated runs of the
tool never create duplicate POs.

Key invariant: the engine's `qty_to_order` is already NET of `on_order` units
(inventory_position includes open-PO quantities). Reconciliation therefore only
decides *where* those units should go, never re-subtracts open-PO quantities.
"""

import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional


# Reconciliation tags stored on each draft line.
NEW_PO = "new_po"                      # no suitable open PO exists -> create one
APPEND_TO_OPEN_PO = "append_to_open_po"  # add/top-up a line on an existing open PO
ALREADY_ON_PO = "already_on_po"        # need already covered by an open PO -> no action


def _order_vendor(rec: Dict[str, Any]) -> (Optional[str], Optional[str]):
    """
    Resolves which vendor a line should be ordered from: the brand's preferred
    vendor when a sourcing rule drove the lead time, otherwise the item's vendor.
    Returns (vendor_id, vendor_name) as strings (or None).
    """
    if rec.get("lead_time_source") == "preferred_vendor" and rec.get("lead_time_vendor_id"):
        return str(rec.get("lead_time_vendor_id")), rec.get("lead_time_vendor")
    vid = rec.get("vendor_id")
    return (str(vid) if vid is not None else None), rec.get("vendor")


def _find_open_po_and_line(open_orders: List[Dict[str, Any]], item_id: str):
    """
    Given the open orders for a vendor+shop, returns (target_order, existing_line)
    where target_order is the open PO we'd append to (first one, if any) and
    existing_line is the line on it already carrying this item (if any).
    """
    if not open_orders:
        return None, None
    target_order = open_orders[0]
    existing_line = None
    for order in open_orders:
        for line in order.get("OrderLine", []):
            if str(line.get("itemID")) == str(item_id):
                # Prefer the order that already carries this item as the target.
                return order, line
    return target_order, existing_line


def reconcile_recommendations(
    recs: List[Dict[str, Any]],
    client,
    created_by: str = "UI_User",
) -> List[Dict[str, Any]]:
    """
    Groups recommendations by (order vendor, shop) and reconciles each line
    against currently-open Lightspeed POs.

    Returns a list of draft dicts ready to persist, each shaped as:
        {
          "draft_id", "vendor_id", "vendor_name", "shop_id",
          "status", "created_by", "created_at", "updated_at",
          "lightspeed_order_id" (target open PO if appending, else None),
          "notes",
          "lines": [ <draft line dict>, ... ]
        }
    Lines whose need is fully covered (qty_to_order <= 0) are only emitted when
    they sit on an open PO (tagged already_on_po, for visibility); otherwise they
    are skipped entirely.
    """
    shop_id_map = client.shop_id_map  # {location_name: shop_id}

    # Group recs by (vendor_id, shop_id).
    groups: Dict[tuple, Dict[str, Any]] = {}
    for rec in recs:
        vendor_id, vendor_name = _order_vendor(rec)
        location = rec.get("location")
        shop_id = shop_id_map.get(location)
        if not vendor_id or not shop_id:
            # Can't route this line to a vendor/shop; skip it.
            continue
        key = (vendor_id, shop_id)
        groups.setdefault(key, {"vendor_name": vendor_name, "recs": []})
        groups[key]["recs"].append(rec)

    drafts: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat()

    for (vendor_id, shop_id), group in groups.items():
        # Open POs for this vendor+shop drive the reconciliation for the group.
        open_orders = client.get_open_orders(vendor_id=vendor_id, shop_id=shop_id)
        target_order_id = str(open_orders[0].get("orderID")) if open_orders else None

        draft_id = str(uuid.uuid4())
        lines: List[Dict[str, Any]] = []

        for rec in group["recs"]:
            item_id = str(rec.get("system_id"))
            qty = int(rec.get("qty_to_order") or 0)
            _, existing_line = _find_open_po_and_line(open_orders, item_id)

            if qty <= 0:
                if existing_line is not None:
                    reconciliation = ALREADY_ON_PO
                else:
                    continue  # fully covered by on-hand; nothing to surface
            elif open_orders:
                reconciliation = APPEND_TO_OPEN_PO
            else:
                reconciliation = NEW_PO

            lines.append({
                "draft_id": draft_id,
                "sku": rec.get("sku"),
                "item_id": item_id,
                "location_id": str(rec.get("location_id") or shop_id),
                "quantity": qty,
                "unit_cost": _safe_float(rec.get("unit_cost") or rec.get("default_cost")),
                "source": "recommendation",
                "recommendation_run_id": rec.get("recommendation_run_id"),
                "reconciliation": reconciliation,
                "target_lightspeed_order_id": target_order_id if reconciliation == APPEND_TO_OPEN_PO else None,
            })

        if not lines:
            continue

        drafts.append({
            "draft_id": draft_id,
            "vendor_id": vendor_id,
            "vendor_name": group["vendor_name"],
            "shop_id": shop_id,
            "status": "draft",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "lightspeed_order_id": target_order_id,
            "notes": None,
            "lines": lines,
        })

    return drafts


def _safe_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def push_draft(draft: Dict[str, Any], client, triggered_by: str = "UI_User") -> List[Dict[str, Any]]:
    """
    Pushes a single reconciled draft to Lightspeed and returns audit rows.

    Re-checks open-PO state at push time (idempotency): a new PO is created at
    most once per draft, and lines that already exist on the target open PO are
    topped up rather than duplicated. The additional quantity applied is the
    line's `quantity` (already net of on-order), regardless of path.
    """
    vendor_id = draft["vendor_id"]
    shop_id = draft["shop_id"]
    draft_id = draft["draft_id"]

    # Snapshot current open-PO state once for this push.
    open_orders = client.get_open_orders(vendor_id=vendor_id, shop_id=shop_id)
    target_order = open_orders[0] if open_orders else None
    created_order_id = None  # lazily create a new PO only if a line needs one
    audit: List[Dict[str, Any]] = []

    def log(line, action, qty, order_id, line_id, status, error=None):
        audit.append({
            "push_id": str(uuid.uuid4()),
            "draft_id": draft_id,
            "sku": line.get("sku"),
            "item_id": line.get("item_id"),
            "location_id": line.get("location_id"),
            "action": action,
            "quantity": int(qty) if qty is not None else None,
            "lightspeed_order_id": str(order_id) if order_id is not None else None,
            "lightspeed_order_line_id": str(line_id) if line_id is not None else None,
            "status": status,
            "error_message": error,
            "triggered_by": triggered_by,
            "created_at": datetime.utcnow().isoformat(),
        })

    for line in draft.get("lines", []):
        if line.get("reconciliation") == ALREADY_ON_PO:
            continue  # nothing to push
        qty = int(line.get("quantity") or 0)
        if qty <= 0:
            continue
        item_id = str(line.get("item_id"))
        price = line.get("unit_cost")

        try:
            # Does this item already sit on an open PO? Top it up to avoid a duplicate line.
            _, existing_line = _find_open_po_and_line(open_orders, item_id)
            if existing_line is not None:
                target_id = None
                for order in open_orders:
                    for l in order.get("OrderLine", []):
                        if str(l.get("orderLineID")) == str(existing_line.get("orderLineID")):
                            target_id = order.get("orderID")
                new_qty = int(existing_line.get("quantity") or 0) + qty
                updated = client.update_order_line(existing_line.get("orderLineID"), new_qty)
                if updated:
                    log(line, "updated_line", qty, target_id, existing_line.get("orderLineID"), "success")
                else:
                    log(line, "updated_line", qty, target_id, existing_line.get("orderLineID"), "failed", "Lightspeed line update failed")
                continue

            # Otherwise append to the existing open PO, or create a new one first.
            if target_order is not None:
                order_id = target_order.get("orderID")
            else:
                if created_order_id is None:
                    new_order = client.create_order(vendor_id, shop_id, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S%z"))
                    if not new_order:
                        log(line, "create_po", qty, None, None, "failed", "Lightspeed order creation failed")
                        continue
                    created_order_id = new_order.get("orderID")
                order_id = created_order_id

            new_line = client.add_order_line(order_id, item_id, qty, price)
            if new_line:
                action = "appended_line" if target_order is not None else "created_po_line"
                log(line, action, qty, order_id, new_line.get("orderLineID"), "success")
            else:
                log(line, "appended_line", qty, order_id, None, "failed", "Lightspeed line add failed")
        except Exception as e:
            log(line, "error", qty, None, None, "failed", str(e))

    return audit
