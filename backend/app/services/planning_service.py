"""Versioned orchestration for weekly forecast runs.

The statistical engine is deliberately pure; this module owns BigQuery reads,
calendar completion, category pooling and forecast-to-recommendation lineage.
Run results are cached in-process for interactive use. Persisting analytical
outputs to BigQuery can be added without changing the API contracts.
"""

import math
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.demand_planning import (
    ASSUMPTION_VERSION,
    DEFAULT_HORIZON_WEEKS,
    MODEL_VERSION,
    build_purchase_recommendation,
    exposure_adjusted_seasonal_profile,
    monthly_rollups,
    week_start,
)


_RUNS: Dict[str, Dict[str, Any]] = {}
_RUN_LOCK = threading.RLock()


def _plain(value: Any) -> Any:
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _records(frame_or_records: Any) -> List[Dict[str, Any]]:
    if frame_or_records is None:
        return []
    if hasattr(frame_or_records, "to_dict"):
        source = frame_or_records.to_dict(orient="records")
    else:
        source = list(frame_or_records)
    return [{key: _plain(value) for key, value in row.items()} for row in source]


def _calendar(start: date, end: date) -> List[date]:
    current = week_start(start)
    end = week_start(end)
    values = []
    while current <= end:
        values.append(current)
        current += timedelta(days=7)
    return values


