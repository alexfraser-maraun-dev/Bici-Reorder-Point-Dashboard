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


def test_open_clock_takes_the_earlier_shopify_date():
    # The `SO` tag was added 6 days after the order went live, so the customer had already been
    # waiting 6 days before the Lightspeed special order existed.
    late_tag = _row(created_date="2026-08-15", shopify_order_created_at="2026-08-09T10:00:00-07:00")
    got = sla.compute_open_clock(late_tag, TODAY)
    assert got["demand_started_date"] == "2026-08-09", got
    assert got["demand_started_source"] == "shopify_order", got
    assert got["days_open"] == 11, got
    assert got["intake_lag_days"] == 6, got

    # A Shopify order linked AFTER the special order is a later link, not a late tag. No lag.
    later = _row(created_date="2026-08-09", shopify_order_created_at="2026-08-15T10:00:00-07:00")
    got = sla.compute_open_clock(later, TODAY)
    assert got["demand_started_date"] == "2026-08-09", got
    assert got["demand_started_source"] == "ls_so", got
    assert got["intake_lag_days"] is None, got

    # Workorder-origin rows carry no Shopify date, so they stay on the Lightspeed SO date --
    # Workorder.timeIn is when the BIKE was booked in, not when the part was requested.
    bench = _row(created_date="2026-08-15", source="workorder", workorder_time_in="2026-07-01")
    got = sla.compute_open_clock(bench, TODAY)
    assert got["demand_started_date"] == "2026-08-15", got
    assert got["days_open"] == 5, got
    assert got["intake_lag_days"] is None, got
    print("test_open_clock_takes_the_earlier_shopify_date OK")


def test_open_clock_never_moves_the_sla_clock():
    # The whole point of the separation: days_since_creation drives severity, dwell and the
    # archive window, and must be unaffected by the display clock.
    row = _row(created_date="2026-08-15", days_since_creation=5,
               shopify_order_created_at="2026-07-01T10:00:00-07:00")
    out = sla.build_escalations([row], {}, TODAY)["orders"][0]
    assert out["days_open"] == 50, out["days_open"]
    assert out["days_since_creation"] == 5, out["days_since_creation"]
    assert len(sla.filter_live_window([out], 30)) == 1  # windowed on the LS clock, so it stays
    print("test_open_clock_never_moves_the_sla_clock OK")


def test_earliest_ready_covers_every_basis():
    def basis(**kw):
        got = sla.compute_sla(_row(**kw), TODAY)
        return got["earliest_ready_date"], got["earliest_ready_basis"]

    # Received: the individual SO's own receipt timestamp, nothing added.
    assert basis(procurement_stage="received", so_received_date="2026-08-18") == \
        ("2026-08-18", "received")
    # Ordered: the PO says the box lands 08-25; the customer can have it a buffer day later.
    assert basis(procurement_stage="ordered", expected_date="2026-08-25") == \
        ("2026-08-26", "po_eta_plus_buffer")
    # Unordered with a fastest-path annotation: use it as-is, it already carries the buffer.
    assert basis(fastest_landing_date="2026-08-24") == ("2026-08-24", "fastest_route")
    # Annotation skipped (BigQuery hiccup): fall back to today + lead + buffer, never blank.
    assert basis(vendor_lead_time_days=5) == ("2026-08-26", "lead_time_default")
    print("test_earliest_ready_covers_every_basis OK")


def test_priority_band_a_scales_with_lateness():
    def score(promise):
        row = _row(shopify_expected_date=promise, vendor_lead_time_days=5)
        return sla.build_escalations([row], {}, TODAY)["orders"][0]["priority_score"]

    assert score("2026-08-18") == 7, score("2026-08-18")    # 2 days late
    assert score("2026-08-16") == 8, score("2026-08-16")    # 4 days late
    assert score("2026-08-11") == 9, score("2026-08-11")    # 9 days late
    assert score("2026-07-21") == 10, score("2026-07-21")   # 30 days late
    print("test_priority_band_a_scales_with_lateness OK")


def test_priority_band_b_ramps_on_window_slack():
    def score(**kw):
        row = _row(vendor_lead_time_days=5, **kw)
        return sla.build_escalations([row], {}, TODAY)["orders"][0]["priority_score"]

    # With a real quote: order-by is promise - 6d. Slack -1 / 0 / 3 / 10.
    assert score(shopify_expected_date="2026-08-25") == 6, score(shopify_expected_date="2026-08-25")
    assert score(shopify_expected_date="2026-08-26") == 5
    assert score(shopify_expected_date="2026-08-29") == 4
    assert score(shopify_expected_date="2026-09-05") == 2

    # With NO quote the inferred window does the same work, so the ~third of the board that was
    # never quoted is still rankable rather than flat.
    assert score(could_have_landed="2026-08-25") == 6
    assert score(could_have_landed="2026-08-26") == 5
    assert score(could_have_landed="2026-08-29") == 4
    assert score(could_have_landed="2026-09-05") == 2

    inferred = sla.compute_sla(_row(vendor_lead_time_days=5, could_have_landed="2026-09-05"), TODAY)
    assert inferred["scoring_window_source"] == "inferred", inferred
    assert inferred["order_by_date"] is None, inferred  # stays promise-only; never invented
    print("test_priority_band_b_ramps_on_window_slack OK")


