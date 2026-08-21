"""Offline unit tests for special-order SLA severity, backward scheduling and ack semantics."""

import sys
from datetime import date

sys.path.insert(0, ".")
from app.services import so_sla_service as sla  # noqa: E402

TODAY = date(2026, 8, 20)


def _row(**kw):
    base = {
        "special_order_id": "1", "procurement_stage": "open_pool", "shop_id": "3",
        "days_since_creation": 5, "days_in_stage": 1, "expected_date": None,
        "shopify_expected_date": None, "workorder_eta_out": None,
        "vendor_name": None, "vendor_lead_time_days": None, "available_vendors": [],
    }
    base.update(kw)
    return base


def test_promise_precedence_and_lead_time_source():
    both = _row(shopify_expected_date="2026-09-01", workorder_eta_out="2026-09-20")
    assert sla.resolve_promise(both) == (date(2026, 9, 1), "shopify_metafield")
    # A workorder's etaOut is the bike's booking date, not a promise about the part (it equals
    # timeIn for 76% of workorders and predates the SO in 31% of cases), so it must NOT resolve
    # as a promise while TREAT_WORKORDER_ETA_AS_PROMISE is False.
    svc = _row(workorder_eta_out="2026-09-20")
    assert sla.TREAT_WORKORDER_ETA_AS_PROMISE is False
    assert sla.resolve_promise(svc) == (None, None)
    assert sla.resolve_promise(_row()) == (None, None)
    manual = _row(source="workorder", service_promise_date="2026-09-10",
                  shopify_expected_date="2026-09-01")
    assert sla.resolve_promise(manual) == (date(2026, 9, 10), "service_manual")

    # Once a PO exists we know the real vendor; before that, the fastest that can supply.
    assert sla.effective_lead_time(_row(vendor_lead_time_days=5)) == (5, "po_vendor")
    assert sla.effective_lead_time(
        _row(available_vendors=[{"vendor_name": "HLC", "lead_time_days": 2}])
    ) == (2, "fastest_qualifying_vendor")
    assert sla.effective_lead_time(_row()) == (sla.FALLBACK_LEAD_TIME_DAYS, "default")
    print("test_promise_precedence_and_lead_time_source OK")


def test_backward_schedule_arithmetic():
    # promise 2026-09-01, lead 5d, buffer 1d -> order by 08-26, slack 6d from 08-20.
    got = sla.compute_sla(_row(shopify_expected_date="2026-09-01", vendor_lead_time_days=5), TODAY)
    assert got["order_by_date"] == "2026-08-26", got
    assert got["slack_days"] == 6, got
    assert got["sla_severity"] == "on_track", got
    print("test_backward_schedule_arithmetic OK")


def test_severity_boundaries():
    def sev(**kw):
        return sla.compute_sla(_row(**kw), TODAY)["sla_severity"]

    # Slack 0 = today is the last day that still works.
    assert sev(shopify_expected_date="2026-08-26", vendor_lead_time_days=5) == "order_today"
    # Slack 3 = inside the at-risk window; slack 4 = clear.
    assert sev(shopify_expected_date="2026-08-29", vendor_lead_time_days=5) == "at_risk"
    assert sev(shopify_expected_date="2026-08-30", vendor_lead_time_days=5) == "on_track"
    # Promise already gone.
    assert sev(shopify_expected_date="2026-08-19", vendor_lead_time_days=5) == "promise_missed"
    # Unreachable even ordering now: promise in 3d, lead 5d + buffer 1d.
    assert sev(shopify_expected_date="2026-08-23", vendor_lead_time_days=5) == "impossible"
    # No promise at all is a CS gap, not a procurement breach.
    assert sev() == "no_promise"
    assert sla.compute_sla(_row(), TODAY)["missing_promise"] is True
    # Who must set the missing promise depends on origin: the bench owns workorder parts ETAs,
    # CS owns the Shopify metafield. Blaming CS for both hands them the bench's backlog.
    service = sla.compute_sla(_row(source="workorder"), TODAY)
    assert service["promise_owner"] == "service"
    assert "Service needs to record" in service["sla_reason"]
    assert sla.compute_sla(_row(source="shopify"), TODAY)["promise_owner"] == "cs"
    print("test_severity_boundaries OK")