def _number(row: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _history_maps(
    weekly_rows: Iterable[Dict[str, Any]], calendar: List[date]
) -> Tuple[Dict[Tuple[str, str], List[float]], Dict[Tuple[str, str], Dict[int, float]]]:
    demand: Dict[Tuple[str, str, date], float] = defaultdict(float)
    categories_for_item: Dict[Tuple[str, str], set] = defaultdict(set)
    for row in weekly_rows:
        key = (str(row.get("item_id")), str(row.get("location_id")))
        try:
            period = week_start(row.get("week_start"))
        except (TypeError, ValueError):
            continue
        demand[(key[0], key[1], period)] += max(
            0.0, float(row.get("adjusted_units_sold", row.get("raw_units_sold", 0)) or 0)
        )
        for category in (row.get("category_path"), row.get("category_top_level")):
            if category:
                categories_for_item[key].add(str(category))
        if not categories_for_item[key]:
            categories_for_item[key].add("Uncategorized")

    histories: Dict[Tuple[str, str], List[float]] = {}
    category_week: Dict[Tuple[str, str, date], float] = defaultdict(float)
    for key in set(categories_for_item) | {(item, location) for item, location, _ in demand}:
        history = [demand.get((key[0], key[1], period), 0.0) for period in calendar]
        histories[key] = history
        for category in categories_for_item.get(key, {"Uncategorized"}):
            for period, value in zip(calendar, history):
                category_week[(key[1], category, period)] += value

    profiles: Dict[Tuple[str, str], Dict[int, float]] = {}
    category_keys = {(location, category) for location, category, _ in category_week}
    for key in category_keys:
        profile_rows = []
        for period in calendar:
            iso = period.isocalendar()
            profile_rows.append({
                "sales_year": iso.year,
                "week_of_year": iso.week,
                "units": category_week.get((key[0], key[1], period), 0.0),
                "observed": True,
            })
        profiles[key] = exposure_adjusted_seasonal_profile(profile_rows)
    return histories, profiles


def create_planning_run(
    *,
    items: Any = None,
    weekly_history: Any = None,
    as_of_date: Optional[date] = None,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
    location_ids: Optional[Iterable[str]] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    if not 1 <= int(horizon_weeks) <= 104:
        raise ValueError("horizon_weeks must be between 1 and 104")
    as_of = as_of_date or date.today()
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    locations = {str(value) for value in (location_ids or [])}

    lead_by_vendor_location: Dict[Tuple[str, str], float] = {}
    lead_by_vendor: Dict[str, float] = {}
    if items is None:
        from app.services.bigquery_sync import build_lead_time_lookup, fetch_tagged_items_metrics
        items = fetch_tagged_items_metrics("auto-replen", force_refresh=force_refresh)
        lead_by_vendor_location, lead_by_vendor = build_lead_time_lookup(force_refresh=force_refresh)
    item_rows = _records(items)
    if locations:
        item_rows = [row for row in item_rows if str(row.get("location_id")) in locations]
    item_ids = sorted({str(row.get("item_id")) for row in item_rows if row.get("item_id") is not None})

    if weekly_history is None:
        from app.services.bigquery_sync import RELIABLE_HISTORY_START, fetch_weekly_item_history
        weekly_history = fetch_weekly_item_history(item_ids=item_ids, years=3)
        reliable_start = date.fromisoformat(RELIABLE_HISTORY_START)
    else:
        reliable_start = date(as_of.year - 3, 1, 1)
    weekly_rows = _records(weekly_history)
    if locations:
        weekly_rows = [row for row in weekly_rows if str(row.get("location_id")) in locations]

    last_complete_week = week_start(as_of) - timedelta(days=7)
    first_observed = min(
        (week_start(row["week_start"]) for row in weekly_rows if row.get("week_start")),
        default=last_complete_week - timedelta(days=7 * 103),
    )
    calendar = _calendar(max(week_start(reliable_start), first_observed), last_complete_week)
    histories, profiles = _history_maps(weekly_rows, calendar)
    item_categories: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for history_row in weekly_rows:
        key = (str(history_row.get("item_id")), str(history_row.get("location_id")))
        for value in (history_row.get("category_path"), history_row.get("category_top_level")):
            if value and str(value) not in item_categories[key]:
                item_categories[key].append(str(value))
    run_id = str(uuid.uuid4())
    source_snapshot_at = datetime.now(timezone.utc).isoformat()
    recommendations = []

    for row in item_rows:
        item_id = str(row.get("item_id"))
        location_id = str(row.get("location_id"))
        category = str(row.get("category") or "Uncategorized")
        history = histories.get((item_id, location_id), [0.0] * len(calendar))
        profile = profiles.get((location_id, category))
        if profile is None:
            profile = next(
                (profiles[(location_id, candidate)] for candidate in item_categories.get((item_id, location_id), [])
                 if (location_id, candidate) in profiles),
                None,
            )
        vendor_id = str(row.get("vendor_id")) if row.get("vendor_id") is not None else ""
        lead_days = int(
            _number(row, "lead_time_days", "lead_time")
            or lead_by_vendor_location.get((vendor_id, location_id))
            or lead_by_vendor.get(vendor_id)
            or 14
        )
        on_order = _number(row, "on_order") or 0.0
        scheduled_receipts = []
        if on_order > 0:
            scheduled_receipts.append({
                "week_start": (week_start(as_of) + timedelta(days=7 * max(1, math.ceil(lead_days / 7)))).isoformat(),
                "quantity": on_order,
                "confidence": "estimated",
                "reason": "source_po_has_no_normalized_expected_receipt_week",
            })
        item = {
            **row,
            "item_id": item_id,
            "location_id": location_id,
            "category": category,
            "on_hand": _number(row, "current_qoh", "on_hand") or 0.0,
            "scheduled_receipts": scheduled_receipts,
            "lead_time_days": lead_days,
            "landed_cost": _number(row, "landed_cost", "landed_unit_cost", "default_cost", "unit_cost"),
            "selling_price": _number(row, "selling_price", "price", "retail_price"),
            "case_pack": int(_number(row, "case_pack") or 1),
            "moq": int(_number(row, "moq") or 0),
            "vendor_name": row.get("vendor_name") or row.get("vendor"),
        }
        recommendation = build_purchase_recommendation(
            item, history, profile, as_of, int(horizon_weeks), run_id
        )
        recommendation["source_snapshot_at"] = source_snapshot_at
        recommendation["history"] = [
            {"week_start": period.isoformat(), "raw_units": value, "adjusted_units": value}
            for period, value in zip(calendar, history)
        ]
        recommendation["scheduled_receipts"] = scheduled_receipts
        recommendations.append(recommendation)

    recommendations.sort(
        key=lambda row: (
            bool(row.get("blocked")),
            -float(row.get("priority_score") or 0.0),
            row.get("need_by_week") or "9999-12-31",
        )
    )

    run = {
        "run_id": run_id,
        "status": "complete",
        "created_at": source_snapshot_at,
        "source_snapshot_at": source_snapshot_at,
        "as_of_date": as_of.isoformat(),
        "horizon_weeks": int(horizon_weeks),
        "model_version": MODEL_VERSION,
        "assumption_version": ASSUMPTION_VERSION,
        "recommendation_count": len(recommendations),
        "blocking_exception_count": sum(bool(row.get("blocked")) for row in recommendations),
        "recommendations": recommendations,
        "monthly_rollups": monthly_rollups(recommendations),
    }
    with _RUN_LOCK:
        _RUNS[run_id] = run
    return run


def get_planning_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _RUN_LOCK:
        return _RUNS.get(run_id)


def get_recommendations(
    run_id: str, *, location_id: Optional[str] = None, blocked: Optional[bool] = None
) -> List[Dict[str, Any]]:
    run = get_planning_run(run_id)
    if not run:
        raise KeyError(run_id)
    rows = run["recommendations"]
    if location_id is not None:
        rows = [row for row in rows if str(row.get("location_id")) == str(location_id)]
    if blocked is not None:
        rows = [row for row in rows if bool(row.get("blocked")) is blocked]
    return rows


def get_forecast(run_id: str, item_id: str, location_id: str) -> Optional[Dict[str, Any]]:
    for row in get_recommendations(run_id):
        if str(row.get("item_id")) == str(item_id) and str(row.get("location_id")) == str(location_id):
            return row
    return None


def get_recommendations_by_id(run_id: str, recommendation_ids: Iterable[str]) -> List[Dict[str, Any]]:
    wanted = {str(value) for value in recommendation_ids}
    return [row for row in get_recommendations(run_id) if str(row.get("recommendation_id")) in wanted]


def clear_run_cache() -> None:
    with _RUN_LOCK:
        _RUNS.clear()
