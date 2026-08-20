"""Offline unit tests for the PO recommendation engine. No network, no database."""

import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from app.services import po_recommendation_service as reco  # noqa: E402

TODAY = date(2026, 8, 20)


def _row(**kw):
    base = {
        "special_order_id": "1", "item_id": "100", "shop_id": "3", "store": "Bici Adanac",
        "unit_quantity": 1, "promise_date": None, "available_vendors": [],
    }
    base.update(kw)
    return base


def _ctx(stock=None, unallocated=None, open_orders=None, cadence=None):
    return {"stock": stock or {}, "unallocated": unallocated or {},
            "open_orders": open_orders or [], "cadence": cadence or {}}


def test_in_stock_beats_everything():
    # An item already on the shelf must never become a PO line, however good the PO looks.
    ctx = _ctx(
        stock={("100", "3"): {"sellable": 4, "qoh": 4}},
        unallocated={("100", "3"): [{"order_id": "9", "order_line_id": "1", "reference_number": "PO9",
                                     "vendor_id": "5", "vendor_name": "HLC",
                                     "expected_arrival_at": "2026-08-21", "unallocated_units": 5,
                                     "units_remaining": 5, "ordered_at": None}]})
    got = reco.recommend(_row(), ctx, TODAY)
    assert got["tier"] == "in_stock", got
    assert "Confirm on the shelf" in got["reason"]
    # The inbound PO is still offered as an alternative, not discarded.
    assert any(a["tier"] == "inbound_po" for a in got["alternatives"]), got["alternatives"]
    print("test_in_stock_beats_everything OK")


def test_insufficient_stock_does_not_count():
    # 1 sellable cannot satisfy a special order for 3.
    ctx = _ctx(stock={("100", "3"): {"sellable": 1, "qoh": 1}})
    assert reco.recommend(_row(unit_quantity=3), ctx, TODAY)["tier"] == "new_po"
    print("test_insufficient_stock_does_not_count OK")


def test_transfer_only_between_victoria_and_langford():
    # Victoria (2) <-> Langford (20) are close enough to transfer.
    ctx = _ctx(stock={("100", "20"): {"sellable": 5, "qoh": 5}})
    got = reco.recommend(_row(shop_id="2", store="Bici Victoria"), ctx, TODAY)
    assert got["tier"] == "transfer", got

    ctx2 = _ctx(stock={("100", "2"): {"sellable": 5, "qoh": 5}})
    assert reco.recommend(_row(shop_id="20"), ctx2, TODAY)["tier"] == "transfer"

    # Adanac (3) is not proximate: it must neither receive nor supply a transfer.
    assert reco.SISTER_STORE.get("3") is None
    ctx3 = _ctx(stock={("100", "2"): {"sellable": 5, "qoh": 5}})
    assert reco.recommend(_row(shop_id="3"), ctx3, TODAY)["tier"] == "new_po"
    ctx4 = _ctx(stock={("100", "3"): {"sellable": 5, "qoh": 5}})
    assert reco.recommend(_row(shop_id="2"), ctx4, TODAY)["tier"] == "new_po"
    print("test_transfer_only_between_victoria_and_langford OK")


def test_inbound_po_beats_draft_and_flags_stale_eta():
    inbound = {("100", "3"): [{"order_id": "9", "order_line_id": "1", "reference_number": "PO9",
                               "vendor_id": "5", "vendor_name": "HLC",
                               "expected_arrival_at": "2026-04-21", "unallocated_units": 2,
                               "units_remaining": 2, "ordered_at": None}]}
    drafts = [{"orderID": "77", "shopID": "3", "po_state": "unsent", "vendorID": "5",
               "refNum": "D77", "Vendor": {"name": "HLC"}, "createTime": "2026-08-01"}]
    got = reco.recommend(_row(available_vendors=[{"vendor_id": "5", "vendor_name": "HLC", "lead_time_days": 2}]),
                         _ctx(unallocated=inbound, open_orders=drafts), TODAY)
    assert got["tier"] == "inbound_po", got
    # An arrival date in the past is late, not fast — say so rather than implying delivery.
    assert got["recommendation"]["eta_overdue"] is True
    assert "that date has passed" in got["reason"]
    print("test_inbound_po_beats_draft_and_flags_stale_eta OK")


