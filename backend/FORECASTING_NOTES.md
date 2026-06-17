# Phase 2 Forecasting Enrichment — Notes

Preparatory, **additive / opt-in** groundwork for Phase 2 of the demand-planning
roadmap. Nothing here changes the live replenishment path by default:
`process_recommendations` produces byte-for-byte identical output unless you pass
one of the new optional parameters.

## What was built

### `app/services/forecasting.py` (new, pure Python, fully unit-tested)

| Function | Purpose |
| --- | --- |
| `seasonality_indices(period_totals, num_periods=12, min_total=0, smoothing=0)` | Multiplicative seasonal factors normalized so the mean index ≈ 1.0. Works for months (12) or ISO weeks (52/53). Sparse/zero history returns a neutral all-1.0 map. Optional shrinkage toward 1.0 for thin history. |
| `seasonality_index_for_period(indices, period, default=1.0)` | Safe lookup of one period's index; missing/zero → neutral 1.0. |
| `crostons_method(demand, alpha=0.1)` | Classic Croston intermittent-demand estimate (smoothed size ÷ smoothed interval). For slow movers where daily-average velocity is noisy. |
| `tsb_method(demand, alpha=0.1, beta=0.1)` | Teunter-Syntetos-Babai variant. Smooths demand *probability* every period, so the estimate decays toward 0 when an item stops selling (classic Croston does not). |
| `trend_coefficient(v14, v15_30, v31_60, min=0.5, max=2.0, sensitivity=1.0)` | Converts the engine's three existing window velocities into a single bounded trend multiplier (default clamp 0.5x–2.0x). Formalizes the momentum *label* as a *number*. |
| `apply_forecast_enrichment(base, seasonality_index=None, trend_multiplier=None, croston_daily=None, croston_blend=0.0)` | Combines the factors onto a base velocity. With defaults it returns `base` unchanged (no-op). |

### Hierarchical category seasonality (added 2026-06-16, pure Python, unit-tested)

BICI's catalog has categories that peak in opposite seasons (trainers Oct–Feb,
nutrition May–Sep). Per-SKU history is usually too thin for a stable shape, so we
build the seasonal shape at the **category level** and let each SKU borrow it
(the "seasonality groups" pattern), blending in the SKU's own shape as it earns
enough history.

| Function | Purpose |
| --- | --- |
| `build_seasonal_profiles(records, level_fields, ...)` | Builds a seasonal index map for every category value at every level (e.g. `category_path`, `category_level_2`, `category_top_level`). Groups below `min_group_total` are omitted (too little signal). |
| `resolve_category_profile(category_values, profiles, level_priority)` | Returns the most specific available profile for a SKU, falling back up the tree (leaf → … → top-level). |
| `blend_seasonal_indices(own, category, own_history_periods, full_weight_periods=24)` | Blends SKU-own and category indices with `w = clamp(months/24, 0, 1)`, renormalized to mean ≈ 1.0. |
| `seasonal_profile_for_item(...)` | Convenience: composes the three above into a SKU's final monthly index map. |

### `app/services/bigquery_sync.py` — `fetch_monthly_sales_history(years=3)` (new)

Aggregates multi-year **monthly** sales from the canonical `sales_master_view`
(LS-API-backed, favoured over raw Fivetran tables). Counts special-order /
layaway / workorder demand per BICI's demand definition; excludes only warranty
workorder lines. Returns `(item_id, location_id, category_top_level,
category_level_2/3/4, category_path, brand_name, sales_month, month_of_year,
sales_year, total_units_sold)` — the category columns feed `build_seasonal_profiles`.

**Validated against live BigQuery (2026-06-16):** runs (~36 MB scan), and the
opposite-season pattern is confirmed in real data — Trainers peak M10 (index
~2.4x, June trough ~0.32x); Apparel/Parts peak M05.

### `app/services/replenishment_engine.py` — opt-in integration

Three new optional params on `process_recommendations`, all defaulting to OFF/neutral:

- `apply_trend: bool = False` — when True, multiplies each row's forward velocity by `trend_coefficient(...)`.
- `seasonality_indices_by_period: Dict[int, float] = None` — month→index map.
- `forecast_month: int = None` — which period to look up; only used with the map.

Enrichment is applied to the **forward-looking** velocity (`adjusted_daily_sales`)
only; the reported raw `daily_sales` is unchanged. Two new additive output fields,
`trend_multiplier` and `seasonality_index`, are `None` when the feature is off.

## How to enable each feature

```python
# Trend only:
process_recommendations(items, lead_times, apply_trend=True)

# Seasonality only (e.g. forecasting into July):
from app.services.forecasting import seasonality_indices
from app.services.bigquery_sync import fetch_monthly_sales_history
df = fetch_monthly_sales_history(years=3)                       # untested vs live BQ
totals = df.groupby("month_of_year")["total_units_sold"].sum().to_dict()
idx = seasonality_indices(totals)                              # mean ≈ 1.0
process_recommendations(items, lead_times,
                        seasonality_indices_by_period=idx, forecast_month=7)

# Croston / TSB for a slow mover (standalone, not yet wired into the engine):
from app.services.forecasting import crostons_method
daily = crostons_method([0,0,0,2,0,0,0,2])   # -> 0.5 units/period
```

Note: per-SKU seasonality (a different index map per item) and Croston blending
into the engine row loop are intentionally **not** wired in yet — `forecast_month`
+ one shared index map is the minimal safe hook. Per-SKU wiring is a follow-up.

## Tested vs. untested

- **Unit-tested** (`backend/test_forecasting.py`, 54 cases, all passing via
  `backend/venv/bin/python -m unittest test_forecasting`): seasonality
  normalization, sparse/zero/negative/smoothing edge cases, weekly cycle;
  Croston + TSB including empty / all-zeros / single-point / intermittent;
  trend coefficient clamps and edge cases; `apply_forecast_enrichment` no-op and
  blends; hierarchical profiles (opposite-peak categories, min-group filter,
  most-specific-then-fallback resolution, blend weighting + renormalization).
- **Validated against live BigQuery**: `fetch_monthly_sales_history` runs, and an
  end-to-end build → resolve → blend on real data produces sane category shapes
  (a new Trainers SKU with no history borrows the Oct-peak winter profile).
- **Regression-checked**: `test_inventory_status` still passes; `app.main` still
  imports; default `process_recommendations` output verified unchanged.

## Left for the user / next steps

1. **Backtest** seasonality/trend against held-out history (WMAPE/MASE vs. a
   seasonal-naive baseline) before enabling by default — per the plan's Phase 2 gate.
2. **Wire hierarchical seasonality into the engine row loop** — currently the
   engine accepts a single shared index map + `forecast_month`; the next step is
   per-SKU resolution via `seasonal_profile_for_item` using each row's category,
   and applying the index across the lead-time window (not just the order month).
3. **Croston blending** into the engine for flagged slow movers.
4. **ShopifyQL / Klaviyo behavioral signals** — skipped here (needs external setup).
5. **Open-to-Buy (OTB) budgeting** view — not started (Phase 2 item 5).
