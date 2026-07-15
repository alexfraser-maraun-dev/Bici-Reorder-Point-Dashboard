"""Transparent weekly demand planning and purchase recommendation primitives.

The initial engine intentionally favors explainable statistical models and
rolling backtests. More complex global ML models can be added as challengers
without changing the public planning contracts.
"""

import math
import statistics
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.forecasting import tsb_method


MODEL_VERSION = "weekly-planning-v1"
ASSUMPTION_VERSION = "balanced-p90-v1"
DEFAULT_HORIZON_WEEKS = 52
QUANTILES = (0.50, 0.80, 0.90, 0.95)

MODEL_LEGEND = {
    "auto": "Backtests eligible models for this SKU-location and keeps the lowest-error baseline or challenger.",
    "current_velocity": "A transparent blend of the latest 4, 8 and 13 weeks; responsive but not seasonal.",
    "seasonal_naive": "Repeats the same week from the prior year when enough history exists.",
    "hierarchical_seasonal": "Local demand level and damped trend shaped by the most trustworthy category-location seasonality.",
    "tsb": "Intermittent-demand model that separately estimates demand occurrence and positive demand size.",
    "ets_damped": "Exponentially smoothed local level and damped trend for dense products with long history.",
}
SUPPORTED_MODEL_OVERRIDES = set(MODEL_LEGEND) - {"auto"}


def _nonnegative(values: Sequence[float]) -> List[float]:
    return [max(0.0, float(value or 0.0)) for value in values]


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def week_start(value: Any) -> date:
    if isinstance(value, datetime):
        value = value.date()
    elif isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if not isinstance(value, date):
        raise ValueError("week_start requires a date, datetime, or ISO date string")
    return value - timedelta(days=value.weekday())


def exposure_adjusted_seasonal_profile(
    records: Iterable[Dict[str, Any]],
    period_field: str = "week_of_year",
    cycle_field: str = "sales_year",
    value_field: str = "units",
    exposure_field: str = "observed",
    num_periods: int = 52,
    smoothing_window: int = 5,
    shrinkage: float = 1.0,
) -> Dict[int, float]:
    """Average each seasonal period across observed cycles before normalizing.

    This prevents a month/week represented in three years from appearing 50%
    stronger than a period represented in only two. Inputs should include a
    complete zero-filled spine with ``observed=False`` outside reliable history.
    ISO week 53 is folded into period 52 for a stable 52-week operational cycle.
    """
    if num_periods <= 0:
        return {}
    samples: Dict[int, List[float]] = {period: [] for period in range(1, num_periods + 1)}
    by_cycle_period: Dict[Tuple[Any, int], float] = {}
    exposures: Dict[Tuple[Any, int], bool] = {}
    for row in records:
        if exposure_field in row and not bool(row.get(exposure_field)):
            continue
        try:
            period = int(row.get(period_field))
            cycle = row.get(cycle_field)
            value = max(0.0, float(row.get(value_field) or 0.0))
        except (TypeError, ValueError):
            continue
        if period == 53 and num_periods == 52:
            period = 52
        if cycle is None or not 1 <= period <= num_periods:
            continue
        key = (cycle, period)
        by_cycle_period[key] = by_cycle_period.get(key, 0.0) + value
        exposures[key] = True
    for (cycle, period), value in by_cycle_period.items():
        if exposures.get((cycle, period)):
            samples[period].append(value)

    period_means: Dict[int, Optional[float]] = {
        period: (sum(values) / len(values) if values else None)
        for period, values in samples.items()
    }
    observed_means = [value for value in period_means.values() if value is not None]
    if not observed_means or sum(observed_means) <= 0:
        return {period: 1.0 for period in range(1, num_periods + 1)}
    global_mean = sum(observed_means) / len(observed_means)
    raw = {
        period: ((value if value is not None else global_mean) + shrinkage * global_mean)
        / ((1.0 + shrinkage) * global_mean)
        for period, value in period_means.items()
    }

    width = max(1, int(smoothing_window))
    if width % 2 == 0:
        width += 1
    radius = width // 2
    smoothed: Dict[int, float] = {}
    for period in range(1, num_periods + 1):
        neighborhood = [
            raw[((period - 1 + offset) % num_periods) + 1]
            for offset in range(-radius, radius + 1)
        ]
        smoothed[period] = sum(neighborhood) / len(neighborhood)
    mean_index = sum(smoothed.values()) / num_periods
    return {period: value / mean_index for period, value in smoothed.items()}


