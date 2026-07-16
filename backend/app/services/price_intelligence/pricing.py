"""Lightspeed price push with server-side guards (feature c).

The floor is the highest of: the MAP floor (for MAP-tagged items this is our own
retail price, i.e. we won't advertise a MAP item below its current price; an
explicit map_price override wins if set), the per-item manual override, and
cost * (1 + margin floor). The server re-validates
on push — the preview verdict from the client is never trusted — and a push that
moves price more than MAX_PUSH_CHANGE_PCT is rejected outright as a fat-finger
guard. Every attempt is audit-logged to pi_price_push_log.
"""
from . import config, repository


def compute_floor(item: dict):
    """Returns (floor_price or None, floor_source)."""
    candidates = []
    if item.get("is_map"):
        # MAP == our default price for tagged items (map_price override wins).
        map_floor = item.get("map_price") or item.get("current_retail")
        if map_floor:
            candidates.append((float(map_floor), "map_price"))
    if item.get("min_price_override"):
        candidates.append((float(item["min_price_override"]), "min_price_override"))
    if item.get("current_cost"):
        candidates.append(
            (round(float(item["current_cost"]) * (1 + config.MARGIN_FLOOR_PCT), 2),
             "cost_margin_floor")
        )
    if not candidates:
        return None, None
    return max(candidates, key=lambda c: c[0])


def _get_tracked_item(item_id: str) -> dict:
    rows = [r for r in repository.get_tracked_products() if str(r["item_id"]) == str(item_id)]
    if not rows:
        raise ValueError(f"Item {item_id} is not in the tracked-products list")
    return rows[0]


def build_preview(item_id: str, new_price: float) -> dict:
    item = _get_tracked_item(item_id)
    floor, floor_source = compute_floor(item)
    current = item.get("current_retail")
    market_rows = [
        r for r in repository.get_tracked_products_with_market()
        if str(r["item_id"]) == str(item_id)
    ]
    market = market_rows[0] if market_rows else {}
    guard = "ok"
    reasons = []
    if floor is not None and new_price < floor - 0.005:
        guard = "below_floor"
        reasons.append(f"below {floor_source} of {floor:.2f}")
    if current and current > 0:
        change_pct = abs(new_price - float(current)) / float(current)
        if change_pct > config.MAX_PUSH_CHANGE_PCT:
            guard = "rejected"
            reasons.append(
                f"change of {change_pct:.0%} exceeds the {config.MAX_PUSH_CHANGE_PCT:.0%} sanity limit"
            )
    return {
        "item_id": str(item_id),
        "title": item.get("title"),
        "sku": item.get("sku"),
        "current_price": current,
        "proposed_price": round(float(new_price), 2),
        "floor_price": floor,
        "floor_source": floor_source,
        "is_map": bool(item.get("is_map")),
        "map_price": item.get("map_price"),
        "current_cost": item.get("current_cost"),
        "market_min": market.get("market_min"),
        "market_min_in_stock": market.get("market_min_in_stock"),
        "market_median": market.get("market_median"),
        "guard": guard,
        "guard_reasons": reasons,
    }


def push_price(item_id: str, new_price: float, actor: str, override_floor: bool = False) -> dict:
    """Validates and pushes one item's Default price to Lightspeed."""
    preview = build_preview(item_id, new_price)
    if preview["guard"] == "rejected":
        raise ValueError("; ".join(preview["guard_reasons"]))
    if preview["guard"] == "below_floor" and not override_floor:
        raise ValueError(
            "Proposed price is below the floor "
            f"({preview['floor_source']} = {preview['floor_price']:.2f}); "
            "explicit override required"
        )

    from app.services.lightspeed_client import LightspeedClient
    item = _get_tracked_item(item_id)
    client = LightspeedClient()
    log_row = {
        "pushed_at": repository.utcnow_iso(),
        "item_id": str(item_id),
        "sku": item.get("sku"),
        "old_price": preview["current_price"],
        "new_price": preview["proposed_price"],
        "floor_price": preview["floor_price"],
        "guard_result": "overridden" if preview["guard"] == "below_floor" else "passed",
        "actor": actor or "Dashboard",
        "status": "success",
        "error": None,
        "run_context": "price_intel_ui",
    }
    try:
        result = client.update_item_price(str(item_id), preview["proposed_price"])
        if result is None:
            raise RuntimeError("Lightspeed rejected the price update (see backend logs)")
    except Exception as e:
        log_row["status"] = "failed"
        log_row["error"] = str(e)[:500]
        repository.log_price_push(log_row)
        raise
    repository.log_price_push(log_row)
    repository.update_tracked_current_retail(item_id, preview["proposed_price"])
    repository.invalidate_pi_caches()
    return {**preview, "status": "success"}


