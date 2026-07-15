"""PO tracker: surfaces placed-but-unreceived purchase orders and triages them
against their expected arrival date and the vendor's median lead time.

Data flow:
  * Lightspeed legacy /Order.json (complete=false, archived=false, orderedDate
    within the caller's window) with Vendor/Shop/OrderLines loaded.
  * Median vendor lead times per shop come from the existing BigQuery po_report
    aggregation (fetch_lead_times) — best-effort, rows simply lack lead-time
    context when BigQuery is unavailable.
  * Alert acknowledgements live in the planning store (po_watch_acks). An ack
    is pinned to the PO's expected date at ack time, so editing the expected
    date in Lightspeed re-arms the alert automatically.

Triage tiers (expected date relative to today):
  on_track > 7 days out · due_soon within 7 days · late 1-7 days past ·
  very_late 8-14 · critical 15+ · no_eta when no expected date exists and no
  median lead time can imply one.
"""

import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.services.lightspeed_client import LightspeedClient, _to_number
from app.services.notifications import slack
from app.services.planning_store import get_planning_store

LIGHTSPEED_PO_URL = "https://us.merchantos.com/?name=purchase.views.order&form_name=view&id={order_id}"

TRIAGE_ORDER = ["critical", "very_late", "late", "due_soon", "no_eta", "on_track"]
DUE_SOON_DAYS = 7
VERY_LATE_AFTER_DAYS = 7   # late -> very_late past this many days late
CRITICAL_AFTER_DAYS = 14   # very_late -> critical past this many days late

_CACHE_TTL_SECONDS = 300
_watch_cache: Dict[int, Dict[str, Any]] = {}   # ordered_within_days -> {"data", "fetched_at"}
_watch_lock = threading.Lock()

_employee_cache: Dict[str, Any] = {"data": None, "fetched_at": 0.0}
_EMPLOYEE_TTL_SECONDS = 24 * 3600


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def alert_days_late_threshold() -> int:
    """POs this many days past their expected date become Slack-alertable."""
    return max(1, _env_int("PO_WATCH_ALERT_DAYS_LATE", 3))


def _iso_date(value: Optional[str]) -> Optional[str]:
    """Lightspeed date fields arrive as '2026-07-24T00:00:00+00:00'; keep the date."""
    if not value:
        return None
    return str(value)[:10]


def _employee_names(client: LightspeedClient) -> Dict[str, str]:
    now = time.time()
    if _employee_cache["data"] is not None and now - _employee_cache["fetched_at"] < _EMPLOYEE_TTL_SECONDS:
        return _employee_cache["data"]
    try:
        names = client.get_employee_names()
    except Exception as e:
        print(f"po-watch: employee lookup failed: {e}")
        names = _employee_cache["data"] or {}
    _employee_cache["data"] = names
    _employee_cache["fetched_at"] = now
    return names


def _median_lead_times() -> Dict[tuple, Dict[str, Any]]:
    """{(vendor_id, shop_id): {"days": int, "po_count": int}} from BigQuery; {} on failure."""
    try:
        from app.services.bigquery_sync import fetch_lead_times
        frame = fetch_lead_times()
        out = {}
        for row in frame.to_dict(orient="records"):
            key = (str(row.get("vendor_id")), str(row.get("location_id")))
            out[key] = {
                "days": int(row.get("lead_time_days") or 0),
                "po_count": int(row.get("po_count") or 0),
            }
        return out
    except Exception as e:
        print(f"po-watch: lead-time lookup failed: {e}")
        return {}


def _line_progress(lines: List[Dict[str, Any]]) -> Dict[str, float]:
    """Units/cost ordered vs received across a PO's lines. A line's received
    count is max(numReceived, checkedIn) capped at its quantity — Lightspeed's
    check-in flow populates checkedIn before numReceived lands."""
    units = received_units = cost = received_cost = 0.0
    for line in lines:
        qty = _to_number(line.get("quantity"))
        price = _to_number(line.get("price"))
        received = min(max(_to_number(line.get("numReceived")), _to_number(line.get("checkedIn"))), qty)
        units += qty
        received_units += received
        cost += qty * price
        received_cost += received * price
    return {
        "units": units,
        "received_units": received_units,
        "cost": cost,
        "received_cost": received_cost,
    }