def classify_demand(series: Sequence[float]) -> Dict[str, Any]:
    values = _nonnegative(series)
    positives = [value for value in values if value > 0]
    if not positives:
        return {"demand_class": "no_demand", "adi": None, "cv2": None, "lifecycle": "inactive"}
    lifecycle = "new" if len(values) < 13 else "active"
    if len(values) >= 13 and sum(values[-13:]) == 0 and sum(values[:-13]) > 0:
        lifecycle = "dormant"
    adi = len(values) / len(positives)
    mean_positive = sum(positives) / len(positives)
    cv2 = 0.0
    if len(positives) > 1 and mean_positive > 0:
        cv2 = (statistics.pstdev(positives) / mean_positive) ** 2
    if adi < 1.32 and cv2 < 0.49:
        demand_class = "smooth"
    elif adi < 1.32:
        demand_class = "erratic"
    elif cv2 < 0.49:
        demand_class = "intermittent"
    else:
        demand_class = "lumpy"
    return {
        "demand_class": demand_class,
        "adi": round(adi, 3),
        "cv2": round(cv2, 3),
        "lifecycle": lifecycle,
    }


def current_velocity_forecast(series: Sequence[float], horizon: int) -> List[float]:
    values = _nonnegative(series)
    if not values:
        return [0.0] * horizon
    windows = ((4, 0.5), (8, 0.3), (13, 0.2))
    level = sum((sum(values[-size:]) / min(size, len(values))) * weight for size, weight in windows)
    return [max(0.0, level)] * horizon


def seasonal_naive_forecast(series: Sequence[float], horizon: int, period: int = 52) -> List[float]:
    values = _nonnegative(series)
    if not values:
        return [0.0] * horizon
    fallback = sum(values[-min(13, len(values)):]) / min(13, len(values))
    result = []
    for step in range(horizon):
        index = len(values) - period + (step % period)
        result.append(values[index] if 0 <= index < len(values) else fallback)
    return result


def hierarchical_seasonal_forecast(
    series: Sequence[float], profile: Dict[int, float], horizon: int
) -> List[float]:
    values = _nonnegative(series)
    if not values:
        return [0.0] * horizon
    start_period = ((len(values) - 1) % 52) + 1
    deseasonalized = []
    for index, value in enumerate(values):
        period = (index % 52) + 1
        seasonal = max(0.05, float(profile.get(period, 1.0)))
        deseasonalized.append(value / seasonal)
    tail = deseasonalized[-min(13, len(deseasonalized)):]
    level = sum(tail) / len(tail)
    annual_growth = 1.0
    if len(deseasonalized) >= 26:
        prior = deseasonalized[-26:-13]
        recent_mean = sum(tail) / len(tail)
        prior_mean = sum(prior) / len(prior)
        if prior_mean > 0:
            annual_growth = max(0.85, min(1.30, recent_mean / prior_mean))
    weekly_growth = annual_growth ** (1.0 / 52.0)
    cumulative = 0.0
    result = []
    for step in range(1, horizon + 1):
        cumulative += 0.90 ** step
        period = ((start_period - 1 + step) % 52) + 1
        result.append(max(0.0, level * (weekly_growth ** cumulative) * profile.get(period, 1.0)))
    return result


def intermittent_tsb_forecast(series: Sequence[float], horizon: int) -> List[float]:
    value = tsb_method(_nonnegative(series), alpha=0.1, beta=0.1)
    return [max(0.0, value)] * horizon


