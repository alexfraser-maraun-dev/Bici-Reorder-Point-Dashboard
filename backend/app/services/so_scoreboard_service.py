"""Scoreboard for special orders: where the time actually goes, and who owns it.

Answers the question the whole feature exists for — *"how long is it taking us to get the
customer their special order, and are we hitting the quoted date?"* — from three sources, each
chosen because it is trustworthy for the specific thing it is asked:

* **Live Lightspeed rows** for current queue health. Stage entry timestamps are DERIVED from
  Lightspeed's own dates, so dwell is correct from the first sweep rather than needing weeks of
  history to accumulate.
* **`so_promises`** for promise integrity. The ledger keeps every quoted date, so on-time can be
  scored against the ORIGINAL promise. Scoring against the current one is trivially gamed by
  sliding the date, which is exactly the behaviour a scoreboard should expose rather than hide.
* **BigQuery PO timestamps** for historical cycle time. `open_special_orders_view` must never be
  used to count *open* special orders (it disagrees with the API by ~5x), but its
  `po_ordered_at` / `po_received_at` columns are real Lightspeed timestamps and are fine for
  measuring completed work.

Every metric is split by owner, because the failures have different homes: procurement sits on
un-allocated orders, the service bench and CS fail to record a promise, receiving fails to close
out. A single blended number would hide all three.
"""

from datetime import date
from typing import Any, Dict, List, Optional

from app.services import so_sla_service

# Anything older than this is treated as abandoned rather than late. Without it the 1,100+ stale
# "Ready For Pickup" rows (median 68 days, max 6+ years) dominate every average. Mirrors
# LIVE_SO_MAX_DAYS in the frontend.
LIVE_MAX_DAYS = 365


