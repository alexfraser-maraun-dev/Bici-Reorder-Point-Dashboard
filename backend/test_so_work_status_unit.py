"""Offline unit tests for the Start/Done work statuses. No network, no database.

These cover the rules that decide when a row comes BACK to Action required, which is the whole
safety story behind letting an employee clear a ticket in one click with no reason entry.
"""

import sys
from datetime import date
from typing import Any, Dict

sys.path.insert(0, ".")
from app.services import so_sla_service as sla  # noqa: E402

TODAY = date(2026, 8, 21)


def _row(**kw) -> Dict[str, Any]:
    base = {
        "special_order_id": "44690",
        "procurement_stage": "unordered_po",
        "procurement_stage_index": 1,
        "expected_date": "2026-09-01",
        "shopify_expected_date": "2026-09-05",
        "days_since_creation": 30,
        "days_in_stage": 5,
        "kind": "ls",
        "store": "Vancouver",
    }
    base.update(kw)
    return base


def _ack(**kw) -> Dict[str, Any]:
    base = {
        "special_order_id": "44690",
        "acked_by": "buyer@bici.cc",
        "reason_code": "in_progress",
        "note": None,
        "acked_at": "2026-08-21T10:00:00+00:00",
        "checkback_date": "2026-08-24",
        "pinned_stage": None,
        "pinned_promise": None,
        "pinned_po_eta": None,
        "pinned_work_state": "needs_ordering",
        "escalation_level": 0,
        "work_status": "in_progress",
    }
    base.update(kw)
    return base


def test_started_row_is_silenced_until_its_checkback():
    row = _row()
    ack = _ack(checkback_date="2026-08-24")
    assert sla.ack_is_active(ack, row, TODAY, work_state="needs_ordering") is True
    # The day after the check-back it returns to the queue on its own.
    assert sla.ack_is_active(ack, row, date(2026, 8, 25), work_state="needs_ordering") is False
    print("test_started_row_is_silenced_until_its_checkback OK")


def test_started_row_survives_an_eta_or_promise_edit():
    """Editing the ETA is part of working a ticket. Re-arming on your own edit would throw the
    row back at the person who just claimed it, which is why in_progress drops those pins."""
    ack = _ack()
    moved = _row(expected_date="2026-10-15", shopify_expected_date="2026-10-20",
                 procurement_stage="ordered")
    assert sla.ack_is_active(ack, moved, TODAY, work_state="needs_ordering") is True
    print("test_started_row_survives_an_eta_or_promise_edit OK")


def test_done_never_expires_but_new_work_reopens_it():
    ack = _ack(work_status="done", reason_code="done", checkback_date="2036-08-19",
               pinned_work_state="needs_ordering")
    row = _row()
    # No date brings a finished task back...
    assert sla.ack_is_active(ack, row, date(2030, 1, 1), work_state="needs_ordering") is True
    # ...but a genuinely different job on the same order does. A received order that later
    # needs a customer close-out call must not stay hidden behind "I ordered it".
    assert sla.ack_is_active(ack, row, TODAY, work_state="closeout") is False
    print("test_done_never_expires_but_new_work_reopens_it OK")


def test_done_never_escalates_but_keeps_prior_level():
    ack = _ack(work_status="done", checkback_date="2020-01-01", escalation_level=1)
    assert sla.escalation_level(ack, TODAY) == 1
    # The same lapsed date on a park does escalate.
    parked = _ack(work_status="parked", reason_code="vendor_backorder",
                  checkback_date="2020-01-01", escalation_level=1)
    assert sla.escalation_level(parked, TODAY) == 2
    print("test_done_never_escalates_but_keeps_prior_level OK")


def test_legacy_acks_without_the_column_stay_parks():
    """Rows written before work_status existed must keep behaving exactly as before."""
    legacy = {
        "checkback_date": "2026-08-24",
        "pinned_stage": "unordered_po",
        "pinned_promise": "2026-09-05",
        "pinned_po_eta": "2026-09-01",
        "escalation_level": 0,
    }
    assert sla.ack_work_status(legacy) == "parked"
    assert sla.ack_is_active(legacy, _row(), TODAY) is True
    # Still re-arms on all three pins.
    assert sla.ack_is_active(legacy, _row(expected_date="2026-10-01"), TODAY) is False
    assert sla.ack_is_active(legacy, _row(procurement_stage="ordered"), TODAY) is False
    print("test_legacy_acks_without_the_column_stay_parks OK")


def test_missing_pinned_work_state_falls_back_to_the_date():
    """A started record with no pin must not re-arm every row at once."""
    ack = _ack(pinned_work_state=None)
    assert sla.ack_is_active(ack, _row(), TODAY, work_state="closeout") is True
    assert sla.ack_is_active(ack, _row(), date(2026, 8, 25), work_state="closeout") is False
    print("test_missing_pinned_work_state_falls_back_to_the_date OK")


def test_build_escalations_reports_status_and_clears_the_action_queue():
    row = _row(special_order_id="1", procurement_stage="open_pool", procurement_stage_index=0,
               expected_date=None, order_id=None)
    built = sla.build_escalations([row], {}, TODAY)
    work_state = built["orders"][0]["work_state"]
    assert built["orders"][0]["actionable"] is True
    assert built["orders"][0]["work_status"] is None

    started = sla.build_escalations(
        [row], {"1": _ack(special_order_id="1", pinned_work_state=work_state)}, TODAY)
    only = started["orders"][0]
    assert only["ack_active"] is True
    assert only["work_status"] == "in_progress"
    assert only["actionable"] is False
    assert only["checkback_due"] is False
    assert started["summary"]["in_progress"] == 1

    done = sla.build_escalations(
        [row], {"1": _ack(special_order_id="1", work_status="done", reason_code="done",
                          checkback_date="2020-01-01", pinned_work_state=work_state)}, TODAY)
    finished = done["orders"][0]
    assert finished["work_status"] == "done"
    assert finished["actionable"] is False
    # A long-past date on a done record must never present as "check back now".
    assert finished["checkback_due"] is False
    assert done["summary"]["done"] == 1
    print("test_build_escalations_reports_status_and_clears_the_action_queue OK")


if __name__ == "__main__":
    test_started_row_is_silenced_until_its_checkback()
    test_started_row_survives_an_eta_or_promise_edit()
    test_done_never_expires_but_new_work_reopens_it()
    test_done_never_escalates_but_keeps_prior_level()
    test_legacy_acks_without_the_column_stay_parks()
    test_missing_pinned_work_state_falls_back_to_the_date()
    test_build_escalations_reports_status_and_clears_the_action_queue()
    print("\nAll special-order work-status unit tests passed.")