def ets_damped_forecast(series: Sequence[float], horizon: int) -> List[float]:
    values = _nonnegative(series)
    if len(values) < 8:
        return current_velocity_forecast(values, horizon)
    best = None
    for alpha in (0.2, 0.4, 0.6, 0.8):
        for beta in (0.1, 0.3):
            level = values[0]
            trend = values[1] - values[0]
            error = 0.0
            for actual in values[1:]:
                predicted = max(0.0, level + 0.9 * trend)
                error += (actual - predicted) ** 2
                previous = level
                level = alpha * actual + (1.0 - alpha) * (level + 0.9 * trend)
                trend = beta * (level - previous) + (1.0 - beta) * 0.9 * trend
            if best is None or error < best[0]:
                best = (error, level, trend)
    _, level, trend = best
    return [max(0.0, level + (sum(0.9 ** i for i in range(1, step + 1))) * trend) for step in range(1, horizon + 1)]


def forecast_metrics(actual: Sequence[float], predicted: Sequence[float], training: Sequence[float]) -> Dict[str, float]:
    pairs = list(zip(_nonnegative(actual), _nonnegative(predicted)))
    if not pairs:
        return {"wape": 0.0, "mase": 0.0, "bias": 0.0, "quantile_loss_p90": 0.0, "inventory_cost": 0.0}
    absolute_error = sum(abs(a - p) for a, p in pairs)
    actual_total = sum(a for a, _ in pairs)
    wape = absolute_error / actual_total if actual_total > 0 else absolute_error / len(pairs)
    train = _nonnegative(training)
    naive_scale = (
        sum(abs(train[i] - train[i - 1]) for i in range(1, len(train))) / (len(train) - 1)
        if len(train) > 1 else 1.0
    )
    if naive_scale <= 0:
        naive_scale = 1.0
    mase = (absolute_error / len(pairs)) / naive_scale
    bias = sum(p - a for a, p in pairs) / (actual_total if actual_total > 0 else len(pairs))
    quantile_loss = sum(
        0.90 * (a - p) if a >= p else 0.10 * (p - a)
        for a, p in pairs
    ) / len(pairs)
    # A transparent inventory proxy: one unit short is assumed to cost four
    # times one unit of excess holding. Absolute financial simulation can replace
    # these weights when margin and holding-cost inputs are available.
    inventory_cost = sum(
        4.0 * max(a - p, 0.0) + max(p - a, 0.0)
        for a, p in pairs
    ) / len(pairs)
    return {
        "wape": round(wape, 6),
        "mase": round(mase, 6),
        "bias": round(bias, 6),
        "quantile_loss_p90": round(quantile_loss, 6),
        "inventory_cost": round(inventory_cost, 6),
    }


def rolling_backtest(
    series: Sequence[float], forecaster: Callable[[Sequence[float], int], List[float]],
    horizons: Sequence[int] = (1, 4, 8, 13, 26, 52), min_train: int = 26,
) -> Dict[str, Any]:
    values = _nonnegative(series)
    actual: List[float] = []
    predicted: List[float] = []
    residuals: List[float] = []
    cutoffs = []
    for horizon in horizons:
        cutoff = len(values) - int(horizon)
        if cutoff < min_train:
            continue
        train = values[:cutoff]
        forecast = forecaster(train, int(horizon))
        observed = values[cutoff:cutoff + int(horizon)]
        actual.extend(observed)
        predicted.extend(forecast[:len(observed)])
        residuals.extend(a - p for a, p in zip(observed, forecast))
        cutoffs.append({"cutoff": cutoff, "horizon": int(horizon)})
    metrics = forecast_metrics(actual, predicted, values[:-1] or values)
    return {"metrics": metrics, "residuals": residuals, "cutoffs": cutoffs}