def test_clock_stops_at_receipt():
    # The stale ready-for-pickup tail must never contaminate the on-time number.
    got = sla.compute_sla(
        _row(procurement_stage="received", days_in_stage=705,
             shopify_expected_date="2020-01-01"), TODAY)
    assert got["sla_severity"] == "closed_out", got
    assert got["sla_owner"] == "receiving", got
    assert got["missing_promise"] is False, got
    print("test_clock_stops_at_receipt OK")


def test_worse_of_two_clocks_wins():
    # Comfortable promise, but the stage itself has stalled -> must still surface.
    stalled = sla.compute_sla(
        _row(shopify_expected_date="2026-12-01", vendor_lead_time_days=5, days_in_stage=48), TODAY)
    assert stalled["sla_severity"] == "stage_stalled", stalled
    assert stalled["days_over_stage_sla"] == 46, stalled

    # A promise breach outranks a dwell breach -- it is the more urgent fact.
    both = sla.compute_sla(
        _row(shopify_expected_date="2026-08-19", vendor_lead_time_days=5, days_in_stage=48), TODAY)
    assert both["sla_severity"] == "promise_missed", both

    # No promise + stalled stage -> the stall is what gets surfaced, not "no_promise".
    nop = sla.compute_sla(_row(days_in_stage=48), TODAY)
    assert nop["sla_severity"] == "stage_stalled", nop
    print("test_worse_of_two_clocks_wins OK")


def test_per_store_stage_limits():
    # Victoria (shop 2) genuinely runs slower; a global limit would be wrong for both stores.
    assert sla.stage_sla_days("open_pool", "3") == 2
    assert sla.stage_sla_days("open_pool", "2") == 4
    assert sla.compute_sla(_row(shop_id="3", days_in_stage=3), TODAY)["sla_severity"] == "stage_stalled"
    assert sla.compute_sla(_row(shop_id="2", days_in_stage=3), TODAY)["sla_severity"] == "no_promise"
    print("test_per_store_stage_limits OK")


def test_ack_rearms_on_each_pinned_value():
    row = _row(procurement_stage="open_pool", shopify_expected_date="2026-09-01", expected_date=None)
    ack = {"checkback_date": "2026-08-27", "pinned_stage": "open_pool",
           "pinned_promise": "2026-09-01", "pinned_po_eta": None}
    assert sla.ack_is_active(ack, row, TODAY) is True

    # 1. check-back date arrives
    assert sla.ack_is_active({**ack, "checkback_date": "2026-08-19"}, row, TODAY) is False
    # 2. the SO moved stage (or regressed)
    assert sla.ack_is_active(ack, {**row, "procurement_stage": "unordered_po"}, TODAY) is False
    # 3. the customer promise moved
    assert sla.ack_is_active(ack, {**row, "shopify_expected_date": "2026-10-01"}, TODAY) is False
    # 4. the vendor moved the PO ETA
    assert sla.ack_is_active(ack, {**row, "expected_date": "2026-09-15"}, TODAY) is False
    assert sla.ack_is_active(None, row, TODAY) is False
    print("test_ack_rearms_on_each_pinned_value OK")


def test_escalation_ladder_and_queue():
    assert sla.escalation_level(None, TODAY) == 0
    assert sla.escalation_level({"checkback_date": "2026-08-27", "escalation_level": 0}, TODAY) == 0
    assert sla.escalation_level({"checkback_date": "2026-08-19", "escalation_level": 0}, TODAY) == 1
    assert sla.escalation_level({"checkback_date": "2026-08-19", "escalation_level": 1}, TODAY) == 2
    assert sla.escalation_level({"checkback_date": "2026-08-19", "escalation_level": 2}, TODAY) == 2

    orders = [
        _row(special_order_id="a", shopify_expected_date="2026-08-19", vendor_lead_time_days=5),
        _row(special_order_id="b", shopify_expected_date="2026-12-01", vendor_lead_time_days=5),
        _row(special_order_id="c", procurement_stage="received", days_in_stage=705),
    ]
    acks = {"a": {"checkback_date": "2026-08-27", "pinned_stage": "open_pool",
                  "pinned_promise": "2026-08-19", "pinned_po_eta": None,
                  "reason_code": "vendor_backorder", "escalation_level": 0}}
    out = sla.build_escalations(orders, acks, TODAY)
    ids = [r["special_order_id"] for r in out["orders"]]
    assert ids[-1] == "a", ids  # parked breach sorts behind active operational work
    assert out["summary"]["acked"] == 1, out["summary"]
    # Operational work is wider than a delivery breach: b still needs PO allocation and c
    # still needs post-receipt close-out. The parked breach remains suppressed.
    assert out["summary"]["actionable"] == 2, out["summary"]
    assert out["summary"]["by_severity"]["closed_out"] == 1
    print("test_escalation_ladder_and_queue OK")