# --- whole-matrix push -------------------------------------------------------------
#
# A matrix push reprices every variant of a Lightspeed matrix at once, so it gets
# two safety gates beyond the per-item guards:
#   1. The variant list comes live from the Lightspeed API, never from BigQuery —
#      a stale snapshot must not decide which items get repriced. The anchor item
#      must itself appear in that live list or everything aborts.
#   2. The execute call must echo the exact variant-id list it previewed; if the
#      matrix changed in between, the push is rejected before any write.


def _extract_default_price(ls_item: dict):
    prices = (ls_item.get("Prices") or {}).get("ItemPrice") or []
    if isinstance(prices, dict):
        prices = [prices]
    for p in prices:
        if str(p.get("useType") or "").lower() == "default":
            try:
                return float(p.get("amount"))
            except (TypeError, ValueError):
                return None
    return None


def _matrix_variants_live(matrix_id: str) -> list:
    from app.services.lightspeed_client import LightspeedClient
    items = LightspeedClient().get_matrix_items(str(matrix_id))
    variants = []
    for it in items:
        if str(it.get("archived", "false")).lower() == "true":
            continue
        attrs = it.get("ItemAttributes") or {}
        try:
            cost = float(it.get("defaultCost") or 0) or None
        except (TypeError, ValueError):
            cost = None
        variants.append({
            "item_id": str(it.get("itemID")),
            "description": it.get("description"),
            "system_sku": it.get("systemSku"),
            "attributes": [a for a in (attrs.get("attribute1"), attrs.get("attribute2"),
                                       attrs.get("attribute3")) if a],
            "current_price": _extract_default_price(it),
            "cost": cost,
        })
    if not variants:
        raise ValueError(f"Lightspeed returned no active variants for matrix {matrix_id}")
    return variants


def build_matrix_preview(item_id: str, new_price: float) -> dict:
    """Per-variant guard preview for a whole-matrix price push (anchor = the tracked
    item the user clicked). One fat-finger-rejected variant blocks the entire matrix:
    a matrix push is all-or-nothing by intent."""
    anchor = _get_tracked_item(item_id)
    matrix_id = anchor.get("item_matrix_id")
    if not matrix_id:
        raise ValueError(f"Item {item_id} does not belong to a matrix")
    variants = _matrix_variants_live(matrix_id)
    if str(item_id) not in {v["item_id"] for v in variants}:
        raise ValueError(
            f"Item {item_id} is not in Lightspeed's live variant list for matrix "
            f"{matrix_id} — aborting to avoid repricing the wrong matrix"
        )

    tracked = {
        str(t["item_id"]): t for t in repository.get_tracked_products()
        if str(t.get("item_matrix_id") or "") == str(matrix_id)
    }
    # Sizes share MSRP, so the strictest MAP floor among tracked variants covers
    # every variant — an untracked sibling is still the same MAP-governed model.
    map_floors = [
        float(t.get("map_price") or t.get("current_retail"))
        for t in tracked.values()
        if t.get("is_map") and (t.get("map_price") or t.get("current_retail"))
    ]
    matrix_map_floor = max(map_floors) if map_floors else None

    new_price = round(float(new_price), 2)
    out = []
    for v in sorted(variants, key=lambda x: (x["attributes"], x["item_id"])):
        t = tracked.get(v["item_id"]) or {}
        candidates = []
        if matrix_map_floor:
            candidates.append((matrix_map_floor, "map_price"))
        if t.get("min_price_override"):
            candidates.append((float(t["min_price_override"]), "min_price_override"))
        cost = t.get("current_cost") or v.get("cost")
        if cost:
            candidates.append(
                (round(float(cost) * (1 + config.MARGIN_FLOOR_PCT), 2), "cost_margin_floor"))
        floor, floor_source = max(candidates, key=lambda c: c[0]) if candidates else (None, None)

        guard, reasons = "ok", []
        if floor is not None and new_price < floor - 0.005:
            guard = "below_floor"
            reasons.append(f"below {floor_source} of {floor:.2f}")
        current = v.get("current_price")
        if current:
            change_pct = abs(new_price - float(current)) / float(current)
            if change_pct > config.MAX_PUSH_CHANGE_PCT:
                guard = "rejected"
                reasons.append(
                    f"change of {change_pct:.0%} exceeds the "
                    f"{config.MAX_PUSH_CHANGE_PCT:.0%} sanity limit"
                )
        else:
            reasons.append("no current Default price in Lightspeed")
        out.append({
            **v, "tracked": bool(t), "floor_price": floor, "floor_source": floor_source,
            "guard": guard, "guard_reasons": reasons,
        })

    n_rejected = sum(1 for v in out if v["guard"] == "rejected")
    n_below = sum(1 for v in out if v["guard"] == "below_floor")
    guard_reasons = []
    if n_rejected:
        guard_reasons.append(f"{n_rejected} variant(s) fail the fat-finger sanity limit")
    if n_below:
        guard_reasons.append(f"{n_below} variant(s) are below their price floor")
    warnings = []
    current_prices = {round(v["current_price"], 2) for v in out if v.get("current_price")}
    if len(current_prices) > 1:
        warnings.append(
            "Variants do not currently share one price — this push flattens all of "
            "them to the new price."
        )
    return {
        "item_matrix_id": str(matrix_id),
        "matrix_description": anchor.get("matrix_description"),
        "anchor_item_id": str(item_id),
        "proposed_price": new_price,
        "variant_count": len(out),
        "variants": out,
        "guard": "rejected" if n_rejected else ("below_floor" if n_below else "ok"),
        "guard_reasons": guard_reasons,
        "warnings": warnings,
    }


