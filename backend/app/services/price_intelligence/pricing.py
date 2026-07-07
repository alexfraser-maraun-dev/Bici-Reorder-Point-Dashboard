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