def test_drafts_restricted_to_store_state_and_qualifying_vendor():
    vendors = [{"vendor_id": "5", "vendor_name": "HLC", "lead_time_days": 2}]
    orders = [
        {"orderID": "1", "shopID": "2", "po_state": "unsent", "vendorID": "5", "Vendor": {"name": "HLC"}},   # wrong store
        {"orderID": "2", "shopID": "3", "po_state": "ordered", "vendorID": "5", "Vendor": {"name": "HLC"}},  # already placed
        {"orderID": "3", "shopID": "3", "po_state": "unsent", "vendorID": "99", "Vendor": {"name": "Nope"}}, # can't supply brand
        {"orderID": "4", "shopID": "3", "po_state": "unsent", "vendorID": "5", "Vendor": {"name": "HLC"}},   # the one
    ]
    got = reco.recommend(_row(available_vendors=vendors), _ctx(open_orders=orders), TODAY)
    assert got["tier"] == "draft_po", got
    assert got["recommendation"]["order_id"] == "4", got["recommendation"]
    print("test_drafts_restricted_to_store_state_and_qualifying_vendor OK")


def test_meets_promise_is_computed_against_the_quote():
    lines = {("100", "3"): [{"order_id": "9", "order_line_id": "1", "reference_number": "PO9",
                             "vendor_id": "5", "vendor_name": "HLC",
                             "expected_arrival_at": "2026-09-10", "unallocated_units": 1,
                             "units_remaining": 1, "ordered_at": None}]}
    late = reco.recommend(_row(promise_date="2026-09-01"), _ctx(unallocated=lines), TODAY)
    assert late["recommendation"]["meets_promise"] is False
    assert "after the customer promise" in late["reason"]

    ok = reco.recommend(_row(promise_date="2026-09-30"), _ctx(unallocated=lines), TODAY)
    assert ok["recommendation"]["meets_promise"] is True

    # No promise recorded -> unknowable, not False.
    none = reco.recommend(_row(), _ctx(unallocated=lines), TODAY)
    assert none["recommendation"]["meets_promise"] is None
    print("test_meets_promise_is_computed_against_the_quote OK")


def test_new_po_carries_vendor_and_cadence():
    got = reco.recommend(
        _row(available_vendors=[{"vendor_id": "5", "vendor_name": "HLC", "lead_time_days": 2}]),
        _ctx(cadence={("3", "5"): {"cadence_days": 5, "next_expected_order_date": "2026-08-24"}}),
        TODAY)
    assert got["tier"] == "new_po", got
    assert got["recommendation"]["next_order_date"] == "2026-08-24"
    assert "next HLC order due 2026-08-24" in got["reason"]
    print("test_new_po_carries_vendor_and_cadence OK")


def test_candidate_pos_labels_appendability():
    orders = [
        {"orderID": "1", "shopID": "3", "po_state": "unsent", "vendorID": "5", "refNum": "D1", "Vendor": {"name": "HLC"}},
        {"orderID": "2", "shopID": "3", "po_state": "ordered", "vendorID": "5", "refNum": "P2", "Vendor": {"name": "HLC"}},
        {"orderID": "3", "shopID": "2", "po_state": "unsent", "vendorID": "5", "refNum": "D3", "Vendor": {"name": "HLC"}},
        {"orderID": "4", "shopID": "3", "po_state": "complete", "vendorID": "5", "refNum": "C4", "Vendor": {"name": "HLC"}},
    ]
    got = reco.list_candidate_pos(orders, "3")
    ids = [o["order_id"] for o in got]
    assert ids == ["1", "2"], ids           # other store and completed excluded; drafts first
    assert got[0]["appendable"] is True      # unsent can take a new line
    assert got[1]["appendable"] is False     # already sent to the vendor — never append
    print("test_candidate_pos_labels_appendability OK")


if __name__ == "__main__":
    test_in_stock_beats_everything()
    test_insufficient_stock_does_not_count()
    test_transfer_only_between_victoria_and_langford()
    test_inbound_po_beats_draft_and_flags_stale_eta()
    test_drafts_restricted_to_store_state_and_qualifying_vendor()
    test_meets_promise_is_computed_against_the_quote()
    test_new_po_carries_vendor_and_cadence()
    test_candidate_pos_labels_appendability()
    print("\nAll PO recommendation unit tests passed.")
