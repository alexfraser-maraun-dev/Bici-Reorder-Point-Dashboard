"""
Phase 2 forecasting enrichment helpers (ADDITIVE / OPT-IN).

This module contains pure, side-effect-free functions that can enrich the
demand forecast produced by ``replenishment_engine.process_recommendations``.

Nothing here is wired into the live replenishment path by default. The engine
exposes optional parameters (default ``None`` / ``1.0``) that, only when
provided, apply these multipliers. With the defaults, the engine produces
byte-for-byte identical results to before.

Three building blocks live here:

1. ``seasonality_indices`` -- multiplicative seasonal factors (avg ~= 1.0)
   from historical per-period sales totals.
2. ``crostons_method`` / ``tsb_method`` -- intermittent-demand smoothing for
   slow movers where simple daily-average velocity is noisy.
3. ``trend_coefficient`` -- turns the engine's three-window velocities into a
   single bounded trend multiplier (formalizes the momentum label as a number).

All functions are fully unit-tested in ``backend/test_forecasting.py``. The
BigQuery SQL that would feed (1) lives in ``bigquery_sync.fetch_monthly_sales_history``
and is *untested against live BQ* (no credentials available offline).
"""

import math
from typing import Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# A. Seasonality indices
# ---------------------------------------------------------------------------

def seasonality_indices(
    period_totals: Dict[int, float],
    num_periods: int = 12,
    min_total: float = 0.0,
    smoothing: float = 0.0,
) -> Dict[int, float]:
    """Compute multiplicative seasonality indices normalized so the mean ~= 1.0.

    Given historical sales totals keyed by period number (e.g. month 1..12, or
    ISO week 1..53), returns one index per period. An index of 1.3 means that
    period historically runs 30% above the typical period; 0.7 means 30% below.

    The result is normalized so the average index across all ``num_periods`` is
    exactly 1.0, which means multiplying a flat annual forecast by these indices
    redistributes -- but does not inflate or deflate -- total demand.

    Robustness for sparse / zero history:

    * If ``period_totals`` is empty, or every total is <= 0, or the overall mean
      is <= 0, returns a *neutral* map (every index == 1.0). Callers can treat a
      neutral result as "no seasonality signal -- do not adjust".
    * Periods with no data are treated as the global average (index 1.0 before
      normalization), so they neither pull the forecast up nor down.
    * ``smoothing`` (alpha >= 0) pulls each index toward 1.0 via a weighted
      blend ``index = (raw + alpha) / (1 + alpha)`` style shrinkage applied to
      the per-period mean before normalization. Higher smoothing => indices sit
      closer to 1.0, useful when history is thin. Default 0.0 == no shrinkage.

    Args:
        period_totals: period_number -> summed units for that period across all
            years of history. Period numbers outside ``1..num_periods`` are
            ignored.
        num_periods: number of periods in the seasonal cycle (12 for monthly,
            52/53 for weekly).
        min_total: if the grand total of all periods is <= this, return neutral.
        smoothing: shrinkage strength toward 1.0 (>= 0).

    Returns:
        Dict mapping every period number ``1..num_periods`` to its index.
    """
    if num_periods <= 0:
        return {}

    neutral = {p: 1.0 for p in range(1, num_periods + 1)}

    if not period_totals:
        return neutral
    if smoothing < 0:
        raise ValueError("smoothing must be >= 0")

    # Collect only valid, in-range, non-negative totals.
    cleaned: Dict[int, float] = {}
    for period, total in period_totals.items():
        if period is None:
            continue
        try:
            p = int(period)
        except (TypeError, ValueError):
            continue
        if p < 1 or p > num_periods:
            continue
        value = float(total) if total is not None else 0.0
        if value < 0:
            value = 0.0
        cleaned[p] = value

    grand_total = sum(cleaned.values())
    if grand_total <= min_total or grand_total <= 0:
        return neutral

    # Mean over ALL periods in the cycle (missing periods count as 0). This
    # anchors the normalization to a full cycle so a half-year of data does not
    # artificially double the indices.
    mean_per_period = grand_total / num_periods
    if mean_per_period <= 0:
        return neutral

    indices: Dict[int, float] = {}
    for p in range(1, num_periods + 1):
        observed = cleaned.get(p)
        if observed is None:
            # No history for this period: assume average (neutral) behavior.
            raw_index = 1.0
        else:
            raw_index = observed / mean_per_period
        # Shrink toward 1.0.
        if smoothing > 0:
            raw_index = (raw_index + smoothing) / (1.0 + smoothing)
        indices[p] = raw_index

    # Renormalize so the mean index == 1.0 exactly (shrinkage / missing periods
    # can nudge it off).
    mean_index = sum(indices.values()) / num_periods
    if mean_index <= 0:
        return neutral
    return {p: idx / mean_index for p, idx in indices.items()}


