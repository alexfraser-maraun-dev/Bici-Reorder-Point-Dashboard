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

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

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
    # SpecialOrder.timeStamp while its own status is received. Order.receivedDate is PO-wide
    # and can belong to another line in a backorder/split shipment.
    "received": "so_received_date",
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
        # Fall back down the chain when Lightspeed has no individual status timestamp. These
        # are measurement floors only; a PO header receipt date is intentionally excluded
        # because it may belong to another line in a split shipment.
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


# Shopify-only orders share the table with Lightspeed special orders. The prefix keeps a Shopify
# order id from ever colliding with an SO id on the natural key.
SHOPIFY_EVENT_PREFIX = "shopify:"


def build_shopify_observations(shopify_only: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Shopify orders with no Lightspeed special order yet: the pipeline's "Shopify intake" stage.

    The entry date is the Shopify order's own creation date, so it is derived like the LS stages.
    The order leaves the stage when it stops appearing (it was linked to an SO, or closed).
    """
    out: List[Dict[str, Any]] = []
    for order in shopify_only or []:
        order_id = order.get("order_id")
        created = str(order.get("created_at") or "")[:10]
        if not order_id or not created:
            continue
        out.append({
            "special_order_id": f"{SHOPIFY_EVENT_PREFIX}{order_id}",
            "stage": "shopify",
            "entered_at": created,
            "entered_source": "derived",
            "shop_id": None,
            "source": "shopify",
            "order_id": None,
            "vendor_id": None,
            "item_id": None,
        })
    return out


def collect_promises(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every currently-quoted promise date, tagged with where it came from.

    Priority mirrors the SLA: the Shopify metafield is the real customer quote. A workorder's
    eta-out is the bike's booking/service date and is deliberately excluded; service parts
    promises enter this same ledger through the app-owned service-promise endpoint. Implied
    dates are for prioritisation only and never enter the ledger.
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
    return out


def persist_observations(orders: List[Dict[str, Any]], store, observed_at: str,
                         shopify_only: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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

        # The population guard above is about Lightspeed reads only. Shopify intake rides in the
        # same transaction; an empty Shopify list just means no Shopify rows are touched, and
        # build_stage_history judges Shopify openness against Shopify's own latest sweep.
        result["stages"] = store.record_so_stage_observations(
            build_observations(orders, observed_at) + build_shopify_observations(shopify_only)
        )
        # One transaction for the whole sweep. Per-row writes meant a connection
        # round-trip per open special order, every five minutes. The batch writer keeps
        # the same per-row fail-soft behaviour: a bad row is skipped, not fatal.
        result["promises_new"] = store.record_so_promises(collect_promises(orders))
        store.set_po_watch_meta(_META_LAST_POPULATION, str(population))
    except Exception as exc:
        print(f"[so_sla] stage persistence failed: {exc}")
        result["skipped"] = f"error: {exc}"
    return result


# ---------------------------------------------------------------------------
# Stage history: daily dwell trend for the pipeline sparklines
# ---------------------------------------------------------------------------

STAGE_HISTORY_STAGES = ("shopify", "open_pool", "unordered_po", "ordered", "received")
STAGE_HISTORY_COLUMNS = ("stage", "entered", "left", "shop_id", "source", "created")


def _local_date(timestamp: Optional[str], tz) -> Optional[date]:
    """A stored UTC ISO timestamp as a calendar date in the store's timezone."""
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp))
    except ValueError:
        try:
            return date.fromisoformat(str(timestamp)[:10])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz).date() if tz else parsed.date()


def _iso_date(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def build_stage_history(events: Iterable[Dict[str, Any]], today: date, days: int = 60,
                        tz=None) -> Dict[str, Any]:
    """Every stage interval overlapping the last ``days`` days, as compact day offsets.

    ``so_stage_events`` never writes ``left_at``, so the exit is reconstructed here:

    * **open** -- touched by the latest sweep of its group (Shopify and Lightspeed are judged
      separately, so a Shopify outage freezes Shopify rows rather than closing them all);
    * otherwise the SO's next stage entry (an authoritative Lightspeed date) when it has one;
    * otherwise the day it was last seen -- it completed, was linked, or was cancelled.

    ``tracking_since`` is the first day each stage was observed at all. Before it, orders that
    had already left are missing from the table and a median would be biased, so the client
    must not draw earlier points. ``created`` is the earliest entry seen for the SO -- a proxy for
    its creation date, used to mirror the 365-day live window.

    Offsets are whole days relative to ``end`` (today = 0, yesterday = -1); ``left`` is None
    while open. The interval covers days ``entered <= d < left``.
    """
    events = [e for e in events if e.get("stage") in STAGE_HISTORY_STAGES]
    end = today
    start = end - timedelta(days=max(1, int(days)))

    def group(stage: str) -> str:
        return "shopify" if stage == "shopify" else "lightspeed"

    latest_sweep: Dict[str, str] = {}
    tracking_since: Dict[str, date] = {}
    created: Dict[str, date] = {}
    by_so: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        g = group(e["stage"])
        seen = str(e.get("last_seen_at") or "")
        if seen > latest_sweep.get(g, ""):
            latest_sweep[g] = seen
        first = _local_date(e.get("first_seen_at"), tz)
        if first and (e["stage"] not in tracking_since or first < tracking_since[e["stage"]]):
            tracking_since[e["stage"]] = first
        entered = _iso_date(e.get("entered_at"))
        if entered is None:
            continue
        so_id = str(e.get("special_order_id"))
        if so_id not in created or entered < created[so_id]:
            created[so_id] = entered
        by_so.setdefault(so_id, []).append(e)

    rows: List[List[Any]] = []
    for so_id, so_events in by_so.items():
        so_events.sort(key=lambda e: (str(e.get("entered_at")), str(e.get("first_seen_at"))))
        for i, e in enumerate(so_events):
            entered = _iso_date(e.get("entered_at"))
            left: Optional[date] = None
            if str(e.get("last_seen_at") or "") != latest_sweep.get(group(e["stage"])):
                nxt = next((_iso_date(n.get("entered_at")) for n in so_events[i + 1:]
                            if _iso_date(n.get("entered_at")) and _iso_date(n.get("entered_at")) > entered),
                           None)
                left = nxt or _local_date(e.get("last_seen_at"), tz) or entered
                # An SO seen only on the day it entered still spent that day in the stage.
                if left <= entered:
                    left = entered + timedelta(days=1)
            if left is not None and left <= start:
                continue
            if entered > end:
                continue
            rows.append([
                STAGE_HISTORY_STAGES.index(e["stage"]),
                (entered - end).days,
                (left - end).days if left is not None else None,
                e.get("shop_id"),
                e.get("source"),
                (created[so_id] - end).days,
            ])

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "stages": list(STAGE_HISTORY_STAGES),
        "tracking_since": {stage: d.isoformat() for stage, d in tracking_since.items()},
        "columns": list(STAGE_HISTORY_COLUMNS),
        "rows": rows,
    }
