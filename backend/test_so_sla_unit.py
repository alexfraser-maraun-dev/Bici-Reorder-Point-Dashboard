"""Offline unit tests for the special-order SLA stage log. No network, no database.

These cover the decisions that were derived from live data and would silently regress:
timestamps must come from Lightspeed rather than observation time, promise precedence must
favour the real customer quote, and a shrinking population must never be trusted.
"""

import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")
from app.services import so_stage_log  # noqa: E402

NOW = "2026-08-19T12:00:00+00:00"


def _row(**kw) -> Dict[str, Any]:
    base = {
        "special_order_id": "44690",
        "procurement_stage": "open_pool",
        "created_date": "2026-05-20",
        "po_created_date": None,
        "ordered_date": None,
        "po_received_date": None,
        "so_received_date": None,
        "shop_id": "3",
        "source": "workorder",
        "order_id": None,
        "vendor_id": None,
        "item_id": "123",
    }
    base.update(kw)
    return base


def test_each_stage_uses_its_own_lightspeed_timestamp():
    cases = [
        ("open_pool", {"created_date": "2026-05-20"}, "2026-05-20"),
        ("unordered_po", {"po_created_date": "2026-07-02"}, "2026-07-02"),
        ("ordered", {"ordered_date": "2026-07-10"}, "2026-07-10"),
        ("received", {"so_received_date": "2026-08-01"}, "2026-08-01"),
    ]
    for stage, fields, expected in cases:
        entry = so_stage_log.derive_stage_entry(_row(procurement_stage=stage, **fields), NOW)
        assert entry["entered_at"] == expected, (stage, entry)
        assert entry["entered_source"] == "derived", (stage, entry)
    print("test_each_stage_uses_its_own_lightspeed_timestamp OK")


def test_missing_timestamp_falls_back_and_is_marked_observed():
    # A PO-wide receivedDate may belong to another line in a split shipment. If the individual
    # SO has no status timestamp, ignore the PO date and fall back to the ordered date.
    entry = so_stage_log.derive_stage_entry(
        _row(procurement_stage="received", ordered_date="2026-07-10",
             po_received_date="2026-07-25", so_received_date=None), NOW
    )
    assert entry["entered_at"] == "2026-07-10", entry
    assert entry["entered_source"] == "observed", entry

    # Nothing known at all -> observation time, never a crash.
    bare = so_stage_log.derive_stage_entry(
        _row(procurement_stage="received", created_date=None, ordered_date=None,
             po_created_date=None, po_received_date=None), NOW
    )
    assert bare["entered_at"] == "2026-08-19", bare
    assert bare["entered_source"] == "observed", bare
    print("test_missing_timestamp_falls_back_and_is_marked_observed OK")


def test_rows_without_identity_are_skipped_not_crashed():
    assert so_stage_log.derive_stage_entry(_row(special_order_id=None), NOW) is None
    assert so_stage_log.derive_stage_entry(_row(procurement_stage=None), NOW) is None
    obs = so_stage_log.build_observations([_row(), _row(special_order_id=None)], NOW)
    assert len(obs) == 1, obs
    print("test_rows_without_identity_are_skipped_not_crashed OK")


def test_promise_precedence_and_implied_exclusion():
    # The Shopify metafield is the real customer quote and outranks the workorder eta-out.
    both = _row(shopify_expected_date="2026-09-01", workorder_eta_out="2026-09-20",
                shopify_order_id="9001")
    got = so_stage_log.collect_promises([both])
    assert len(got) == 1 and got[0]["promise_source"] == "shopify_metafield", got
    assert got[0]["promise_date"] == "2026-09-01", got

    # Workorder eta-out is the bike booking/service date, not a parts promise. Service parts
    # promises are written explicitly through the app-owned promise endpoint instead.
    svc = so_stage_log.collect_promises([_row(workorder_eta_out="2026-09-20")])
    assert svc == [], svc

    # No human-recorded promise anywhere -> nothing enters the ledger. Implied dates are for
    # prioritisation only; letting them in would make the on-time number self-referential.
    assert so_stage_log.collect_promises([_row()]) == []
    print("test_promise_precedence_and_implied_exclusion OK")


class _FakeStore:
    """Minimal stand-in; records calls so the guard can be asserted without a database."""

    def __init__(self, previous_population=None):
        self.meta = {"so_sweep_last_population": previous_population}
        self.stage_calls: List[int] = []
        # One entry per sweep: the sweep must write promises in a single batch, not
        # one transaction per order.
        self.promise_batches: List[int] = []

    def get_po_watch_meta(self, key):
        return self.meta.get(key)

    def set_po_watch_meta(self, key, value):
        self.meta[key] = value

    def record_so_stage_observations(self, obs):
        self.stage_calls.append(len(obs))
        return {"inserted": len(obs), "touched": 0}

    def record_so_promises(self, promises):
        self.promise_batches.append(len(list(promises)))
        return sum(self.promise_batches[-1:])


def test_population_collapse_is_treated_as_a_bad_read():
    # A truncated Lightspeed read looks exactly like a shrinking population. Writing on it
    # would strand every missing SO at a stale stage.
    store = _FakeStore(previous_population="372")
    out = so_stage_log.persist_observations([_row() for _ in range(50)], store, NOW)
    assert out["skipped"].startswith("population_drop"), out
    assert store.stage_calls == [], store.stage_calls

    # A normal-sized sweep writes.
    store2 = _FakeStore(previous_population="372")
    rows = [_row(special_order_id=str(i)) for i in range(370)]
    out2 = so_stage_log.persist_observations(rows, store2, NOW)
    assert out2["skipped"] is None, out2
    assert store2.stage_calls == [370], store2.stage_calls
    assert store2.meta["so_sweep_last_population"] == "370"
    # 370 orders, one promise write.
    assert len(store2.promise_batches) == 1, store2.promise_batches

    # An empty payload is never a real population either.
    assert so_stage_log.persist_observations([], _FakeStore(), NOW)["skipped"] == "empty_population"
    print("test_population_collapse_is_treated_as_a_bad_read OK")


def test_persistence_never_raises_into_the_dashboard():
    class Exploding(_FakeStore):
        def record_so_stage_observations(self, obs):
            raise RuntimeError("database is on fire")

    out = so_stage_log.persist_observations([_row()], Exploding(), NOW)
    assert out["skipped"].startswith("error:"), out
    print("test_persistence_never_raises_into_the_dashboard OK")


if __name__ == "__main__":
    test_each_stage_uses_its_own_lightspeed_timestamp()
    test_missing_timestamp_falls_back_and_is_marked_observed()
    test_rows_without_identity_are_skipped_not_crashed()
    test_promise_precedence_and_implied_exclusion()
    test_population_collapse_is_treated_as_a_bad_read()
    test_persistence_never_raises_into_the_dashboard()
    print("\nAll special-order SLA unit tests passed.")