def seasonality_index_for_period(
    indices: Dict[int, float],
    period: int,
    default: float = 1.0,
) -> float:
    """Look up one period's index, falling back to ``default`` (neutral 1.0)."""
    if not indices:
        return default
    value = indices.get(period)
    if value is None or value <= 0:
        return default
    return float(value)


# ---------------------------------------------------------------------------
# B. Croston's method (and the TSB variant) for intermittent demand
# ---------------------------------------------------------------------------

def crostons_method(
    demand: Sequence[float],
    alpha: float = 0.1,
    fallback_to_mean: bool = True,
) -> float:
    """Classic Croston's method: smoothed demand-per-period for slow movers.

    Croston decomposes an intermittent series into (a) the size of non-zero
    demands and (b) the interval between them, exponentially smoothing each
    separately. The forecast per period is ``size_estimate / interval_estimate``.

    This is the right estimator when most periods are zero and a plain average
    is dominated by noise (e.g. one unit sold every few weeks).

    Edge cases:

    * Empty series -> 0.0.
    * All zeros -> 0.0 (no demand observed).
    * A single non-zero observation -> that value (interval defaults to 1).
    * If only one non-zero demand exists, the interval estimate stays at its
      initialization (the position of the first demand), matching classic Croston.

    Args:
        demand: per-period demand (units), oldest period first.
        alpha: smoothing constant in (0, 1]. Lower == smoother / more stable.
        fallback_to_mean: unused placeholder kept for API symmetry; classic
            Croston already degrades gracefully. Retained so callers can pass it.

    Returns:
        Estimated demand per period (float, >= 0).
    """
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")

    series = [float(d) if d is not None else 0.0 for d in demand]
    if not series:
        return 0.0
    if all(d <= 0 for d in series):
        return 0.0

    z = None  # smoothed non-zero demand size
    x = None  # smoothed interval between non-zero demands
    periods_since_last = 0
    first_demand_seen = False

    for value in series:
        periods_since_last += 1
        if value > 0:
            if not first_demand_seen:
                # Initialize on first demand.
                z = value
                x = float(periods_since_last)
                first_demand_seen = True
            else:
                z = z + alpha * (value - z)
                x = x + alpha * (periods_since_last - x)
            periods_since_last = 0

    if z is None or x is None or x <= 0:
        return 0.0
    return z / x