def _triage(days_late: Optional[int], days_until: Optional[int]) -> str:
    if days_late is None and days_until is None:
        return "no_eta"
    if days_late and days_late > CRITICAL_AFTER_DAYS:
        return "critical"
    if days_late and days_late > VERY_LATE_AFTER_DAYS:
        return "very_late"
    if days_late and days_late >= 1:
        return "late"
    if days_until is not None and days_until <= DUE_SOON_DAYS:
        return "due_soon"
    return "on_track"


def ack_is_active(ack: Optional[Dict[str, Any]], expected_date: Optional[str], today: date) -> bool:
    """An ack silences alerts until its snooze passes or the PO's expected date
    changes from what it was when acknowledged."""
    if not ack:
        return False
    if (ack.get("expected_date") or None) != (expected_date or None):
        return False
    snooze_until = ack.get("snooze_until")
    if snooze_until and str(snooze_until)[:10] < today.isoformat():
        return False
    return True


def _normalize_order(order: Dict[str, Any], employee_names: Dict[str, str],
                     lead_times: Dict[tuple, Dict[str, Any]], today: date) -> Optional[Dict[str, Any]]:
    order_id = str(order.get("orderID") or "")
    if not order_id:
        return None

    lines_wrap = order.get("OrderLines")
    lines = LightspeedClient._as_list(lines_wrap.get("OrderLine")) if isinstance(lines_wrap, dict) else []
    progress = _line_progress(lines)

    vendor = order.get("Vendor") or {}
    shop = order.get("Shop") or {}
    vendor_id = str(order.get("vendorID") or "")
    shop_id = str(order.get("shopID") or "")

    ordered_date = _iso_date(order.get("orderedDate"))
    expected_date = _iso_date(order.get("arrivalDate"))
    receiving = bool(order.get("receivedDate")) or progress["received_units"] > 0
    status = "receiving" if receiving else "ordered"

    lead = lead_times.get((vendor_id, shop_id))
    median_days = lead["days"] if lead else None

    days_since_ordered = None
    if ordered_date:
        days_since_ordered = (today - date.fromisoformat(ordered_date)).days

    # Triage against the vendor-entered expected date; when none exists, fall
    # back to an implied one (ordered date + median lead time) so the PO still
    # escalates instead of hiding in a "no ETA" bucket forever.
    flags: List[str] = []
    effective_expected = expected_date
    expected_source = "vendor" if expected_date else None
    if not effective_expected and ordered_date and median_days:
        effective_expected = (date.fromisoformat(ordered_date) + timedelta(days=median_days)).isoformat()
        expected_source = "implied"
        flags.append("implied_expected")
    if not expected_date:
        flags.append("no_expected_date")

    days_late = days_until_expected = None
    if effective_expected:
        delta = (today - date.fromisoformat(effective_expected)).days
        if delta > 0:
            days_late = delta
        else:
            days_until_expected = -delta

    # Optimism flag: the buyer promised an arrival faster than this vendor's
    # observed median lead time for this shop.
    promised_days = None
    if expected_date and ordered_date:
        promised_days = (date.fromisoformat(expected_date) - date.fromisoformat(ordered_date)).days
        if promised_days < 0:
            # ETA predates the ordered date — almost certainly stale data entry,
            # so the huge days_late it produces should be read as "fix the ETA".
            flags.append("expected_before_ordered")
        elif median_days and promised_days < median_days:
            flags.append("expected_faster_than_median")
    if median_days and days_since_ordered is not None and days_since_ordered > median_days and not receiving:
        flags.append("past_median_lead_time")

    cost = progress["cost"] or _to_number(order.get("totalCost"))
    if cost > 0 and progress["received_cost"] >= cost:
        # Everything has arrived; the PO just needs to be closed out in Lightspeed.
        flags.append("fully_received_not_closed")
    return {
        "order_id": order_id,
        "ref_num": order.get("refNum") or None,
        "vendor_id": vendor_id,
        "vendor_name": vendor.get("name") or f"Vendor {vendor_id}",
        "shop_id": shop_id,
        "shop_name": shop.get("name") or f"Shop {shop_id}",
        "created_by": employee_names.get(str(order.get("createdByEmployeeID") or "")) or None,
        "created_date": _iso_date(order.get("createTime")),
        "ordered_date": ordered_date,
        "expected_date": expected_date,
        "effective_expected_date": effective_expected,
        "expected_source": expected_source,
        "status": status,
        "line_count": len(lines),
        "units_ordered": int(progress["units"] or _to_number(order.get("totalQuantity"))),
        "units_received": int(progress["received_units"]),
        "cost_ordered": round(cost, 2),
        "cost_received": round(progress["received_cost"], 2),
        "received_pct": round(100.0 * progress["received_cost"] / cost, 1) if cost > 0 else 0.0,
        "median_lead_time_days": median_days,
        "lead_time_po_count": lead["po_count"] if lead else None,
        "promised_lead_time_days": promised_days,
        "days_since_ordered": days_since_ordered,
        "days_late": days_late,
        "days_until_expected": days_until_expected,
        "triage": _triage(days_late, days_until_expected),
        "flags": flags,
        "lightspeed_url": LIGHTSPEED_PO_URL.format(order_id=order_id),
    }