def test_operational_work_states_and_closeout_are_separate_from_delivery_sla():
    needs = sla.build_escalations([_row(source="workorder", created_date="2026-08-01")], {}, TODAY)["orders"][0]
    assert needs["sla_severity"] == "no_promise"
    assert needs["work_state"] == "needs_ordering"
    assert needs["queue_states"] == ["needs_ordering", "promise_needed"]
    assert needs["action_owner"] == "procurement"
    assert needs["actionable"] is True

    followup = sla.build_escalations([
        _row(procurement_stage="ordered", flag="no_eta", source="shopify")
    ], {}, TODAY)["orders"][0]
    assert followup["work_state"] == "vendor_followup"
    assert followup["queue_states"] == ["in_transit", "vendor_followup", "promise_needed"]
    assert followup["action_owner"] == "procurement"

    service_close = sla.build_escalations([
        _row(procurement_stage="received", source="workorder", workorder_id="10",
             workorder_status="Waiting Parts", contacted=False, completed=False,
             po_received_date="2026-08-16", so_received_date="2026-08-18")
    ], {}, TODAY)["orders"][0]
    assert service_close["sla_severity"] == "closed_out"
    assert service_close["work_state"] == "closeout"
    assert service_close["closeout_state"] == "workorder_action_required"
    assert service_close["action_owner"] == "service"
    assert service_close["action_due_date"] == "2026-08-19"
    assert service_close["actionable"] is True

    retail_close = sla.build_escalations([
        _row(procurement_stage="received", contacted=False, completed=False)
    ], {}, TODAY)["orders"][0]
    assert retail_close["closeout_state"] == "ready_not_called"
    assert retail_close["action_owner"] == "retail"

    complete = sla.build_escalations([
        _row(procurement_stage="received", contacted=True, completed=True)
    ], {}, TODAY)["orders"][0]
    assert complete["closeout_state"] == "complete"
    assert complete["work_state"] == "on_track"
    assert complete["actionable"] is False
    print("test_operational_work_states_and_closeout_are_separate_from_delivery_sla OK")


def test_service_promise_clears_promise_needed_queue():
    row = sla.build_escalations([
        _row(procurement_stage="ordered", source="workorder", flag="none",
             service_promise_date="2026-09-15")
    ], {}, TODAY)["orders"][0]
    assert row["promise_source"] == "service_manual"
    assert row["missing_promise"] is False
    assert "promise_needed" not in row["queue_states"]
    assert row["work_state"] == "on_track"
    print("test_service_promise_clears_promise_needed_queue OK")


def test_live_window_zero_and_none_return_historical_rows():
    rows = [
        {"special_order_id": "live", "days_since_creation": 10},
        {"special_order_id": "old", "days_since_creation": 900},
        {"special_order_id": "unknown", "days_since_creation": None},
    ]
    assert len(sla.filter_live_window(rows, 365)) == 2
    assert sla.filter_live_window(rows, 0) == rows
    assert sla.filter_live_window(rows, None) == rows
    print("test_live_window_zero_and_none_return_historical_rows OK")


if __name__ == "__main__":
    test_promise_precedence_and_lead_time_source()
    test_backward_schedule_arithmetic()
    test_severity_boundaries()
    test_clock_stops_at_receipt()
    test_worse_of_two_clocks_wins()
    test_per_store_stage_limits()
    test_ack_rearms_on_each_pinned_value()
    test_escalation_ladder_and_queue()
    test_operational_work_states_and_closeout_are_separate_from_delivery_sla()
    test_service_promise_clears_promise_needed_queue()
    test_live_window_zero_and_none_return_historical_rows()
    print("\nAll SLA severity/ack unit tests passed.")