def select_champion(
    series: Sequence[float], category_profile: Optional[Dict[int, float]] = None,
    forced_model: Optional[str] = None,
) -> Dict[str, Any]:
    values = _nonnegative(series)
    classification = classify_demand(values)
    profile = category_profile or {period: 1.0 for period in range(1, 53)}
    candidates: Dict[str, Callable[[Sequence[float], int], List[float]]] = {
        "current_velocity": current_velocity_forecast,
        "seasonal_naive": seasonal_naive_forecast,
        "hierarchical_seasonal": lambda history, horizon: hierarchical_seasonal_forecast(history, profile, horizon),
    }
    if classification["demand_class"] in {"intermittent", "lumpy"} or classification["lifecycle"] == "dormant":
        candidates["tsb"] = intermittent_tsb_forecast
    if len(values) >= 104 and classification["demand_class"] in {"smooth", "erratic"}:
        candidates["ets_damped"] = ets_damped_forecast

    requested_model = (forced_model or "auto").strip().lower()
    if requested_model not in MODEL_LEGEND:
        raise ValueError(f"Unsupported forecast model: {requested_model}")
    # A buyer may deliberately compare models even when the automatic eligibility
    # rules would not select them. The model implementations retain their safe
    # history-length fallbacks.
    if requested_model == "tsb":
        candidates["tsb"] = intermittent_tsb_forecast
    elif requested_model == "ets_damped":
        candidates["ets_damped"] = ets_damped_forecast

    results = {name: rolling_backtest(values, fn) for name, fn in candidates.items()}
    viable = [
        (result["metrics"]["wape"], result["metrics"]["inventory_cost"], abs(result["metrics"]["bias"]), name)
        for name, result in results.items() if result["cutoffs"]
    ]
    champion = (
        requested_model
        if requested_model != "auto"
        else (min(viable)[3] if viable else "current_velocity")
    )
    return {
        "champion": champion,
        "classification": classification,
        "candidates": results,
        "residuals": results.get(champion, {}).get("residuals", []),
        "forecaster": candidates[champion],
    }


def probabilistic_forecast(
    point_forecast: Sequence[float], residuals: Sequence[float]
) -> List[Dict[str, float]]:
    residual_values = list(residuals) or [0.0]
    result = []
    for step, point in enumerate(point_forecast, start=1):
        scale = min(2.0, math.sqrt(step))
        quantile_values = {
            probability: max(0.0, float(point) + _quantile(residual_values, probability) * scale)
            for probability in QUANTILES
        }
        ordered = []
        floor = 0.0
        for probability in QUANTILES:
            floor = max(floor, quantile_values[probability])
            ordered.append((probability, floor))
        result.append({
            "p50": round(ordered[0][1], 3),
            "p80": round(ordered[1][1], 3),
            "p90": round(ordered[2][1], 3),
            "p95": round(ordered[3][1], 3),
        })
    return result


def round_order_constraints(quantity: float, case_pack: int = 1, moq: int = 0) -> Dict[str, Any]:
    unconstrained = max(0, int(math.ceil(quantity)))
    pack = max(1, int(case_pack or 1))
    minimum = max(0, int(moq or 0))
    if unconstrained <= 0:
        rounded = 0
    else:
        rounded = max(minimum, int(math.ceil(unconstrained / pack) * pack))
    return {
        "unconstrained_quantity": unconstrained,
        "rounded_quantity": rounded,
        "constraint_extra_units": rounded - unconstrained,
        "case_pack": pack,
        "moq": minimum,
    }