def test_priority_bumps_never_manufacture_a_broken_promise():
    # A comfortable row carrying every bump: no ETA, stalled stage, two missed check-backs.
    row = _row(procurement_stage="unordered_po", vendor_lead_time_days=5,
               shopify_expected_date="2026-09-05", flag="no_eta",
               days_in_stage=40, po_created_date="2026-07-11")
    ack = {"checkback_date": "2026-08-01", "escalation_level": 2, "work_status": "parked",
           "pinned_stage": "unordered_po", "reason_code": "other"}
    got = sla.build_escalations([row], {"1": ack}, TODAY)["orders"][0]
    assert got["escalation_level"] == 2, got["escalation_level"]
    # 2 (comfortable) + 3 bumps = 5, capped below the broken-promise band either way.
    assert got["priority_score"] == 5, (got["priority_score"], got["priority_reasons"])
    assert got["priority_band"] == "high", got["priority_band"]
    assert len(got["priority_reasons"]) == 4, got["priority_reasons"]

    # Even from the top of band B the ceiling holds.
    hot = _row(procurement_stage="unordered_po", vendor_lead_time_days=5,
               shopify_expected_date="2026-08-25", flag="no_eta",
               days_in_stage=40, po_created_date="2026-07-11")
    assert sla.build_escalations([hot], {}, TODAY)["orders"][0]["priority_score"] == 6
    print("test_priority_bumps_never_manufacture_a_broken_promise OK")


def test_placed_orders_are_scored_against_the_vendor_date_not_a_counterfactual():
    """An unquoted PO is judged late against the vendor's own date, never against
    `could_have_landed`. Measured live, the counterfactual put 200 of 373 orders on the same
    score -- it is anchored months back, so every placed order blows it by construction."""
    def got(**kw):
        row = _row(procurement_stage="ordered", ordered_date="2026-08-01",
                   could_have_landed="2026-06-01", vendor_lead_time_days=5, **kw)
        return sla.build_escalations([row], {}, TODAY)["orders"][0]

    overdue = got(expected_date="2026-08-14")          # customer could have had it 08-15
    assert overdue["scoring_window_source"] == "po_eta", overdue["scoring_window_source"]
    assert overdue["window_slack_days"] == -5, overdue["window_slack_days"]
    assert overdue["priority_score"] == 6, overdue["priority_score"]

    imminent = got(expected_date="2026-08-20")
    assert imminent["window_slack_days"] == 1 and imminent["priority_score"] == 5, imminent

    healthy = got(expected_date="2026-09-10")
    assert healthy["window_slack_days"] == 22, healthy["window_slack_days"]
    assert healthy["priority_score"] == 2, (healthy["priority_score"], healthy["priority_reasons"])
    print("test_placed_orders_are_scored_against_the_vendor_date_not_a_counterfactual OK")


def test_priority_flags_a_po_that_already_lands_late():
    # The PO is placed and looks healthy, but its own ETA lands after the quoted date.
    row = _row(procurement_stage="ordered", ordered_date="2026-08-10",
               expected_date="2026-09-04", shopify_expected_date="2026-09-01",
               vendor_lead_time_days=5)
    got = sla.build_escalations([row], {}, TODAY)["orders"][0]
    assert got["window_slack_days"] == -4, got["window_slack_days"]
    assert got["priority_score"] == 6, (got["priority_score"], got["priority_reasons"])

    # A completed PO that never carried this line is a backorder nobody was told about: the
    # slack arithmetic alone reads it as comfortable, the bump is what rescues it.
    blind = _row(procurement_stage="ordered", ordered_date="2026-08-10",
                 expected_date="2026-08-25", shopify_expected_date="2026-09-30",
                 receiving_state="po_complete_so_unreceived", vendor_lead_time_days=5)
    got = sla.build_escalations([blind], {}, TODAY)["orders"][0]
    assert got["priority_score"] == 3, (got["priority_score"], got["priority_reasons"])
    assert any("backordered" in r for r in got["priority_reasons"]), got["priority_reasons"]
    print("test_priority_flags_a_po_that_already_lands_late OK")


def test_priority_band_c_is_closeout_age():
    def score(**kw):
        row = _row(procurement_stage="received", so_received=True, **kw)
        return sla.build_escalations([row], {}, TODAY)["orders"][0]["priority_score"]

    # Fully closed out: nothing left to do.
    assert score(so_received_date="2026-08-19", contacted=True, completed=True) == 1
    # Landed yesterday, nobody called yet -- real work, but not urgent.
    assert score(so_received_date="2026-08-19", contacted=False) == 2
    assert score(so_received_date="2026-08-10", contacted=False) == 3    # 10 days uncalled
    assert score(so_received_date="2026-07-15", contacted=False) == 4    # 36 days uncalled
    # A received order can never reach the broken-promise band, however old.
    assert score(so_received_date="2026-01-01", contacted=False,
                 shopify_expected_date="2026-02-01") == 4
    print("test_priority_band_c_is_closeout_age OK")


