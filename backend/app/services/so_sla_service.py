"""Special-order SLA: clocks, severity, and the escalation queue.

The measurement question is "how long is it taking us to get the customer their special order,
and are we hitting the quoted date?". This module turns the dashboard payload into a worklist
answering it, and never re-walks Lightspeed -- it consumes the already-cached rows and overlays
acknowledgements per request, the same shape as `po_watch_service.get_po_watchlist`.

Three design decisions carry the behaviour, each forced by live data:

**Two clocks, flag on the worse.** `days_since_creation` answers "will we miss the promise?";
`days_in_stage` answers "is this step stalling?". Neither substitutes for the other -- an SO can
be 92 days old on a PO drafted 2 days ago (a long pre-allocation wait) or 3 days old on a
48-day-old draft (a stalled draft). Flagging on stage dwell alone reads the first as healthy.

**The clock stops at receipt.** Everything after the item lands is close-out hygiene, not
"did we hit the quoted date". Without this the 1,111 stale ready-for-pickup rows (median 68 days,
max 6+ years) would dominate every average.

**Escalation pins three values, not one.** `po_watch_acks` pins only the expected date. A special
order needs stage, promise and PO ETA pinned, or a snoozed SO that regresses to an earlier stage
stays silently hidden.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Who owns a breach at each stage, so it lands on the right desk rather than defaulting to
# "procurement's fault".
OWNER_BY_STAGE = {
    "open_pool": "procurement",     # needs allocating to a PO
    "unordered_po": "procurement",  # on a draft PO that needs placing
    "ordered": "procurement",       # placed; needs monitoring
    "received": "receiving",        # landed; needs closing out
}

# Maximum days a special order should sit in each stage. Defaults are the p75 of the observed
# healthy distribution rather than invented numbers, so they flag the tail without crying wolf
# on normal work. Per-location because the stores genuinely differ: Victoria's create->place p75
# is 8 days against Langford's 2.
DEFAULT_STAGE_SLA_DAYS = {"open_pool": 2, "unordered_po": 3, "ordered": None, "received": 7}
STAGE_SLA_BY_STORE: Dict[str, Dict[str, int]] = {
    "2": {"open_pool": 4, "unordered_po": 4},   # Victoria runs slower; p75 create->place is 8d
}

# Days between the item arriving and it being ready for the customer. Measured properly in a
# later pass; 1 day is the conservative placeholder so the backward schedule is never optimistic.
RECEIVING_BUFFER_DAYS = 1

# A promise should be captured while the request is still fresh. This is an operational due
# date for the work queue, not part of the delivery SLA calculation.
PROMISE_CAPTURE_DAYS = 1

# Used only when a special order has no vendor and no qualifying brand vendor. Mirrors the
# replenishment engine's fallback so the two do not disagree.
FALLBACK_LEAD_TIME_DAYS = 14

# Worst first. `severity_rank` is what the queue sorts on.
SEVERITY_ORDER = [
    "promise_missed",  # the quoted date has passed and the item is not here
    "impossible",      # cannot arrive by the promise even if ordered right now
    "order_today",     # the last day to order and still make the promise
    "stage_stalled",   # no promise pressure, but this step has overrun its dwell limit
    "at_risk",         # slack is inside one ordering cycle
    "no_promise",      # nothing was ever quoted -- a CS gap, not a procurement one
    "on_track",
    "closed_out",      # received; the SLA clock has stopped
]
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}

# Why a buyer parked this special order. Free-text notes are allowed alongside, but a code is
# required -- an un-categorised snooze cannot be reported on, and reporting is the point.
REASON_CODES = [
    "vendor_backorder",
    "awaiting_vendor_reply",
    "customer_contacted",
    "item_discontinued",
    "waiting_on_cs",
    "substitute_offered",
    "other",
]


def filter_live_window(orders: List[Dict[str, Any]], live_only_days: Optional[int]) -> List[Dict[str, Any]]:
    """Apply the optional live-age window.

    ``0`` and ``None`` explicitly mean the full Lightspeed population. Keeping this rule in a
    pure helper makes the archive toggle testable and prevents a falsy-value shortcut from
    accidentally rebuilding the default 365-day view.
    """
    if live_only_days is None:
        return list(orders)
    try:
        days = int(live_only_days)
    except (TypeError, ValueError):
        return list(orders)
    if days <= 0:
        return list(orders)
    return [
        row for row in orders
        if row.get("days_since_creation") is None
        or int(row.get("days_since_creation") or 0) <= days
    ]


def _parse(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


# Whether a workorder's `etaOut` counts as a customer promise for the PART.
#
# Set False on evidence (measured live 2026-08-19, 112 workorder-linked special orders):
#   * etaOut is populated on 100% of workorders -- a real promise would not be universal;
#   * it equals `timeIn`, the day the bike was booked in, for 85 of 112 (median gap 0 days);
#   * 35 of 112 have etaOut dated BEFORE the special order was even created, so the "promise"
#     predates the request for the part;
#   * 94 of 112 (84%) are already in the past, against 5 of 43 (12%) for genuine Shopify quotes.
#
# It is the workorder's own booking/service date, not a commitment about the part. Treating it
# as a promise manufactured 96 false breaches out of 229 live special orders and buried the 4
# real ones. Service special orders therefore run on stage-dwell limits until the bench starts
# recording a real parts ETA; flip this to True if that changes.
TREAT_WORKORDER_ETA_AS_PROMISE = False


def resolve_promise(row: Dict[str, Any]) -> Tuple[Optional[date], Optional[str]]:
    """The customer-facing promise date and where it came from.

    Only a human-recorded quote counts. Implied dates are computed for prioritisation elsewhere
    but never enter here -- scoring an on-time metric against a date the system invented would
    make it self-referential.
    """
    # Service promises are app-owned because Workorder.etaOut is demonstrably the bike's
    # booking/service date, not a parts commitment. It is only valid on a workorder-origin row.
    service = _parse(row.get("service_promise_date"))
    if service and row.get("source") == "workorder":
        return service, "service_manual"
    shopify = _parse(row.get("shopify_expected_date"))
    if shopify:
        return shopify, "shopify_metafield"
    if TREAT_WORKORDER_ETA_AS_PROMISE:
        wo = _parse(row.get("workorder_eta_out"))
        if wo:
            return wo, "workorder_eta_out"
    return None, None


def effective_lead_time(row: Dict[str, Any]) -> Tuple[int, str]:
    """Days from placing an order to the item arriving, and how we know.

    Once a PO exists we know the actual vendor. Before that there is no vendor, so we use the
    fastest vendor that can supply the brand -- the best case the buyer could still achieve,
    which is the right basis for "is this still possible?".
    """
    if row.get("vendor_lead_time_days") is not None:
        return int(row["vendor_lead_time_days"]), "po_vendor"
    for vendor in row.get("available_vendors") or []:
        if vendor.get("lead_time_days") is not None:
            return int(vendor["lead_time_days"]), "fastest_qualifying_vendor"
    return FALLBACK_LEAD_TIME_DAYS, "default"


def stage_sla_days(stage: str, shop_id: Optional[str]) -> Optional[int]:
    per_store = STAGE_SLA_BY_STORE.get(str(shop_id) or "", {})
    if stage in per_store:
        return per_store[stage]
    return DEFAULT_STAGE_SLA_DAYS.get(stage)


def _due_from(value: Optional[str], days: int) -> Optional[str]:
    parsed = _parse(value)
    return (parsed + timedelta(days=days)).isoformat() if parsed else None


def _workorder_is_open(row: Dict[str, Any]) -> bool:
    if not row.get("workorder_id"):
        return False
    status = str(row.get("workorder_status") or "").strip().lower()
    # An unknown status is deliberately treated as open. The linkage exists and silently
    # declaring it complete would lose the service follow-up from the close-out queue.
    if not status:
        return True
    closed_words = ("complete", "completed", "closed", "finished", "invoiced", "cancel")
    return any(word in status for word in closed_words) is False


def _closeout_action(row: Dict[str, Any]) -> Dict[str, Any]:
    """Post-receipt work, kept explicitly outside the delivery SLA."""
    due = _due_from(
        row.get("po_received_date") or row.get("ordered_date") or row.get("created_date"),
        RECEIVING_BUFFER_DAYS,
    )
    if _workorder_is_open(row):
        return {
            "closeout_state": "workorder_action_required",
            "next_action": "Part received — resume and complete the linked workorder.",
            "action_owner": "service",
            "action_due_date": due,
        }
    if not row.get("contacted"):
        contact_owner = "service" if row.get("source") == "workorder" else "retail"
        return {
            "closeout_state": "ready_not_called",
            "next_action": "Contact the customer and mark the special order as contacted.",
            "action_owner": contact_owner,
            "action_due_date": due,
        }
    fulfillment = str(row.get("shopify_fulfillment_status") or "").strip().lower()
    shopify_still_open = bool(
        row.get("shopify_order_id")
        and not row.get("matched_via_closed_order")
        and fulfillment not in ("fulfilled", "restocked")
    )
    if shopify_still_open:
        return {
            "closeout_state": "shopify_fulfillment_pending",
            "next_action": "Fulfill the received special-order line in Shopify.",
            "action_owner": "cs",
            "action_due_date": due,
        }
    if not row.get("completed"):
        return {
            "closeout_state": "lightspeed_completion_pending",
            "next_action": "Complete the received special order in Lightspeed.",
            "action_owner": "receiving",
            "action_due_date": due,
        }
    return {
        "closeout_state": "complete",
        "next_action": None,
        "action_owner": None,
        "action_due_date": None,
    }


def compute_work_state(row: Dict[str, Any], sla: Dict[str, Any], today: date) -> Dict[str, Any]:
    """Operational next work, intentionally separate from the customer delivery SLA.

    ``work_state`` is the single primary action used for sorting and ownership.
    ``queue_states`` is multi-valued because two teams can legitimately have parallel work: an
    unallocated SO can need ordering while service or CS also records the customer promise.
    """
    stage = row.get("procurement_stage") or "open_pool"
    severity = sla.get("sla_severity")
    queue_states: List[str] = []

    if stage in ("open_pool", "unordered_po"):
        queue_states.append("needs_ordering")
    elif stage == "ordered":
        queue_states.append("in_transit")
        if row.get("flag") == "no_eta" or severity in (
            "promise_missed", "stage_stalled", "impossible", "order_today", "at_risk"
        ):
            queue_states.append("vendor_followup")

    if sla.get("missing_promise"):
        queue_states.append("promise_needed")

    closeout = _closeout_action(row) if stage == "received" else {
        "closeout_state": None,
        "next_action": None,
        "action_owner": None,
        "action_due_date": None,
    }
    if closeout["closeout_state"] not in (None, "complete"):
        queue_states.append("closeout")

    # One primary next action. Lifecycle blockers come before the parallel promise capture;
    # every membership still remains filterable through queue_states.
    if "closeout" in queue_states:
        primary = "closeout"
        action = closeout
    elif "needs_ordering" in queue_states:
        primary = "needs_ordering"
        if stage == "open_pool":
            next_action = "Create/allocate the purchase order manually in Lightspeed."
        else:
            next_action = "Place the attached draft PO with the vendor."
        limit = stage_sla_days(stage, row.get("shop_id")) or 0
        basis = row.get("po_created_date") if stage == "unordered_po" else row.get("created_date")
        action = {
            "next_action": next_action,
            "action_owner": "procurement",
            "action_due_date": sla.get("order_by_date") or _due_from(basis, limit),
        }
    elif "vendor_followup" in queue_states:
        primary = "vendor_followup"
        if row.get("flag") == "no_eta":
            next_action = "Confirm an expected arrival date with the vendor."
        elif severity == "promise_missed":
            next_action = "Chase the vendor now and re-quote the customer if required."
        else:
            next_action = "Follow up with the vendor on the delayed order."
        action = {
            "next_action": next_action,
            "action_owner": "procurement",
            "action_due_date": today.isoformat(),
        }
    elif "promise_needed" in queue_states:
        primary = "promise_needed"
        owner = sla.get("promise_owner") or "cs"
        next_action = (
            "Record a parts promise for this service workorder."
            if owner == "service"
            else "Set the customer promise date in Shopify."
        )
        action = {
            "next_action": next_action,
            "action_owner": owner,
            "action_due_date": _due_from(row.get("created_date"), PROMISE_CAPTURE_DAYS),
        }
    else:
        primary = "on_track"
        action = {"next_action": None, "action_owner": None, "action_due_date": None}
        if not queue_states:
            queue_states.append("on_track")

    return {
        "work_state": primary,
        "queue_states": queue_states,
        "next_action": action.get("next_action"),
        "action_owner": action.get("action_owner"),
        "action_due_date": action.get("action_due_date"),
        "closeout_state": closeout.get("closeout_state"),
    }


def compute_sla(row: Dict[str, Any], today: date) -> Dict[str, Any]:
    """The SLA verdict for one special order: severity, dates, and a plain-English reason."""
    stage = row.get("procurement_stage") or "open_pool"
    owner = OWNER_BY_STAGE.get(stage, "procurement")
    promise, promise_source = resolve_promise(row)
    lead_days, lead_source = effective_lead_time(row)
    already_ordered = stage in ("ordered", "received")
    received = stage == "received"

    order_by: Optional[date] = None
    slack_days: Optional[int] = None
    if promise:
        order_by = promise - timedelta(days=lead_days + RECEIVING_BUFFER_DAYS)
        slack_days = (order_by - today).days

    # Stage dwell, judged against this stage's limit at this store.
    limit = stage_sla_days(stage, row.get("shop_id"))
    dwell = row.get("days_in_stage")
    dwell_over = (
        int(dwell) - int(limit) if limit is not None and dwell is not None and dwell > limit else None
    )

    # --- severity ------------------------------------------------------------------
    # The SLA clock stops at receipt: what remains is close-out hygiene, tracked separately so
    # the stale ready-for-pickup tail can never contaminate the on-time number.
    if received:
        severity = "closed_out"
    elif promise and promise < today:
        severity = "promise_missed"
    elif promise and not already_ordered and today + timedelta(days=lead_days + RECEIVING_BUFFER_DAYS) > promise:
        severity = "impossible"
    elif slack_days is not None and slack_days <= 0 and not already_ordered:
        severity = "order_today"
    elif slack_days is not None and slack_days <= 3 and not already_ordered:
        severity = "at_risk"
    elif not promise:
        severity = "no_promise"
    else:
        severity = "on_track"

    # Flag on the WORSE of the promise clock and the dwell clock. A stalled step still needs
    # chasing even when the promise is comfortable (or absent).
    if dwell_over is not None and not received:
        severity = min(severity, "stage_stalled", key=lambda s: _SEVERITY_RANK[s])

    return {
        "sla_severity": severity,
        "sla_severity_rank": _SEVERITY_RANK[severity],
        "sla_owner": owner,
        "promise_date": promise.isoformat() if promise else None,
        "promise_source": promise_source,
        "lead_time_days": lead_days,
        "lead_time_source": lead_source,
        "receiving_buffer_days": RECEIVING_BUFFER_DAYS,
        "order_by_date": order_by.isoformat() if order_by else None,
        "slack_days": slack_days,
        "stage_sla_days": limit,
        "days_over_stage_sla": dwell_over,
        # "Nobody ever quoted this customer a date" is a real gap, but it is not procurement's.
        # Who must fix it depends on where the SO came from: the service bench sets the parts
        # ETA on a workorder, CS sets the metafield on a Shopify order. Attributing all of it to
        # CS would hand the bench's backlog to the wrong team.
        "missing_promise": promise is None and not received,
        "promise_owner": (None if promise or received else
                          ("service" if row.get("source") == "workorder" else "cs")),
        "sla_reason": _reason(row, severity, promise, promise_source, lead_days, lead_source,
                              order_by, slack_days, limit, dwell_over),
    }


def _reason(row, severity, promise, promise_source, lead_days, lead_source,
            order_by, slack_days, limit, dwell_over) -> str:
    """One line a buyer can act on without opening anything else."""
    if severity == "closed_out":
        return "Item received — SLA clock stopped; close-out only."
    if severity == "no_promise":
        if row.get("source") == "workorder":
            return ("No parts promise recorded — nothing to schedule against. "
                    "Service needs to record the parts promise before this can be prioritised.")
        return ("No customer promise recorded — nothing to schedule against. "
                "CS needs to set the Shopify ETA before this can be prioritised.")

    vendor = row.get("vendor_name")
    if not vendor:
        fastest = (row.get("available_vendors") or [None])[0]
        vendor = (fastest or {}).get("vendor_name") or "unknown vendor"
    src = {"po_vendor": "", "fastest_qualifying_vendor": " (fastest option)", "default": " (assumed)"}[lead_source]
    bits = []
    if promise:
        label = {
            "shopify_metafield": "Shopify",
            "service_manual": "Service parts",
            "workorder_eta_out": "Workorder",
        }.get(promise_source, "Customer")
        bits.append(f"{label} promise {promise.isoformat()}")
        bits.append(f"{vendor} lead time {lead_days}d{src}")
        bits.append(f"buffer {RECEIVING_BUFFER_DAYS}d")
        if order_by:
            bits.append(f"order by {order_by.isoformat()}")
        if slack_days is not None:
            bits.append(f"{slack_days}d slack" if slack_days >= 0 else f"{abs(slack_days)}d past order-by")
    if dwell_over is not None:
        bits.append(f"{dwell_over}d over the {limit}d limit for this stage")
    if severity == "impossible":
        bits.append("cannot arrive in time even if ordered today — re-quote the customer")
    return " · ".join(bits) or "On track."


def ack_is_active(ack: Optional[Dict[str, Any]], row: Dict[str, Any], today: date) -> bool:
    """Whether an acknowledgement still silences this special order.

    Four independent invalidations. The three pinned values re-arm the alert the moment the
    underlying situation changes, so a snooze can only ever hide the problem it was taken for.
    """
    if not ack:
        return False
    if str(ack.get("checkback_date") or "")[:10] < today.isoformat():
        return False                                    # the check-back date arrived
    if (ack.get("pinned_stage") or None) != (row.get("procurement_stage") or None):
        return False                                    # it moved on (or regressed)
    promise, _ = resolve_promise(row)
    if (ack.get("pinned_promise") or None) != (promise.isoformat() if promise else None):
        return False                                    # the customer promise changed
    if (ack.get("pinned_po_eta") or None) != (row.get("expected_date") or None):
        return False                                    # the vendor moved the PO ETA
    return True


def escalation_level(ack: Optional[Dict[str, Any]], today: date) -> int:
    """0 = owner's queue, 1 = missed one check-back, 2 = missed two or more.

    A missed check-back is the signal that a special order is being parked rather than worked,
    which is exactly the pattern that produced the 22-92 day tail.
    """
    if not ack:
        return 0
    checkback = str(ack.get("checkback_date") or "")[:10]
    if not checkback or checkback >= today.isoformat():
        return int(ack.get("escalation_level") or 0)
    return min(int(ack.get("escalation_level") or 0) + 1, 2)


def build_escalations(orders: List[Dict[str, Any]], acks: Dict[str, Dict[str, Any]],
                      today: Optional[date] = None) -> Dict[str, Any]:
    """The full SLA view: every row annotated, plus the counts that drive the tiles."""
    today = today or date.today()
    rows: List[Dict[str, Any]] = []
    for row in orders:
        sla = compute_sla(row, today)
        work = compute_work_state(row, sla, today)
        so_id = str(row.get("special_order_id") or "")
        ack = acks.get(so_id)
        active = ack_is_active(ack, row, today)
        level = escalation_level(ack, today)
        enriched = {**row, **sla, **work}
        enriched.update({
            "ack": ack,
            "ack_active": active,
            "escalation_level": level,
            # Operational actionability is wider than delivery-SLA breach: missing promises
            # and post-receipt close-out are real work, but remain outside the on-time metric.
            "actionable": work["work_state"] != "on_track" and not active,
            "checkback_due": bool(ack and not active and ack.get("checkback_date")
                                  and str(ack["checkback_date"])[:10] <= today.isoformat()),
        })
        rows.append(enriched)

    rows.sort(key=lambda r: (
        not r["actionable"], r["sla_severity_rank"], r.get("action_due_date") or "9999-12-31",
        -(r.get("days_since_creation") or 0),
    ))
    summary = {
        "by_severity": {s: sum(1 for r in rows if r["sla_severity"] == s) for s in SEVERITY_ORDER},
        "by_owner": {o: sum(1 for r in rows if r["sla_owner"] == o and r["actionable"])
                     for o in ("procurement", "receiving", "cs")},
        "by_action_owner": {
            o: sum(1 for r in rows if r.get("action_owner") == o and r["actionable"])
            for o in ("procurement", "service", "cs", "receiving", "retail")
        },
        "by_work_state": {
            state: sum(1 for r in rows if r.get("work_state") == state)
            for state in ("needs_ordering", "vendor_followup", "promise_needed", "closeout", "on_track")
        },
        "by_queue_state": {
            state: sum(1 for r in rows if state in (r.get("queue_states") or []))
            for state in ("needs_ordering", "in_transit", "vendor_followup", "promise_needed", "closeout", "on_track")
        },
        "missing_promise_by_owner": {
            o: sum(1 for r in rows if r.get("promise_owner") == o)
            for o in ("service", "cs")},
        "actionable": sum(1 for r in rows if r["actionable"]),
        "acked": sum(1 for r in rows if r["ack_active"]),
        "checkback_due": sum(1 for r in rows if r["checkback_due"]),
        "escalated": sum(1 for r in rows if r["escalation_level"] >= 1),
        "missing_promise": sum(1 for r in rows if r.get("missing_promise")),
    }
    return {"orders": rows, "summary": summary, "reason_codes": REASON_CODES}