def push_matrix_price(item_id: str, new_price: float, actor: str,
                      override_floor: bool = False, expected_item_ids=None) -> dict:
    """Validates and pushes one price to every variant of the anchor item's matrix.

    Guards are recomputed here from live Lightspeed data — the client's preview is
    never trusted — and the caller must echo the exact variant-id list it previewed
    so a matrix that changed since the preview aborts before any write."""
    preview = build_matrix_preview(item_id, new_price)
    if preview["guard"] == "rejected":
        raise ValueError("; ".join(preview["guard_reasons"]) + " — matrix push blocked")
    if preview["guard"] == "below_floor" and not override_floor:
        raise ValueError(
            "; ".join(preview["guard_reasons"]) + "; explicit override required")
    live_ids = sorted(v["item_id"] for v in preview["variants"])
    if sorted(str(i) for i in (expected_item_ids or [])) != live_ids:
        raise ValueError(
            "The matrix's variant list changed since the preview — nothing was "
            "pushed. Re-open the dialog and review the variants again."
        )

    from app.services.lightspeed_client import (
        LightspeedClient, LightspeedWriteBlocked, lightspeed_writes_enabled,
    )
    # Check the write gate before the loop: a shut gate should abort the whole
    # push cleanly, not log one failed row per variant.
    if not lightspeed_writes_enabled():
        raise LightspeedWriteBlocked(
            "Live Lightspeed writes are disabled — matrix push aborted before any write."
        )
    client = LightspeedClient()
    results, failures = [], []
    first_error = None
    for v in preview["variants"]:
        log_row = {
            "pushed_at": repository.utcnow_iso(),
            "item_id": v["item_id"],
            "sku": v.get("system_sku"),
            "old_price": v.get("current_price"),
            "new_price": preview["proposed_price"],
            "floor_price": v.get("floor_price"),
            "guard_result": "overridden" if v["guard"] == "below_floor" else "passed",
            "actor": actor or "Dashboard",
            "status": "success",
            "error": None,
            "run_context": "price_intel_matrix",
        }
        try:
            result = client.update_item_price(v["item_id"], preview["proposed_price"])
            if result is None:
                raise RuntimeError("Lightspeed rejected the price update (see backend logs)")
            if v["tracked"]:
                repository.update_tracked_current_retail(v["item_id"], preview["proposed_price"])
        except Exception as e:
            log_row["status"] = "failed"
            log_row["error"] = str(e)[:500]
            failures.append(v)
            first_error = first_error or str(e)
        repository.log_price_push(log_row)
        results.append({"item_id": v["item_id"], "description": v.get("description"),
                        "status": log_row["status"], "error": log_row["error"]})
    repository.invalidate_pi_caches()
    if failures:
        done = len(preview["variants"]) - len(failures)
        raise RuntimeError(
            f"Matrix push incomplete: {done} of {len(preview['variants'])} variants "
            "updated; failed: "
            + ", ".join(f"{v['item_id']} ({v.get('description') or 'no description'})"
                        for v in failures)
            + (f" — first error: {first_error}" if first_error else "")
        )
    return {**{k: preview[k] for k in
               ("item_matrix_id", "matrix_description", "proposed_price", "variant_count")},
            "status": "success", "results": results}