def project_inventory_and_order(
    as_of_date: date,
    forecast: Sequence[Dict[str, float]],
    on_hand: float,
    scheduled_receipts: Iterable[Dict[str, Any]],
    lead_time_weeks: int,
    review_period_weeks: int = 4,
    case_pack: int = 1,
    moq: int = 0,
    service_quantile: float = 0.90,
) -> Dict[str, Any]:
    service_quantile = min(0.95, max(0.50, float(service_quantile)))
    service_key = f"p{int(round(service_quantile * 100))}"
    if service_key not in {"p50", "p80", "p90", "p95"}:
        service_key = "p90"
        service_quantile = 0.90
    receipts_by_week: Dict[date, float] = {}
    for receipt in scheduled_receipts or []:
        arrival = week_start(receipt["week_start"])
        receipts_by_week[arrival] = receipts_by_week.get(arrival, 0.0) + max(0.0, float(receipt.get("quantity") or 0.0))
    first_week = week_start(as_of_date) + timedelta(days=7)
    inventory = max(0.0, float(on_hand or 0.0))
    projection = []
    need_by = None
    for index, point in enumerate(forecast):
        current_week = first_week + timedelta(days=index * 7)
        receipt = receipts_by_week.get(current_week, 0.0)
        inventory += receipt
        start_inventory = inventory
        inventory -= float(point.get("p50") or 0.0)
        if need_by is None and inventory < float(point.get(service_key) or 0.0):
            need_by = current_week
        projection.append({
            "week_start": current_week.isoformat(),
            "starting_inventory": round(start_inventory, 3),
            "scheduled_receipts": round(receipt, 3),
            "ending_inventory": round(inventory, 3),
        })
    coverage = max(1, int(lead_time_weeks)) + max(1, int(review_period_weeks))
    target_demand = sum(float(point.get(service_key) or 0.0) for point in forecast[:coverage])
    inbound_during_lead = sum(
        quantity for receipt_week, quantity in receipts_by_week.items()
        if first_week <= receipt_week < first_week + timedelta(days=max(1, int(lead_time_weeks)) * 7)
    )
    inventory_position = max(0.0, float(on_hand or 0.0)) + inbound_during_lead
    constraints = round_order_constraints(target_demand - inventory_position, case_pack, moq)
    return {
        "need_by_week": need_by.isoformat() if need_by else None,
        "target_lead_time_demand": round(target_demand, 3),
        "target_lead_time_demand_p90": round(target_demand, 3),
        "service_quantile": service_quantile,
        "inventory_position_at_order": round(inventory_position, 3),
        "projection": projection,
        **constraints,
    }


