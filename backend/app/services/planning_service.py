"""Versioned orchestration for weekly forecast runs.

The statistical engine is deliberately pure; this module owns BigQuery reads,
calendar completion, category pooling and forecast-to-recommendation lineage.
Run results are cached in-process and persisted to Postgres for interactive use.
BigQuery remains the analytical source of truth.
"""

import math
import threading
import uuid
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services.demand_planning import (
    ASSUMPTION_VERSION,
    DEFAULT_HORIZON_WEEKS,
    MODEL_VERSION,
    MODEL_LEGEND,
    build_purchase_recommendation,
    exposure_adjusted_seasonal_profile,
    monthly_rollups,
    week_start,
)
from app.services.planning_store import get_planning_store


# Postgres is the durable source for interactive runs. Keep only the most
# recently accessed run in process memory so repeated planning runs cannot
# accumulate hundreds of megabytes in a long-lived Render worker.
MAX_CACHED_RUNS = 1
_RUNS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_RUN_LOCK = threading.RLock()


def _cache_run(run: Dict[str, Any]) -> None:
    run_id = str(run["run_id"])
    with _RUN_LOCK:
        _RUNS[run_id] = run
        _RUNS.move_to_end(run_id)
        while len(_RUNS) > MAX_CACHED_RUNS:
            _RUNS.popitem(last=False)


def get_run_cache_info() -> Dict[str, Any]:
    """Small diagnostics surface used by tests and operational checks."""
    with _RUN_LOCK:
        return {
            "size": len(_RUNS),
            "max_size": MAX_CACHED_RUNS,
            "run_ids": list(_RUNS.keys()),
        }


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if type(value).__name__ in {"NAType", "NaTType"}:
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
    weekly_rows: Iterable[Dict[str, Any]], calendar: List[date],
    smoothing_window: int = 5, shrinkage: float = 1.0,
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
        profiles[key] = exposure_adjusted_seasonal_profile(
            profile_rows, smoothing_window=smoothing_window, shrinkage=shrinkage
        )
    return histories, profiles


def _normalized_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = config or {}
    service = float(source.get("service_quantile") or 0.90)
    if service not in {0.50, 0.80, 0.90, 0.95}:
        raise ValueError("service_quantile must be one of 0.50, 0.80, 0.90 or 0.95")
    model = str(source.get("model") or "auto").strip().lower()
    if model not in MODEL_LEGEND:
        raise ValueError(f"Unsupported forecast model: {model}")
    # Backward compatibility for runs saved before the buyer-facing setting
    # was renamed. Coverage is measured after receipt; lead time is added
    # separately to form the full protection horizon.
    order_coverage = source.get("order_coverage_weeks")
    if order_coverage in (None, ""):
        order_coverage = source.get("review_period_weeks")
    return {
        "model": model,
        "service_quantile": service,
        "history_years": max(1, min(5, int(source.get("history_years") or 3))),
        "order_coverage_weeks": max(1, min(52, int(order_coverage or 8))),
        "demand_multiplier": max(0.0, min(3.0, float(source.get("demand_multiplier") or 1.0))),
        "seasonal_smoothing_weeks": max(1, min(13, int(source.get("seasonal_smoothing_weeks") or 5))),
        "seasonal_shrinkage": max(0.0, min(10.0, float(source.get("seasonal_shrinkage") or 1.0))),
        "lead_time_days": (
            max(1, min(365, int(source["lead_time_days"])))
            if source.get("lead_time_days") not in (None, "") else None
        ),
    }