def tsb_method(
    demand: Sequence[float],
    alpha: float = 0.1,
    beta: float = 0.1,
) -> float:
    """Teunter-Syntetos-Babai (TSB) variant of Croston.

    TSB smooths the *probability* of demand occurring each period (updated every
    period, including zero periods) rather than the inter-demand interval. This
    avoids Croston's bias and -- crucially -- lets the estimate decay toward 0
    when an item stops selling, which classic Croston does not.

    Forecast per period = demand_probability * smoothed_demand_size.

    Edge cases mirror :func:`crostons_method`: empty -> 0.0, all zeros -> 0.0,
    single non-zero -> a sensible positive estimate.

    Args:
        demand: per-period demand (units), oldest first.
        alpha: smoothing constant for demand probability, in (0, 1].
        beta: smoothing constant for demand size, in (0, 1].

    Returns:
        Estimated demand per period (float, >= 0).
    """
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    if not 0 < beta <= 1:
        raise ValueError("beta must be in (0, 1]")

    series = [float(d) if d is not None else 0.0 for d in demand]
    if not series:
        return 0.0
    if all(d <= 0 for d in series):
        return 0.0

    # Initialize size from the first non-zero demand; probability from the
    # observed non-zero rate so the very first update is not wildly off.
    nonzero = [d for d in series if d > 0]
    z = nonzero[0]
    p = len(nonzero) / len(series)

    for value in series:
        if value > 0:
            p = p + alpha * (1.0 - p)
            z = z + beta * (value - z)
        else:
            p = p + alpha * (0.0 - p)

    estimate = p * z
    return estimate if estimate > 0 else 0.0


# ---------------------------------------------------------------------------
# C. Trend coefficient
# ---------------------------------------------------------------------------

def trend_coefficient(
    adjusted_daily_sales_14d: float,
    adjusted_daily_sales_15_30d: float,
    adjusted_daily_sales_31_60d: float,
    min_multiplier: float = 0.5,
    max_multiplier: float = 2.0,
    sensitivity: float = 1.0,
) -> float:
    """Turn the engine's three-window velocities into a bounded trend multiplier.

    Formalizes the existing momentum *label* (surging / rising / flat / cooling)
    into a single number that can nudge a forward forecast. The result is clamped
    to ``[min_multiplier, max_multiplier]`` (default 0.5x..2.0x) so a noisy
    short-window spike can never blow up the forecast.

    Logic:

    * Compares recent demand (14d) against an older baseline blended from the
      15-30d and 31-60d windows (recent windows weighted more heavily because
      they better reflect "where demand is heading").
    * A ratio of recent/baseline of 1.0 -> multiplier 1.0 (no trend).
    * ``sensitivity`` (>= 0) scales how aggressively the ratio moves the
      multiplier away from 1.0. ``sensitivity == 0`` always returns 1.0 (neutral
      / feature off). Default 1.0 passes the raw ratio through (then clamps).

    Edge cases:

    * No older baseline (both older windows 0) but recent > 0 -> max_multiplier
      (clearly accelerating from nothing).
    * No recent demand but older demand existed -> min_multiplier (cooling off).
    * No demand at all -> 1.0 (neutral; nothing to trend).
    * Negative inputs are floored to 0.

    Args:
        adjusted_daily_sales_14d: recent stockout-adjusted daily velocity.
        adjusted_daily_sales_15_30d: mid-window daily velocity.
        adjusted_daily_sales_31_60d: older-window daily velocity.
        min_multiplier: lower clamp (default 0.5).
        max_multiplier: upper clamp (default 2.0).
        sensitivity: how strongly the ratio shifts the multiplier (>= 0).

    Returns:
        A trend multiplier in ``[min_multiplier, max_multiplier]``.
    """
    if min_multiplier <= 0 or max_multiplier <= 0:
        raise ValueError("multiplier bounds must be positive")
    if min_multiplier > max_multiplier:
        raise ValueError("min_multiplier must be <= max_multiplier")
    if sensitivity < 0:
        raise ValueError("sensitivity must be >= 0")

    recent = max(0.0, float(adjusted_daily_sales_14d or 0.0))
    mid = max(0.0, float(adjusted_daily_sales_15_30d or 0.0))
    older = max(0.0, float(adjusted_daily_sales_31_60d or 0.0))

    if sensitivity == 0:
        return 1.0

    # Older baseline: weight the nearer window (15-30d) more than 31-60d.
    baseline = 0.6 * mid + 0.4 * older

    if baseline <= 0 and recent <= 0:
        return 1.0
    if baseline <= 0 and recent > 0:
        return max_multiplier
    if recent <= 0 and baseline > 0:
        return min_multiplier

    ratio = recent / baseline
    # Apply sensitivity around the neutral point of 1.0.
    adjusted = 1.0 + (ratio - 1.0) * sensitivity
    return _clamp(adjusted, min_multiplier, max_multiplier)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# D. Hierarchical category seasonality
