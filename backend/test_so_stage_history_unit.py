"""Offline unit tests for the pipeline dwell trend (so_stage_log.build_stage_history).

`so_stage_events` never writes `left_at`, so every exit is reconstructed. These pin the rules
that decide whether an order is still in a stage on a given day -- get one wrong and the
sparkline silently drifts.
"""

import sys
from datetime import date
from typing import Any, Dict, List

sys.path.insert(0, ".")
from app.services import so_stage_log  # noqa: E402

TODAY = date(2026, 10, 1)
LATEST = "2026-10-01T18:00:00+00:00"   # the most recent sweep
EARLIER = "2026-09-20T18:00:00+00:00"  # a sweep that stopped seeing the row


def _ev(so_id: str, stage: str, entered: str, last_seen: str = LATEST,
        first_seen: str = "2026-08-20T18:00:00+00:00", **kw) -> Dict[str, Any]:
    return {"special_order_id": so_id, "stage": stage, "entered_at": entered,
            "first_seen_at": first_seen, "last_seen_at": last_seen,
            "shop_id": kw.get("shop_id", "1"), "source": kw.get("source", "neither")}


def _rows(payload) -> List[Dict[str, Any]]:
    cols = payload["columns"]
    out = []
    for row in payload["rows"]:
        r = dict(zip(cols, row))
        r["stage"] = payload["stages"][r["stage"]]
        out.append(r)
    return out


def test_open_row_has_no_exit():
    rows = _rows(so_stage_log.build_stage_history([_ev("1", "ordered", "2026-09-10")], TODAY))
    assert rows == [{"stage": "ordered", "entered": -21, "left": None, "shop_id": "1",
                     "source": "neither", "created": -21}]


def test_exit_comes_from_next_stage_entry_not_last_seen():
    events = [
        _ev("1", "open_pool", "2026-09-01", last_seen="2026-09-25T18:00:00+00:00"),
        _ev("1", "ordered", "2026-09-05"),
    ]
    rows = {r["stage"]: r for r in _rows(so_stage_log.build_stage_history(events, TODAY))}
    assert rows["open_pool"]["left"] == -26  # 2026-09-05, the authoritative LS date
    assert rows["ordered"]["left"] is None
    assert rows["ordered"]["created"] == rows["open_pool"]["created"] == -30


def test_vanished_order_exits_on_last_seen_local_day():
    # 2026-09-21T03:00Z is still the 20th in Vancouver.
    from zoneinfo import ZoneInfo
    events = [_ev("1", "received", "2026-09-15", last_seen="2026-09-21T03:00:00+00:00"),
              _ev("2", "ordered", "2026-09-28")]
    rows = _rows(so_stage_log.build_stage_history(events, TODAY, tz=ZoneInfo("America/Vancouver")))
    assert rows[0]["left"] == -11


def test_rows_that_left_before_the_window_are_dropped():
    events = [_ev("1", "open_pool", "2026-06-01", last_seen="2026-07-01T18:00:00+00:00"),
              _ev("2", "open_pool", "2026-09-01")]
    payload = so_stage_log.build_stage_history(events, TODAY, days=60)
    assert [r["created"] for r in _rows(payload)] == [-30]
    assert payload["start"] == "2026-08-02"


def test_shopify_openness_is_judged_against_shopify_sweeps():
    # Shopify stopped refreshing on the 20th; its rows must stay open, not mass-close.
    events = [_ev("shopify:9", "shopify", "2026-09-18", last_seen=EARLIER),
              _ev("1", "ordered", "2026-09-10")]
    rows = {r["stage"]: r for r in _rows(so_stage_log.build_stage_history(events, TODAY))}
    assert rows["shopify"]["left"] is None


def test_tracking_since_is_per_stage():
    events = [_ev("1", "ordered", "2026-07-01"),
              _ev("shopify:9", "shopify", "2026-09-18", first_seen="2026-10-01T16:00:00+00:00")]
    payload = so_stage_log.build_stage_history(events, TODAY)
    assert payload["tracking_since"] == {"ordered": "2026-08-20", "shopify": "2026-10-01"}


def test_same_day_exit_still_counts_one_day():
    events = [_ev("1", "unordered_po", "2026-09-20", last_seen=EARLIER),
              _ev("2", "ordered", "2026-09-28")]
    rows = {r["stage"]: r for r in _rows(so_stage_log.build_stage_history(events, TODAY))}
    assert (rows["unordered_po"]["entered"], rows["unordered_po"]["left"]) == (-11, -10)


def test_shopify_observations_are_prefixed_and_skip_undated():
    obs = so_stage_log.build_shopify_observations([
        {"order_id": "123", "created_at": "2026-09-30T10:00:00-07:00"},
        {"order_id": "456", "created_at": None},
    ])
    assert obs == [{"special_order_id": "shopify:123", "stage": "shopify", "entered_at": "2026-09-30",
                    "entered_source": "derived", "shop_id": None, "source": "shopify",
                    "order_id": None, "vendor_id": None, "item_id": None}]


class _Store:
    def __init__(self):
        self.recorded: List[Dict[str, Any]] = []
        self.meta: Dict[str, str] = {}

    def get_po_watch_meta(self, key):
        return self.meta.get(key)

    def set_po_watch_meta(self, key, value):
        self.meta[key] = value

    def record_so_stage_observations(self, observations):
        self.recorded.extend(observations)
        return {"inserted": len(observations), "touched": 0}

    def record_so_promises(self, promises):
        return 0


def test_persist_observations_writes_shopify_intake_in_the_same_batch():
    store = _Store()
    order = {"special_order_id": "1", "procurement_stage": "open_pool", "created_date": "2026-09-01"}
    so_stage_log.persist_observations([order], store, LATEST,
                                      shopify_only=[{"order_id": "9", "created_at": "2026-09-30"}])
    assert sorted(o["stage"] for o in store.recorded) == ["open_pool", "shopify"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
    print("\nAll stage-history unit tests passed.")