def _resolve_vendor(
    row: Dict[str, Any], brand_rules: Dict[str, Dict[str, Any]],
    vendor_directory: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw_vendor_id = row.get("vendor_id")
    vendor_id_text = "" if raw_vendor_id is None else str(raw_vendor_id).strip().lower()
    if vendor_id_text not in {"", "nan", "none", "<na>"}:
        return str(raw_vendor_id), row.get("vendor_name") or row.get("vendor"), "item-po-history"
    brand = str(row.get("brand") or "").strip().casefold()
    rule = next(
        (value for key, value in brand_rules.items() if str(key).strip().casefold() == brand), None
    )
    if rule and rule.get("preferred_vendor_id"):
        return (
            str(rule["preferred_vendor_id"]),
            rule.get("preferred_vendor_name") or row.get("vendor") or row.get("default_vendor"),
            "brand-sourcing-rule",
        )
    vendor_name = row.get("vendor_name") or row.get("vendor") or row.get("default_vendor")
    resolved = vendor_directory.get(str(vendor_name or "").strip().casefold())
    if resolved:
        return str(resolved["vendor_id"]), resolved.get("vendor_name") or vendor_name, "vendor-name-directory"
    return None, vendor_name, None


def create_planning_run(
    *,
    items: Any = None,
    weekly_history: Any = None,
    as_of_date: Optional[date] = None,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
    location_ids: Optional[Iterable[str]] = None,
    force_refresh: bool = False,
    scope_type: str = "auto_replen",
    scope_value: Optional[str] = None,
    item_ids: Optional[Iterable[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    # Release the previous large object graph before materializing new BigQuery
    # frames, histories and forecast points. It remains available in Postgres.
    clear_run_cache()
    if not 1 <= int(horizon_weeks) <= 104:
        raise ValueError("horizon_weeks must be between 1 and 104")
    as_of = as_of_date or date.today()
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    locations = {str(value) for value in (location_ids or [])}
    planning_config = _normalized_config(config)

    lead_by_vendor_location: Dict[Tuple[str, str], float] = {}
    lead_by_vendor: Dict[str, float] = {}
    vendor_directory: Dict[str, Dict[str, Any]] = {}
    brand_rules: Dict[str, Dict[str, Any]] = {}
    canonical_receipts: List[Dict[str, Any]] = []
    if items is None:
        from app.services.bigquery_sync import (
            build_lead_time_lookup, fetch_planning_items, fetch_vendor_directory_by_name,
            get_brand_sourcing_rules_map,
        )
        items = fetch_planning_items(
            scope_type=scope_type, scope_value=scope_value, item_ids=item_ids,
            location_ids=location_ids,
        )
        lead_by_vendor_location, lead_by_vendor = build_lead_time_lookup(force_refresh=force_refresh)
        vendor_directory = fetch_vendor_directory_by_name()
        brand_rules = get_brand_sourcing_rules_map()
    item_rows = _records(items)
    if locations:
        item_rows = [row for row in item_rows if str(row.get("location_id")) in locations]
    item_ids = sorted({str(row.get("item_id")) for row in item_rows if row.get("item_id") is not None})

    if weekly_history is None:
        from app.services.bigquery_sync import (
            RELIABLE_HISTORY_START, fetch_scheduled_po_receipts, fetch_weekly_item_history,
        )
        weekly_history = fetch_weekly_item_history(
            item_ids=item_ids, years=planning_config["history_years"]
        )
        canonical_receipts = fetch_scheduled_po_receipts(item_ids, location_ids=location_ids)
        reliable_start = date.fromisoformat(RELIABLE_HISTORY_START)
    else:
        reliable_start = date(as_of.year - planning_config["history_years"], 1, 1)
    weekly_rows = _records(weekly_history)
    if locations:
        weekly_rows = [row for row in weekly_rows if str(row.get("location_id")) in locations]

    last_complete_week = week_start(as_of) - timedelta(days=7)
    first_observed = min(
        (week_start(row["week_start"]) for row in weekly_rows if row.get("week_start")),
        default=last_complete_week - timedelta(days=7 * 103),
    )
    calendar = _calendar(max(week_start(reliable_start), first_observed), last_complete_week)
    histories, profiles = _history_maps(
        weekly_rows, calendar,
        smoothing_window=planning_config["seasonal_smoothing_weeks"],
        shrinkage=planning_config["seasonal_shrinkage"],
    )
    item_categories: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for history_row in weekly_rows:
        key = (str(history_row.get("item_id")), str(history_row.get("location_id")))
        for value in (history_row.get("category_path"), history_row.get("category_top_level")):
            if value and str(value) not in item_categories[key]:
                item_categories[key].append(str(value))
    run_id = str(uuid.uuid4())
    source_snapshot_at = datetime.now(timezone.utc).isoformat()
    recommendations = []
    receipts_by_item_location: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for receipt in _records(canonical_receipts):
        receipts_by_item_location[(str(receipt.get("item_id")), str(receipt.get("location_id")))].append(receipt)

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
        vendor_id, vendor_name, vendor_source = _resolve_vendor(row, brand_rules, vendor_directory)
        lead_days = int(
            planning_config.get("lead_time_days")
            or _number(row, "lead_time_days", "lead_time")
            or lead_by_vendor_location.get((str(vendor_id or ""), location_id))
            or lead_by_vendor.get(str(vendor_id or ""))
            or 14
        )
        scheduled_receipts = []
        for receipt in receipts_by_item_location.get((item_id, location_id), []):
            arrival = receipt.get("expected_arrival_at")
            reason = "lightspeed-expected-arrival"
            confidence = "confirmed-date"
            if not arrival:
                ordered = receipt.get("po_ordered_at")
                base = week_start(ordered) if ordered else week_start(as_of)
                arrival = base + timedelta(days=7 * max(1, math.ceil(lead_days / 7)))
                reason = "estimated-from-vendor-lead-time"
                confidence = "estimated"
            scheduled_receipts.append({
                "week_start": week_start(arrival).isoformat(),
                "quantity": _number(receipt, "quantity") or 0.0,
                "confidence": confidence,
                "reason": reason,
                "order_id": str(receipt.get("order_id")),
            })
        # Injected test data and legacy callers may still supply a single on-order
        # total. Production planning uses the dated canonical PO-line view above.
        if not scheduled_receipts:
            on_order = _number(row, "on_order", "snapshot_on_order") or 0.0
            if on_order > 0:
                scheduled_receipts.append({
                    "week_start": (week_start(as_of) + timedelta(days=7 * max(1, math.ceil(lead_days / 7)))).isoformat(),
                    "quantity": on_order,
                    "confidence": "estimated",
                    "reason": "snapshot-total-estimated-from-vendor-lead-time",
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
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "vendor_resolution_source": vendor_source,
        }
        recommendation = build_purchase_recommendation(
            item, history, profile, as_of, int(horizon_weeks), run_id, planning_config
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
        "scope_type": scope_type,
        "scope_value": scope_value,
        "config": planning_config,
        "recommendation_count": len(recommendations),
        "blocking_exception_count": sum(bool(row.get("blocked")) for row in recommendations),
        "recommendations": recommendations,
        "monthly_rollups": monthly_rollups(recommendations),
    }
    _cache_run(run)
    if persist:
        get_planning_store().save_planning_run(run)
    return run


def get_planning_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _RUN_LOCK:
        cached = _RUNS.get(run_id)
        if cached is not None:
            _RUNS.move_to_end(run_id)
    if cached:
        return cached
    stored = get_planning_store().get_planning_run(run_id)
    if stored:
        _cache_run(stored)
    return stored


def get_latest_planning_run() -> Optional[Dict[str, Any]]:
    stored = get_planning_store().get_latest_planning_run()
    if stored:
        _cache_run(stored)
    return stored


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