def test_fulfilled_shopify_order_outranks_every_procurement_action():
    """A finished Shopify order on a still-open SO is CS cleanup, not procurement work.

    Live 2026-08-25: 3 of 372 rows, but one was the highest-scoring row on the whole board --
    telling a buyer to chase a vendor for a part whose Shopify order was fulfilled and partly
    refunded 71 days earlier.
    """
    def got(**kw):
        row = _row(procurement_stage="ordered", ordered_date="2026-08-01",
                   expected_date="2026-08-14", shopify_order_id="7241601646655",
                   shopify_expected_date="2026-06-10",  # long past: would be promise_missed
                   vendor_lead_time_days=5, **kw)
        return sla.build_escalations([row], {}, TODAY)["orders"][0]

    # Baseline: without the fulfilment signal this is the worst kind of row.
    plain = got(shopify_fulfillment_status="UNFULFILLED")
    assert plain["sla_severity"] == "promise_missed" and plain["priority_score"] == 10, plain["priority_score"]
    assert plain["work_state"] == "vendor_followup", plain["work_state"]

    done = got(shopify_fulfillment_status="FULFILLED", order_id="16267")
    assert done["work_state"] == "shopify_fulfilled", done["work_state"]
    assert done["queue_states"] == ["shopify_fulfilled"], done["queue_states"]
    assert done["action_owner"] == "cs", done["action_owner"]
    assert "check out or cancel" in done["next_action"], done["next_action"]
    assert done["shopify_order_closed"] == "fulfilled"
    # The breach is an artefact of nobody closing the record, so it must not reach Band A.
    assert done["sla_severity"] == "promise_missed"      # the verdict itself is unchanged
    assert done["priority_score"] == 4, (done["priority_score"], done["priority_reasons"])
    assert done["actionable"] is True

    # No PO attached: nothing is committed, so a point less.
    assert got(shopify_fulfillment_status="FULFILLED", order_id=None)["priority_score"] == 3

    # Refunded-and-restocked reads as cancel, not check out.
    restocked = got(shopify_fulfillment_status="RESTOCKED", order_id="16267")
    assert "cancel the SO" in restocked["next_action"], restocked["next_action"]
    assert restocked["shopify_order_closed"] == "restocked"

    # PARTIALLY_FULFILLED is NOT this: the special-order line is usually the one still
    # outstanding, which is exactly normal. Flagging it would fire on 9 healthy live rows.
    partial = got(shopify_fulfillment_status="PARTIALLY_FULFILLED")
    assert partial["work_state"] == "vendor_followup", partial["work_state"]
    assert partial["shopify_order_closed"] is None

    # A link that no longer resolves proves nothing about the customer.
    broken = got(shopify_fulfillment_status="FULFILLED", link_broken="7241601646655")
    assert broken["shopify_order_closed"] is None, broken["shopify_order_closed"]

    # Received rows keep their close-out routing: same instruction, right owner.
    landed = _row(procurement_stage="received", so_received=True, so_received_date="2026-08-18",
                  shopify_order_id="7241601646655", shopify_fulfillment_status="FULFILLED",
                  contacted=True)
    out = sla.build_escalations([landed], {}, TODAY)["orders"][0]
    assert out["work_state"] != "shopify_fulfilled", out["work_state"]
    assert out["closeout_state"] == "lightspeed_completion_pending", out["closeout_state"]
    print("test_fulfilled_shopify_order_outranks_every_procurement_action OK")


def test_priority_is_intrinsic_not_damped_by_parking():
    # Parking records that a human knows about a problem. It does not make the problem smaller,
    # and a score that sank on a button click would hide the worst rows from the sort built to
    # find them.
    row = _row(shopify_expected_date="2026-08-11", vendor_lead_time_days=5)
    ack = {"checkback_date": "2026-09-01", "work_status": "parked", "reason_code": "vendor_backorder",
           "pinned_stage": "open_pool", "pinned_promise": "2026-08-11", "pinned_po_eta": None}
    got = sla.build_escalations([row], {"1": ack}, TODAY)["orders"][0]
    assert got["ack_active"] is True, got["ack_active"]
    assert got["actionable"] is False, got["actionable"]
    assert got["priority_score"] == 9, got["priority_score"]
    print("test_priority_is_intrinsic_not_damped_by_parking OK")


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
    test_open_clock_takes_the_earlier_shopify_date()
    test_open_clock_never_moves_the_sla_clock()
    test_earliest_ready_covers_every_basis()
    test_priority_band_a_scales_with_lateness()
    test_priority_band_b_ramps_on_window_slack()
    test_priority_bumps_never_manufacture_a_broken_promise()
    test_placed_orders_are_scored_against_the_vendor_date_not_a_counterfactual()
    test_priority_flags_a_po_that_already_lands_late()
    test_priority_band_c_is_closeout_age()
    test_fulfilled_shopify_order_outranks_every_procurement_action()
    test_priority_is_intrinsic_not_damped_by_parking()
    print("\nAll SLA severity/ack unit tests passed.")