def _percentiles(values: List[int]) -> Optional[Dict[str, int]]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return {
        "n": n,
        "median": ordered[n // 2],
        "p75": ordered[min(int(n * 0.75), n - 1)],
        "max": ordered[-1],
    }


def _bucket(rows, key_fn, value_fn):
    out: Dict[str, List[int]] = {}
    for row in rows:
        value = value_fn(row)
        if value is None:
            continue
        out.setdefault(str(key_fn(row)), []).append(int(value))
    return {k: _percentiles(v) for k, v in out.items()}


def build_scoreboard(orders: List[Dict[str, Any]], acks: Dict[str, Dict[str, Any]],
                     promises: List[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    live = [o for o in orders if (o.get("days_since_creation") or 0) <= LIVE_MAX_DAYS]
    sla = so_sla_service.build_escalations(live, acks, today)
    rows = sla["orders"]

    # --- where time is being spent right now -------------------------------------------
    open_rows = [r for r in rows if r["procurement_stage"] != "received"]
    dwell = {
        "by_stage": _bucket(open_rows, lambda r: r["procurement_stage"], lambda r: r.get("days_in_stage")),
        "by_store": _bucket(open_rows, lambda r: r.get("store") or "unknown", lambda r: r.get("days_since_creation")),
        "by_source": _bucket(open_rows, lambda r: r.get("source") or "neither", lambda r: r.get("days_since_creation")),
    }

    # --- promise integrity --------------------------------------------------------------
    # Scored against the ORIGINAL quote (revision_index 0). A revision is recorded, not forgiven.
    original_by_so: Dict[str, str] = {}
    revisions: Dict[str, int] = {}
    for p in promises:
        so_id = str(p.get("special_order_id") or "")
        if not so_id:
            continue
        revisions[so_id] = max(revisions.get(so_id, 0), int(p.get("revision_index") or 0))
        if int(p.get("revision_index") or 0) == 0:
            original_by_so[so_id] = str(p.get("promise_date"))[:10]

    promised = [r for r in rows if r.get("promise_date")]
    met = missed = breached_outstanding = undetermined = 0
    revised = 0
    for r in promised:
        so_id = str(r.get("special_order_id"))
        original = original_by_so.get(so_id) or r.get("promise_date")
        received = r.get("po_received_date")
        if revisions.get(so_id, 0) > 0:
            revised += 1
        if received:
            # Only a delivered order can be scored. This is the on-time rate.
            if str(received)[:10] <= original:
                met += 1
            else:
                missed += 1
        elif today.isoformat() > original:
            # Not here and the date has gone: already a failure, counted separately because it
            # is not yet a *completed* outcome.
            breached_outstanding += 1
        else:
            # Still inside its window. NOT "on time" -- undetermined. Counting these as met is
            # how an on-time metric flatters itself: most of the population has simply not had
            # the chance to fail yet.
            undetermined += 1

    settled = met + missed
    promise = {
        "with_promise": len(promised),
        "settled": settled,
        "met": met,
        "missed": missed,
        # Denominator is settled orders only -- the honest on-time rate.
        "on_time_pct_vs_original": round(100 * met / settled, 1) if settled else None,
        "breached_outstanding": breached_outstanding,
        "undetermined": undetermined,
        "revised_at_least_once": revised,
        # Split out so a missing promise is never mistaken for a met one.
        "missing_promise": sla["summary"]["missing_promise"],
        "missing_promise_by_owner": sla["summary"]["missing_promise_by_owner"],
    }

    # --- accountability ------------------------------------------------------------------
    reason_counts: Dict[str, int] = {}
    for r in rows:
        ack = r.get("ack")
        if ack and r.get("ack_active"):
            reason_counts[str(ack.get("reason_code"))] = reason_counts.get(str(ack.get("reason_code")), 0) + 1

    # --- close-out hygiene (deliberately OUTSIDE the SLA) --------------------------------
    # The clock stops at receipt, so this is tracked but never folded into on-time.
    received_rows = [r for r in rows if r["procurement_stage"] == "received"]
    stale_all = [o for o in orders
                 if o.get("procurement_stage") == "received"
                 and (o.get("days_since_creation") or 0) > LIVE_MAX_DAYS]

    return {
        "as_of": today.isoformat(),
        "population": {
            "live": len(live),
            "open": len(open_rows),
            "received_awaiting_closeout": len(received_rows),
            "stale_beyond_live_window": len(stale_all),
        },
        "dwell_days": dwell,
        "promise": promise,
        "queue": {
            "actionable": sla["summary"]["actionable"],
            "by_severity": sla["summary"]["by_severity"],
            "by_owner": sla["summary"]["by_owner"],
            "acked": sla["summary"]["acked"],
            "escalated": sla["summary"]["escalated"],
            "checkback_due": sla["summary"]["checkback_due"],
            "top_blocking_reasons": sorted(reason_counts.items(), key=lambda kv: -kv[1])[:5],
        },
    }


def fetch_historical_cycle_times(lookback_months: int = 12) -> Dict[str, Any]:
    """Completed special-order cycle times, per store, from real Lightspeed PO timestamps.

    Uses `open_special_orders_view` for its `po_ordered_at` / `po_received_at` columns ONLY.
    That view must never be used to count open special orders — it reports ~5x the API's figure —
    but the PO dates on completed rows are genuine and are the only record of historical
    throughput available without waiting months for `so_stage_events` to accumulate.
    """
    from app.services.bigquery_sync import get_bq_client, LS_DATASET

    query = f"""
        SELECT shop_name,
               COUNT(*) AS n,
               APPROX_QUANTILES(DATE_DIFF(DATE(po_ordered_at), special_order_created_date, DAY), 4) AS create_to_place,
               APPROX_QUANTILES(DATE_DIFF(DATE(po_received_at), DATE(po_ordered_at), DAY), 4) AS place_to_receive,
               APPROX_QUANTILES(DATE_DIFF(DATE(po_received_at), special_order_created_date, DAY), 4) AS end_to_end
        FROM `{LS_DATASET}.open_special_orders_view`
        WHERE po_ordered_at IS NOT NULL AND po_received_at IS NOT NULL
          AND special_order_created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(lookback_months)} MONTH)
          -- Clamp obvious data-entry noise; a "cycle" of a year is a stale record, not a lead time.
          AND DATE_DIFF(DATE(po_received_at), special_order_created_date, DAY) BETWEEN 0 AND 365
        GROUP BY shop_name
        ORDER BY n DESC
    """
    out = []
    for r in get_bq_client().query(query).result():
        def _q(values):
            if not values or len(values) < 5:
                return None
            return {"p25": int(values[1]), "median": int(values[2]), "p75": int(values[3])}
        out.append({
            "store": r.shop_name,
            "n": int(r.n),
            "create_to_place": _q(r.create_to_place),
            "place_to_receive": _q(r.place_to_receive),
            "end_to_end": _q(r.end_to_end),
        })
    return {"lookback_months": lookback_months, "stores": out}