def build_purchase_recommendation(
    item: Dict[str, Any],
    weekly_history: Sequence[float],
    category_profile: Optional[Dict[int, float]],
    as_of_date: date,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
    run_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = config or {}
    forced_model = config.get("model") or "auto"
    service_quantile = float(config.get("service_quantile") or 0.90)
    demand_multiplier = max(0.0, float(config.get("demand_multiplier") or 1.0))
    review_period_weeks = max(1, int(config.get("review_period_weeks") or item.get("review_period_weeks") or 4))
    selection = select_champion(weekly_history, category_profile, forced_model=forced_model)
    point = selection["forecaster"](weekly_history, horizon_weeks)
    point = [value * demand_multiplier for value in point]
    distribution = probabilistic_forecast(point, selection["residuals"])
    inventory = project_inventory_and_order(
        as_of_date=as_of_date,
        forecast=distribution,
        on_hand=item.get("on_hand", item.get("current_qoh", 0)),
        scheduled_receipts=item.get("scheduled_receipts") or [],
        lead_time_weeks=max(1, int(math.ceil(float(config.get("lead_time_days") or item.get("lead_time_days") or item.get("lead_time") or 14) / 7.0))),
        review_period_weeks=review_period_weeks,
        case_pack=int(item.get("case_pack") or 1),
        moq=int(item.get("moq") or 0),
        service_quantile=service_quantile,
    )
    landed_cost = item.get("landed_cost")
    selling_price = item.get("selling_price")
    rounded_qty = inventory["rounded_quantity"]
    missing_cost = landed_cost is None or float(landed_cost) <= 0
    forecast_weeks = []
    first_week = week_start(as_of_date) + timedelta(days=7)
    for index, quantiles in enumerate(distribution):
        p50 = quantiles["p50"]
        forecast_weeks.append({
            "week_start": (first_week + timedelta(days=7 * index)).isoformat(),
            **quantiles,
            "forecast_cogs": round(p50 * float(landed_cost), 2) if landed_cost is not None else None,
            "forecast_revenue": round(p50 * float(selling_price), 2) if selling_price is not None else None,
        })
    metrics = selection["candidates"].get(selection["champion"], {}).get("metrics", {})
    confidence = "high" if metrics and metrics.get("wape", 1) <= 0.25 else "medium" if metrics else "low"
    reasons = []
    if inventory["need_by_week"]:
        reasons.append("projected_service_risk")
    if inventory["constraint_extra_units"]:
        reasons.append("supplier_constraints_applied")
    if missing_cost:
        reasons.append("missing_landed_cost")
    if selection["classification"]["demand_class"] in {"intermittent", "lumpy"}:
        reasons.append("intermittent_demand")
    spend = round(rounded_qty * float(landed_cost), 2) if landed_cost is not None else None
    protected_units = inventory["target_lead_time_demand"]
    gross_margin_protected = None
    priority_score = 0.0
    if landed_cost is not None and selling_price is not None:
        gross_margin_protected = round(
            protected_units * max(0.0, float(selling_price) - float(landed_cost)), 2
        )
        if spend and spend > 0:
            urgency = 2.0 if inventory["need_by_week"] else 0.5
            confidence_penalty = {"high": 1.0, "medium": 0.8, "low": 0.5}[confidence]
            excess_penalty = 1.0 / (1.0 + max(0, inventory["constraint_extra_units"]))
            priority_score = urgency * gross_margin_protected / spend * confidence_penalty * excess_penalty
    return {
        "recommendation_id": str(uuid.uuid4()),
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "assumption_version": ASSUMPTION_VERSION,
        "item_id": str(item.get("item_id") or item.get("system_id")),
        "sku": item.get("sku"),
        "description": item.get("description"),
        "brand": item.get("brand"),
        "category": item.get("category"),
        "category_top_level": item.get("category_top_level"),
        "location_id": str(item.get("location_id")),
        "location": item.get("location"),
        "vendor_id": str(item.get("vendor_id")) if item.get("vendor_id") is not None else None,
        "vendor_name": item.get("vendor_name") or item.get("vendor"),
        "vendor_resolution_source": item.get("vendor_resolution_source"),
        "champion_model": selection["champion"],
        "requested_model": forced_model,
        "seasonal_source": "category-location" if category_profile else "flat fallback",
        "demand_classification": selection["classification"],
        "forecast_metrics": metrics,
        "confidence": confidence,
        "service_quantile": inventory["service_quantile"],
        "demand_multiplier": demand_multiplier,
        "review_period_weeks": review_period_weeks,
        "forecast": forecast_weeks,
        "inventory_projection": inventory["projection"],
        "need_by_week": inventory["need_by_week"],
        "unconstrained_quantity": inventory["unconstrained_quantity"],
        "recommended_quantity": rounded_qty,
        "constraint_extra_units": inventory["constraint_extra_units"],
        "case_pack": inventory["case_pack"],
        "moq": inventory["moq"],
        "landed_unit_cost": landed_cost,
        "currency": item.get("currency") or "CAD",
        "purchase_commitment_spend": spend,
        "expected_receipt_value": spend,
        "gross_margin_protected": gross_margin_protected,
        "priority_score": round(priority_score, 6),
        "blocked": missing_cost or not item.get("vendor_id"),
        "reason_codes": reasons,
    }


def monthly_rollups(recommendations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for recommendation in recommendations:
        category = str(recommendation.get("category") or "Uncategorized")
        for point in recommendation.get("forecast", []):
            month = point["week_start"][:7]
            bucket = buckets.setdefault((category, month), {
                "category": category, "month": month, "units": 0.0,
                "cogs": 0.0, "revenue": 0.0, "missing_cogs": False,
            })
            bucket["units"] += float(point.get("p50") or 0.0)
            if point.get("forecast_cogs") is None:
                bucket["missing_cogs"] = True
            else:
                bucket["cogs"] += float(point["forecast_cogs"])
            if point.get("forecast_revenue") is not None:
                bucket["revenue"] += float(point["forecast_revenue"])
    return [
        {**bucket, "units": round(bucket["units"], 2), "cogs": round(bucket["cogs"], 2),
         "revenue": round(bucket["revenue"], 2)}
        for _, bucket in sorted(buckets.items())
    ]
