"""Stage derivation and persistence for the special-order SLA.

The dashboard recomputes special orders into an in-process cache every few minutes and then
throws them away, so no dwell time has ever been measurable. This module turns each rebuild
into a durable record of *when* each special order entered each procurement stage.

Two design points carry the whole thing:

**Timestamps are derived, not observed.** Observation time is only correct for an SO that
appears while the sweep is running. For a backlog item already 92 days old it would read
"entered today", and that backlog is exactly what the SLA exists to surface. So every stage
whose entry Lightspeed already timestamps takes that timestamp (``entered_source='derived'``)
and only genuinely unstamped transitions fall back to observation.

**Absence never means closure.** A truncated Lightspeed read looks identical to a shrinking
population, so a sweep that trusted absence would mass-close hundreds of special orders on one
API hiccup. ``LightspeedClient.get_special_orders`` now raises rather than returning a partial
list, and ``persist_observations`` additionally refuses to write when the population collapses.
"""

from typing import Any, Dict, List, Optional

# A sweep that sees far fewer special orders than the last one is far more likely to be a
# degraded read than a real change. Below this fraction of the previous population we skip the
# write entirely and leave the prior record standing.
POPULATION_DROP_GUARD = 0.75

# Which field carries the authoritative entry timestamp for each stage. Order matters only for
# readability; the stage on the row decides which entry is written.
_STAGE_TIMESTAMP_FIELD = {
    "open_pool": "created_date",        # SaleLine.createTime -- the customer asked
    "unordered_po": "po_created_date",  # Order.createTime -- a draft PO was opened
    "ordered": "ordered_date",          # Order.orderedDate -- placed with the vendor
    "received": "po_received_date",     # Order.receivedDate -- the item landed
}

_META_LAST_POPULATION = "so_sweep_last_population"


def derive_stage_entry(row: Dict[str, Any], observed_at: str) -> Optional[Dict[str, Any]]:
    """One special order's current stage plus when it entered that stage.

    Returns None for a row with no usable identity. ``entered_source`` is ``'derived'`` when
    Lightspeed supplied the timestamp and ``'observed'`` when we had to fall back to now --
    the latter is a measurement floor, not a real date, and the scoreboard should say so.
    """
    so_id = row.get("special_order_id")
    stage = row.get("procurement_stage")
    if not so_id or not stage:
        return None

    entered_at = row.get(_STAGE_TIMESTAMP_FIELD.get(stage) or "")
    entered_source = "derived"
    if not entered_at:
        # Fall back down the chain: a received SO whose PO has no receivedDate is still at
        # least as old as its ordered date, and so on. Only when nothing at all is known do
        # we stamp the observation time.
        for fallback in ("ordered_date", "po_created_date", "created_date"):
            if row.get(fallback):
                entered_at = row[fallback]
                entered_source = "observed"  # approximated from an earlier stage
                break
    if not entered_at:
        entered_at = observed_at[:10]
        entered_source = "observed"

    return {
        "special_order_id": str(so_id),
        "stage": stage,
        "entered_at": str(entered_at)[:10],
        "entered_source": entered_source,
        "shop_id": row.get("shop_id"),
        "source": row.get("source"),
        "order_id": row.get("order_id"),
        "vendor_id": row.get("vendor_id"),
        "item_id": row.get("item_id"),
    }


def build_observations(orders: List[Dict[str, Any]], observed_at: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in orders:
        entry = derive_stage_entry(row, observed_at)
        if entry:
            out.append(entry)
    return out


def collect_promises(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every currently-quoted promise date, tagged with where it came from.

    Priority mirrors the SLA: the Shopify metafield is the real customer quote; the workorder's
    eta-out is the service-flow equivalent. Implied dates are computed for prioritisation but
    deliberately never enter the ledger -- only a human-recorded promise counts as a promise.
    """
    out: List[Dict[str, Any]] = []
    for row in orders:
        so_id = row.get("special_order_id")
        if row.get("shopify_expected_date"):
            out.append({
                "special_order_id": str(so_id) if so_id else None,
                "shopify_order_id": row.get("shopify_order_id"),
                "promise_date": str(row["shopify_expected_date"])[:10],
                "promise_source": "shopify_metafield",
            })
        elif row.get("workorder_eta_out"):
            out.append({
                "special_order_id": str(so_id) if so_id else None,
                "shopify_order_id": None,
                "promise_date": str(row["workorder_eta_out"])[:10],
                "promise_source": "workorder_eta_out",
            })
    return out


def persist_observations(orders: List[Dict[str, Any]], store, observed_at: str) -> Dict[str, Any]:
    """Write one sweep's stage observations and promises. Never raises.

    Called from the dashboard rebuild, so a database hiccup must degrade to "no metrics this
    sweep" rather than taking the dashboard down with it.
    """
    result: Dict[str, Any] = {"skipped": None, "stages": None, "promises_new": 0}
    try:
        population = len(orders)
        if population == 0:
            result["skipped"] = "empty_population"
            return result

        previous = store.get_po_watch_meta(_META_LAST_POPULATION)
        if previous:
            try:
                if population < int(previous) * POPULATION_DROP_GUARD:
                    # Treat a collapse as a degraded read, not a real change.
                    result["skipped"] = f"population_drop {previous}->{population}"
                    return result
            except (TypeError, ValueError):
                pass

        result["stages"] = store.record_so_stage_observations(
            build_observations(orders, observed_at)
        )
        new_promises = 0
        for promise in collect_promises(orders):
            try:
                if store.record_so_promise(**promise):
                    new_promises += 1
            except Exception as exc:  # one bad row must not lose the rest of the sweep
                print(f"[so_sla] promise write failed for {promise.get('special_order_id')}: {exc}")
        result["promises_new"] = new_promises
        store.set_po_watch_meta(_META_LAST_POPULATION, str(population))
    except Exception as exc:
        print(f"[so_sla] stage persistence failed: {exc}")
        result["skipped"] = f"error: {exc}"
    return result