# ---------------------------------------------------------------------------
#
# BICI's catalog has categories that peak in opposite seasons (trainers Oct-Feb,
# nutrition May-Sep). A single SKU rarely has enough clean history for a stable
# per-SKU seasonal shape, so we build the seasonal *shape* at the category level
# (where aggregation smooths the noise) and let each SKU borrow it -- the
# industry "seasonality groups" pattern.
#
# The category tree is hierarchical (top-level -> ... -> leaf). We build a
# profile at EVERY level so a SKU can use its most specific subcategory when that
# has signal, and fall back up the tree otherwise. We then blend the SKU's own
# profile with the resolved category profile, weighted by how much history the
# SKU itself has: w = clamp(months / full_weight_periods, 0, 1).


def build_seasonal_profiles(
    records,
    level_fields,
    period_field: str = "month_of_year",
    value_field: str = "total_units_sold",
    num_periods: int = 12,
    min_group_total: float = 0.0,
    smoothing: float = 0.0,
) -> Dict[str, Dict[object, Dict[int, float]]]:
    """Build a seasonal index map for each category value at each category level.

    Args:
        records: iterable of dict-like rows. Each row must expose ``period_field``
            (e.g. 1..12), ``value_field`` (units), and each field in
            ``level_fields`` (the category value at that level).
        level_fields: the category level columns to build profiles for, e.g.
            ``("category_path", "category_level_3", "category_level_2",
            "category_top_level")``. Order does not matter here; it matters in
            :func:`resolve_category_profile`.
        period_field: row key holding the seasonal period number.
        value_field: row key holding the units for that row.
        num_periods: periods per cycle (12 monthly).
        min_group_total: a category group whose summed units are <= this is
            omitted (too little signal to trust); callers then fall back up the
            tree. Default 0.0 keeps every non-empty group.
        smoothing: shrinkage toward 1.0 passed through to
            :func:`seasonality_indices`.

    Returns:
        ``{level_field: {category_value: {period: index}}}``.
    """
    accum: Dict[str, Dict[object, Dict[int, float]]] = {lf: {} for lf in level_fields}
    for row in records:
        period = row.get(period_field)
        if period is None:
            continue
        try:
            p = int(period)
        except (TypeError, ValueError):
            continue
        try:
            value = float(row.get(value_field) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        for lf in level_fields:
            cat = row.get(lf)
            if cat is None or cat == "":
                continue
            grp = accum[lf].setdefault(cat, {})
            grp[p] = grp.get(p, 0.0) + value

    profiles: Dict[str, Dict[object, Dict[int, float]]] = {lf: {} for lf in level_fields}
    for lf in level_fields:
        for cat, period_totals in accum[lf].items():
            if sum(period_totals.values()) <= min_group_total:
                continue
            profiles[lf][cat] = seasonality_indices(
                period_totals, num_periods=num_periods, smoothing=smoothing
            )
    return profiles


def resolve_category_profile(
    category_values: Dict[str, object],
    profiles: Dict[str, Dict[object, Dict[int, float]]],
    level_priority: Sequence[str],
):
    """Return the most specific available category profile for a SKU.

    Walks ``level_priority`` from most specific to least specific and returns the
    first ``(level_field, indices)`` whose category value has a built profile, so
    a SKU uses its leaf subcategory when that has signal and falls back up the
    tree otherwise.

    Returns ``(None, None)`` if no level has a profile.
    """
    for lf in level_priority:
        cat = category_values.get(lf)
        if cat is None or cat == "":
            continue
        prof = profiles.get(lf, {}).get(cat)
        if prof:
            return lf, prof
    return None, None


def blend_seasonal_indices(
    own_indices: Optional[Dict[int, float]],
    category_indices: Optional[Dict[int, float]],
    own_history_periods: float,
    full_weight_periods: float = 24.0,
    num_periods: int = 12,
) -> Dict[int, float]:
    """Blend a SKU's own seasonal indices with its category's, weighted by history.

    ``w = clamp(own_history_periods / full_weight_periods, 0, 1)``. With no own
    history (w=0) the category profile is used; with >= ``full_weight_periods``
    months of own history the SKU's own profile dominates. The blended result is
    renormalized so its mean index is ~= 1.0 (a pure redistribution of demand).

    If only one source is available it is returned as-is; if neither, a neutral
    all-1.0 map is returned.
    """
    if full_weight_periods <= 0:
        raise ValueError("full_weight_periods must be > 0")

    neutral = {p: 1.0 for p in range(1, num_periods + 1)}
    has_own = bool(own_indices)
    has_cat = bool(category_indices)
    if not has_own and not has_cat:
        return neutral
    if not has_cat:
        return dict(own_indices)
    if not has_own:
        return dict(category_indices)

    w = _clamp(float(own_history_periods) / full_weight_periods, 0.0, 1.0)
    blended: Dict[int, float] = {}
    for p in range(1, num_periods + 1):
        own_v = own_indices.get(p, 1.0)
        cat_v = category_indices.get(p, 1.0)
        blended[p] = w * own_v + (1.0 - w) * cat_v

    mean_index = sum(blended.values()) / num_periods
    if mean_index <= 0:
        return neutral
    return {p: v / mean_index for p, v in blended.items()}


def seasonal_profile_for_item(
    own_period_totals: Optional[Dict[int, float]],
    own_history_periods: float,
    category_values: Dict[str, object],
    profiles: Dict[str, Dict[object, Dict[int, float]]],
    level_priority: Sequence[str],
    num_periods: int = 12,
    full_weight_periods: float = 24.0,
    smoothing: float = 0.0,
) -> Dict[int, float]:
    """Compose the full per-item seasonal map: own indices blended with the
    most-specific available category profile, weighted by the item's history.

    Convenience wrapper over :func:`seasonality_indices`,
    :func:`resolve_category_profile`, and :func:`blend_seasonal_indices`.
    Returns a neutral all-1.0 map when there is no usable signal anywhere.
    """
    own = (
        seasonality_indices(own_period_totals, num_periods=num_periods, smoothing=smoothing)
        if own_period_totals
        else {}
    )
    _, category_indices = resolve_category_profile(category_values, profiles, level_priority)
    return blend_seasonal_indices(
        own,
        category_indices or {},
        own_history_periods,
        full_weight_periods=full_weight_periods,
        num_periods=num_periods,
    )


# ---------------------------------------------------------------------------
# Combined enrichment helper (used by the engine integration stub)
# ---------------------------------------------------------------------------

def apply_forecast_enrichment(
    base_daily_sales: float,
    seasonality_index: Optional[float] = None,
    trend_multiplier: Optional[float] = None,
    croston_daily: Optional[float] = None,
    croston_blend: float = 0.0,
) -> float:
    """Apply opt-in enrichment factors to a base daily-sales velocity.

    Every factor is optional and defaults to a no-op, so calling this with only
    ``base_daily_sales`` returns ``base_daily_sales`` unchanged.

    Order of operations:
        1. Optionally blend in a Croston intermittent-demand estimate
           (``croston_blend`` in [0, 1]; 0 == ignore Croston entirely).
        2. Multiply by the trend multiplier (default 1.0).
        3. Multiply by the seasonality index (default 1.0).

    Args:
        base_daily_sales: the engine's existing weighted base velocity.
        seasonality_index: multiplicative seasonal factor (None -> 1.0).
        trend_multiplier: bounded trend factor (None -> 1.0).
        croston_daily: Croston/TSB estimate of daily demand (None -> ignored).
        croston_blend: weight given to the Croston estimate vs. base velocity,
            in [0, 1]. 0.0 (default) means base velocity is used as-is.

    Returns:
        The enriched daily-sales velocity (float, >= 0).
    """
    if not 0.0 <= croston_blend <= 1.0:
        raise ValueError("croston_blend must be in [0, 1]")

    velocity = max(0.0, float(base_daily_sales or 0.0))

    if croston_daily is not None and croston_blend > 0:
        velocity = (1.0 - croston_blend) * velocity + croston_blend * max(0.0, float(croston_daily))

    if trend_multiplier is not None:
        velocity *= max(0.0, float(trend_multiplier))

    if seasonality_index is not None:
        velocity *= max(0.0, float(seasonality_index))

    return velocity


# ---------------------------------------------------------------------------
# API response builders (shape pure service output for the frontend charts)
# ---------------------------------------------------------------------------

# Calendar-month labels for monthly (num_periods == 12) seasonal profiles.
MONTH_ABBREVIATIONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build_seasonal_profile_response(
    records,
    level_fields: Sequence[str] = ("category_top_level", "category_level_2"),
    period_field: str = "month_of_year",
    value_field: str = "total_units_sold",
    num_periods: int = 12,
    min_group_total: float = 0.0,
    smoothing: float = 0.0,
) -> List[Dict[str, object]]:
    """Assemble seasonal-profile chart data for ``/api/forecast/seasonal-profiles``.

    Builds one multiplicative seasonal profile per category value at each level in
    ``level_fields`` (reusing :func:`build_seasonal_profiles`) and shapes each for the
    frontend chart + paired table: the category label, the level it was computed at,
    its ``1..num_periods`` index map, and the total units backing it (so the UI can
    rank categories by volume and signal how trustworthy each curve is).

    Pure / side-effect-free: the endpoint feeds it rows from
    ``bigquery_sync.fetch_monthly_sales_history``; tests feed it fixtures. Results are
    sorted by ``sample_units`` descending so the highest-signal categories surface
    first.
    """
    records = list(records)
    profiles = build_seasonal_profiles(
        records,
        level_fields,
        period_field=period_field,
        value_field=value_field,
        num_periods=num_periods,
        min_group_total=min_group_total,
        smoothing=smoothing,
    )

    # Total units backing each (level, category), used for ranking + confidence.
    totals: Dict[str, Dict[object, float]] = {lf: {} for lf in level_fields}
    for row in records:
        try:
            value = float(row.get(value_field) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        for lf in level_fields:
            cat = row.get(lf)
            if cat is None or cat == "":
                continue
            totals[lf][cat] = totals[lf].get(cat, 0.0) + value

    result: List[Dict[str, object]] = []
    for lf in level_fields:
        for cat, indices in profiles.get(lf, {}).items():
            result.append({
                "category_label": str(cat),
                "level": lf,
                "indices": {int(p): round(float(v), 4) for p, v in sorted(indices.items())},
                "sample_units": round(totals[lf].get(cat, 0.0), 2),
            })

    result.sort(key=lambda entry: entry["sample_units"], reverse=True)
    return result


def lead_time_window_months(
    reference_month: int,
    lead_time_days: float = 14.0,
    coverage_days: float = 30.0,
    num_periods: int = 12,
) -> Dict[str, int]:
    """The months a PO placed in ``reference_month`` would be covering.

    A PO arrives after the lead time, then covers demand for ``coverage_days``.
    The chart shades this window so the buyer sees they must buy *ahead of* a
    ramp (the research's "Aug PO for the Oct trainers peak" point). Months are
    1-based and wrap around the year.

    The window is expressed on the forward forecast timeline, which begins the
    month *after* ``reference_month``. The start offset is therefore floored at 1
    so a short (sub-month) lead time still lands on the first forecast month
    rather than the reference month itself (which is 12 months out in the wrapped
    forecast sequence).
    """
    start_offset = max(1, round(float(lead_time_days) / 30.0))
    end_offset = start_offset + max(1, round(float(coverage_days) / 30.0))
    start_month = ((reference_month - 1 + start_offset) % num_periods) + 1
    end_month = ((reference_month - 1 + end_offset) % num_periods) + 1
    return {"start_month": start_month, "end_month": end_month}


def _deseasonalize_levels(
    monthly_level_series,
    indices: Optional[Dict[int, float]],
    num_periods: int = 12,
) -> List[float]:
    """Divide each month's units by its seasonal index -> a trend-only level series.

    Accepts rows as dicts ({"month", "units"}) or (year, month, units) tuples,
    oldest -> newest. With indices that average ~1.0, dividing removes the
    seasonal component so a trend can be fit on the level (the standard
    deseasonalize -> trend -> reseasonalize recipe).
    """
    levels: List[float] = []
    for entry in monthly_level_series or []:
        if isinstance(entry, dict):
            month = int(entry.get("month"))
            units = float(entry.get("units") or 0.0)
        else:
            month = int(entry[1])
            units = float(entry[2] or 0.0)
        idx = (indices.get(month, 1.0) if indices else 1.0)
        if idx is None or idx <= 0:
            idx = 1.0
        levels.append(units / idx)
    return levels


def _loglinear_annual_growth(levels: List[float]) -> float:
    """Annualized growth ratio from an OLS fit of log(level) on month index."""
    pts = [(i, v) for i, v in enumerate(levels) if v and v > 0]
    if len(pts) < 2:
        return 1.0
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(math.log(p[1]) for p in pts) / n
    denom = sum((p[0] - mx) ** 2 for p in pts)
    if denom <= 0:
        return 1.0
    beta = sum((p[0] - mx) * (math.log(p[1]) - my) for p in pts) / denom
    return math.exp(beta * 12.0)


def _trend_factor(
    monthly_level_series,
    indices: Optional[Dict[int, float]],
    months_observed: int,
    growth_cap=(0.85, 1.30),
    full_weight_periods: float = 24.0,
    num_periods: int = 12,
):
    """Estimate (anchor level L0, monthly growth factor r) from history.

    Anchors on the trailing deseasonalized run-rate (not the long-run average)
    and estimates one annual growth ratio -- year-over-year when there are >= 18
    months, else a log-linear slope. The ratio is clamped to ``growth_cap`` and
    shrunk toward 1.0 by how much clean history exists (``months_observed /
    full_weight_periods``), then converted to a monthly factor. Returns
    ``(None, None)`` when there is too little signal, so the caller falls back to
    the flat baseline.
    """
    levels = _deseasonalize_levels(monthly_level_series, indices, num_periods)
    if len(levels) < 3:
        return None, None

    tail = levels[-3:]
    L0 = sum(tail) / len(tail)
    if L0 <= 0:
        return None, None

    n = len(levels)
    if n >= 18:
        recent = levels[-12:]
        prior = levels[-24:-12] if n >= 24 else levels[: n - 12]
        recent_mean = sum(recent) / len(recent)
        prior_mean = (sum(prior) / len(prior)) if prior else recent_mean
        g = (recent_mean / prior_mean) if prior_mean > 0 else 1.0
    else:
        g = _loglinear_annual_growth(levels)

    lo, hi = growth_cap
    g = max(lo, min(hi, g))

    w = max(0.0, min(1.0, months_observed / full_weight_periods)) if full_weight_periods > 0 else 1.0
    g_eff = 1.0 + (g - 1.0) * w
    r = g_eff ** (1.0 / 12.0)
    return L0, r


def project_monthly_forecast(
    period_totals: Dict[int, float],
    months_observed: int,
    indices: Dict[int, float],
    reference_month: int,
    horizon_months: int = 12,
    num_periods: int = 12,
    monthly_level_series=None,
    growth_cap=(0.85, 1.30),
    damping: float = 0.90,
    full_weight_periods: float = 24.0,
) -> List[Dict[str, float]]:
    """Project a forward monthly forecast: (trended) baseline × seasonal indices.

    Without ``monthly_level_series`` this is a flat-baseline model -- average
    monthly demand (total observed units / observed month-buckets) scaled by each
    month's seasonal index -- producing output identical to the original level-only
    behavior.

    When ``monthly_level_series`` is supplied (the deseasonalizable monthly history,
    oldest -> newest), the baseline is instead anchored on the recent run-rate and a
    capped, history-shrunk, **damped** growth factor is applied so a growing category
    forecasts upward without over-extrapolating on thin history. ``damping`` (phi,
    0<phi<=1) flattens growth over the horizon: each step's cumulative growth exponent
    is ``phi + phi^2 + ... + phi^step``.

    Produces ``horizon_months`` points starting at the month *after*
    ``reference_month``. Pure / side-effect-free.
    """
    total = sum(v for v in period_totals.values() if v and v > 0)
    base_monthly = (total / months_observed) if months_observed and months_observed > 0 else 0.0

    L0, r = _trend_factor(
        monthly_level_series, indices, months_observed, growth_cap, full_weight_periods, num_periods
    )
    if L0 is None:
        L0, r = base_monthly, 1.0

    forecast: List[Dict[str, float]] = []
    cumulative = 0.0
    for step in range(1, horizon_months + 1):
        cumulative += damping ** step
        moy = ((reference_month - 1 + step) % num_periods) + 1
        idx = indices.get(moy, 1.0)
        if idx is None or idx <= 0:
            idx = 1.0
        units = L0 * (r ** cumulative) * idx
        forecast.append({
            "month": moy,
            "units": round(units, 2),
            "seasonal_index": round(float(idx), 4),
        })
    return forecast


DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def project_weeks_of_cover(
    on_hand: float,
    on_order: float,
    daily_velocity: float,
    indices: Optional[Dict[int, float]],
    reference_month: int,
    horizon_months: int = 12,
    num_periods: int = 12,
    critical_weeks: float = 2.0,
    low_weeks: float = 4.0,
) -> List[Dict[str, object]]:
    """Project forward weeks-of-cover per month for one SKU at one location.

    Starting inventory is ``on_hand + on_order``. Each forward month consumes
    ``daily_velocity`` scaled by that month's seasonal index, and weeks-of-cover
    is the inventory at the *start* of the month divided by that month's weekly
    demand rate. This surfaces *future* stockouts (e.g. a seasonal ramp draining
    stock three months out) before they happen -- the heatmap's whole point.

    ``stockout_risk`` buckets the cover for color-coding. An item with no velocity
    is always "healthy" (it cannot stock out). Pure / side-effect-free.
    """
    inventory = max(0.0, float(on_hand or 0.0)) + max(0.0, float(on_order or 0.0))
    velocity = max(0.0, float(daily_velocity or 0.0))

    result: List[Dict[str, object]] = []
    for step in range(1, horizon_months + 1):
        moy = ((reference_month - 1 + step) % num_periods) + 1
        idx = (indices.get(moy, 1.0) if indices else 1.0)
        if idx is None or idx <= 0:
            idx = 1.0
        days = DAYS_IN_MONTH[(moy - 1) % 12]
        weekly_demand = velocity * 7.0 * idx
        monthly_demand = velocity * days * idx

        if weekly_demand <= 0:
            weeks = 52.0 if inventory > 0 else 0.0
        else:
            weeks = min(52.0, round(inventory / weekly_demand, 1))

        risk = "healthy"
        if velocity > 0:
            if weeks < critical_weeks:
                risk = "critical"
            elif weeks < low_weeks:
                risk = "low"

        result.append({"month": moy, "weeks": weeks, "stockout_risk": risk})
        inventory = max(0.0, inventory - monthly_demand)

    return result