def _build_watchlist(ordered_within_days: int) -> Dict[str, Any]:
    client = LightspeedClient()
    since = (date.today() - timedelta(days=ordered_within_days)).isoformat()
    raw_orders = client.get_ordered_purchase_orders(ordered_since=since)
    employee_names = _employee_names(client)
    lead_times = _median_lead_times()
    today = date.today()

    orders = []
    for raw in raw_orders:
        normalized = _normalize_order(raw, employee_names, lead_times, today)
        if normalized:
            orders.append(normalized)

    orders.sort(key=lambda o: (
        TRIAGE_ORDER.index(o["triage"]),
        -(o["days_late"] or 0),
        o["days_until_expected"] if o["days_until_expected"] is not None else 10**6,
    ))
    return {
        "orders": orders,
        "meta": {
            "ordered_within_days": ordered_within_days,
            "ordered_since": since,
            "order_count": len(orders),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def get_po_watchlist(ordered_within_days: int = 300, force_refresh: bool = False) -> Dict[str, Any]:
    """Cached watchlist + per-request ack/alert overlay and triage summary.

    The Lightspeed walk is cached per window for a few minutes; acks are merged
    on every call so an acknowledgement is visible immediately.
    """
    ordered_within_days = max(1, min(int(ordered_within_days), 730))
    now = time.time()
    cached = _watch_cache.get(ordered_within_days)
    if force_refresh or cached is None or now - cached["fetched_at"] > _CACHE_TTL_SECONDS:
        with _watch_lock:
            cached = _watch_cache.get(ordered_within_days)
            if force_refresh or cached is None or now - cached["fetched_at"] > _CACHE_TTL_SECONDS:
                cached = {"data": _build_watchlist(ordered_within_days), "fetched_at": time.time()}
                _watch_cache[ordered_within_days] = cached

    payload = {"orders": [dict(o) for o in cached["data"]["orders"]], "meta": dict(cached["data"]["meta"])}

    try:
        acks = get_planning_store().list_po_acks()
    except Exception as e:
        print(f"po-watch: ack lookup failed: {e}")
        acks = {}
    today = date.today()
    threshold = alert_days_late_threshold()
    summary: Dict[str, Any] = {tier: 0 for tier in TRIAGE_ORDER}
    summary.update({"alertable": 0, "acknowledged": 0, "expected_faster_than_median": 0})
    for order in payload["orders"]:
        ack = acks.get(order["order_id"])
        active = ack_is_active(ack, order.get("expected_date"), today)
        order["ack"] = {
            "acked_by": ack.get("acked_by"),
            "acked_at": ack.get("acked_at"),
            "note": ack.get("note"),
            "snooze_until": ack.get("snooze_until"),
            "active": active,
        } if ack else None
        # Slack-alertable = late AND nothing received yet AND not snoozed. Once a
        # first receipt lands the vendor has delivered (that moment IS the lead
        # time), so partially/fully received POs never page the channel — they
        # remain visible in the tracker for close-out instead.
        order["alertable"] = bool(
            (order.get("days_late") or 0) >= threshold
            and order.get("status") == "ordered"
            and not active
        )
        summary[order["triage"]] += 1
        summary["alertable"] += 1 if order["alertable"] else 0
        summary["acknowledged"] += 1 if active else 0
        summary["expected_faster_than_median"] += 1 if "expected_faster_than_median" in order["flags"] else 0

    payload["summary"] = summary
    payload["meta"]["alert_days_late_threshold"] = threshold
    return payload


def get_po_lines(order_id: str) -> Optional[Dict[str, Any]]:
    """Line-level detail for one PO (used when a row is expanded in the UI)."""
    client = LightspeedClient()
    order = client.get_order_with_lines(order_id)
    if order is None:
        return None
    lines_wrap = order.get("OrderLines")
    raw_lines = LightspeedClient._as_list(lines_wrap.get("OrderLine")) if isinstance(lines_wrap, dict) else []
    items = client.get_items_basic_by_ids([line.get("itemID") for line in raw_lines])
    lines = []
    for line in raw_lines:
        item_id = str(line.get("itemID") or "")
        item = items.get(item_id, {})
        qty = _to_number(line.get("quantity"))
        price = _to_number(line.get("price"))
        received = min(max(_to_number(line.get("numReceived")), _to_number(line.get("checkedIn"))), qty)
        lines.append({
            "order_line_id": str(line.get("orderLineID") or ""),
            "item_id": item_id,
            "sku": item.get("sku"),
            "description": item.get("description"),
            "quantity": int(qty),
            "received": int(received),
            "unit_cost": round(price, 2),
            "total": round(qty * price, 2),
        })
    lines.sort(key=lambda l: (l["received"] >= l["quantity"], -(l["total"] or 0)))
    return {"order_id": str(order_id), "lines": lines}


# ---------------------------------------------------------------------------
# Slack digest: daily list of late, unacknowledged POs
# ---------------------------------------------------------------------------

def slack_config() -> Dict[str, Any]:
    return {
        "enabled": _env_flag("PO_WATCH_SLACK_ENABLED"),
        "webhook_url": os.getenv("PO_WATCH_SLACK_WEBHOOK_URL", "").strip(),
        "hour": _env_int("PO_WATCH_SLACK_HOUR", 8),
        "minute": _env_int("PO_WATCH_SLACK_MINUTE", 0),
        "timezone": os.getenv("PO_WATCH_SLACK_TIMEZONE", "America/Vancouver").strip(),
        "ordered_within_days": _env_int("PO_WATCH_ORDERED_WITHIN_DAYS", 300),
        "max_lines": _env_int("PO_WATCH_SLACK_MAX_LINES", 15),
    }


# One color-barred attachment per PO — Slack's closest equivalent to a bordered
# card. Bar color tracks the triage tier.
_TIER_COLORS = {"critical": "#d21f3c", "very_late": "#e8912d", "late": "#f2c744"}


def _fmt_slack_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        parsed = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return iso
    day = parsed.strftime("%d").lstrip("0")
    return f"{parsed.strftime('%b')} {day}, {parsed.strftime('%Y')}"


def _po_attachment(order: Dict[str, Any]) -> Dict[str, Any]:
    implied = " _(implied from median lead time)_" if order.get("expected_source") == "implied" else ""
    title = (
        f"*<{order['lightspeed_url']}|PO #{order['order_id']}>*  ·  "
        f"{order['vendor_name']}  →  {order['shop_name']}"
    )
    detail = (
        f"*{order['days_late']:,} days late* — expected {_fmt_slack_date(order['effective_expected_date'])}{implied}\n"
        f"${order['cost_ordered']:,.0f} on order ({order['units_ordered']} units) · "
        f"placed {_fmt_slack_date(order['ordered_date'])}"
        + (f" · by {order['created_by']}" if order.get("created_by") else "")
    )
    if "expected_before_ordered" in order["flags"]:
        detail += "\n⚠️ _The expected date predates the ordered date — likely stale; fix the ETA in Lightspeed._"
    return {
        "color": _TIER_COLORS.get(order["triage"], "#f2c744"),
        "fallback": f"PO #{order['order_id']} — {order['vendor_name']}: {order['days_late']}d late",
        "blocks": [slack.section(f"{title}\n{detail}")],
    }


def build_slack_digest(ordered_within_days: int) -> Dict[str, Any]:
    """Returns {text, blocks, attachments, alertable_count}; empty when nothing to send."""
    watchlist = get_po_watchlist(ordered_within_days=ordered_within_days)
    late = [o for o in watchlist["orders"] if o["alertable"]]
    threshold = watchlist["meta"]["alert_days_late_threshold"]
    if not late:
        return {"text": None, "blocks": None, "attachments": None, "alertable_count": 0}

    config = slack_config()
    late.sort(key=lambda o: -(o["days_late"] or 0))
    shown = late[: config["max_lines"]]
    total_value = sum(o["cost_ordered"] or 0 for o in late)
    acked = watchlist["summary"]["acknowledged"]

    blocks = [
        slack.header(f"🚚 {len(late)} overdue purchase order{'s' if len(late) != 1 else ''}"),
        slack.context(
            f"Unreceived POs ≥{threshold} day(s) past their expected date · "
            f"${total_value:,.0f} outstanding"
            + (f" · {acked} snoozed" if acked else "")
        ),
    ]
    attachments = [_po_attachment(order) for order in shown]

    footer_lines = []
    if len(late) > len(shown):
        footer_lines.append(f"…plus *{len(late) - len(shown)} more* overdue PO(s) not shown here.")
    footer_lines.append("_Review and acknowledge in the PO Tracker tab to stop these pings; "
                        "an acknowledged PO stays silent until its expected date changes or its snooze expires._")
    attachments.append({
        "color": "#a3a3a3",
        "fallback": "Review in the PO Tracker tab.",
        "blocks": [slack.context("\n".join(footer_lines))],
    })

    return {
        "text": f"{len(late)} unreceived purchase order(s) are {threshold}+ days past their expected date",
        "blocks": blocks,
        "attachments": attachments,
        "alertable_count": len(late),
    }


def send_slack_digest(force: bool = False) -> Dict[str, Any]:
    """Build + post the late-PO digest. Best-effort; returns a status dict."""
    config = slack_config()
    if not force and not config["enabled"]:
        return {"sent": False, "reason": "PO_WATCH_SLACK_ENABLED is off"}
    if not config["webhook_url"]:
        return {"sent": False, "reason": "PO_WATCH_SLACK_WEBHOOK_URL is not set"}
    digest = build_slack_digest(config["ordered_within_days"])
    if not digest["alertable_count"]:
        return {"sent": False, "reason": "no alertable POs", "alertable_count": 0}
    ok = slack.post(
        config["webhook_url"],
        text=digest["text"],
        blocks=digest["blocks"],
        attachments=digest["attachments"],
    )
    return {"sent": ok, "alertable_count": digest["alertable_count"]}


_scheduler_started = False


def start_scheduler() -> None:
    """Daily-digest daemon thread (mirrors the price-intel scheduler). Started
    once per process; the planning-store meta row guards double-sends across
    restarts/redeploys."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def _scheduler_loop() -> None:
    while True:
        try:
            config = slack_config()
            if config["enabled"] and config["webhook_url"]:
                now = datetime.now(ZoneInfo(config["timezone"]))
                today = now.strftime("%Y-%m-%d")
                if (now.hour, now.minute) >= (config["hour"], config["minute"]):
                    store = get_planning_store()
                    if store.get_po_watch_meta("last_digest_date") != today:
                        store.set_po_watch_meta("last_digest_date", today)
                        result = send_slack_digest()
                        print(f"po-watch: scheduled digest for {today}: {result}")
        except Exception as e:
            print(f"po-watch: scheduler tick failed: {e}")
        time.sleep(60)
