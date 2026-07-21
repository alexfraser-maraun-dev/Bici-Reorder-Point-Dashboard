from dotenv import load_dotenv
load_dotenv()

import os
import time
import hmac
import threading
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from app.services.bigquery_sync import (
    log_recommendation_run, log_velocity_snapshots, log_writeback,
    get_managed_skus, upsert_managed_skus, get_sku_overrides, upsert_sku_override,
    get_recommendation_runs, get_writeback_logs as fetch_writeback_logs
)
import pandas as pd
import io
import uuid
from datetime import datetime
from typing import List, Dict, Any
from fastapi.responses import Response, RedirectResponse, JSONResponse

def build_lightspeed_item_url(item_id: str) -> str:
    return f"https://us.merchantos.com/?name=item.views.item&form_name=view&id={item_id}&tab=details"

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default

def to_json_safe(value):
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [to_json_safe(item) for item in value]
    if hasattr(value, "items"):
        return {key: to_json_safe(val) for key, val in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

# App initialization
# Disable the interactive docs / OpenAPI schema outside development so the
# public deployment doesn't publish a complete map of every endpoint. Set
# EXPOSE_API_DOCS=true (e.g. locally) to turn them back on.
_expose_docs = os.getenv("EXPOSE_API_DOCS", "false").strip().lower() in ("1", "true", "yes", "on")
app = FastAPI(
    title="SKU Reorder Point Automation API",
    docs_url="/docs" if _expose_docs else None,
    redoc_url="/redoc" if _expose_docs else None,
    openapi_url="/openapi.json" if _expose_docs else None,
)

# Setup CORS for frontend — restrict to known origins via env var
_raw_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3002,http://127.0.0.1:3002")
_allowed_origins = [o.strip().rstrip("/") for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared-secret gate. The frontend never calls this API directly from the browser;
# it goes through a same-origin Next.js proxy that (a) enforces the NextAuth session
# and (b) injects this secret server-side. So every legitimate request carries the
# X-Internal-Secret header and the browser never sees the value.
#
# Behavior:
#   * BACKEND_SHARED_SECRET set  -> every request (except liveness probes / preflight)
#     must present a matching header, else 401.
#   * BACKEND_SHARED_SECRET unset -> no-op, but we warn loudly at startup. This lets a
#     deploy land before the secret is configured on both services (zero-downtime
#     rollout) without instantly 401-ing the running app.
_INTERNAL_SECRET = os.getenv("BACKEND_SHARED_SECRET", "")
# Liveness/readiness probes hit these directly (Render health checks, uptime monitors)
# and carry no secret, so they stay open. They expose nothing sensitive.
_AUTH_EXEMPT_PATHS = {"/", "/api/health"}


@app.middleware("http")
async def _require_internal_secret(request: Request, call_next):
    if (
        _INTERNAL_SECRET
        and request.method != "OPTIONS"
        and request.url.path not in _AUTH_EXEMPT_PATHS
    ):
        provided = request.headers.get("x-internal-secret", "")
        if not hmac.compare_digest(provided, _INTERNAL_SECRET):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.on_event("startup")
def _warn_if_backend_unsecured() -> None:
    if not _INTERNAL_SECRET:
        print(
            "WARNING: BACKEND_SHARED_SECRET is not set — this API is UNAUTHENTICATED "
            "and reachable by anyone who knows its URL. Set BACKEND_SHARED_SECRET on "
            "this service AND the frontend proxy to enforce access control."
        )

# Price Intelligence is fully feature-flagged: with the flag off the package is
# never imported, so a disabled deployment carries none of its code, deps, or
# scheduler thread.
if os.getenv("PRICE_INTEL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
    from app.services.price_intelligence.router import router as price_intel_router
    from app.services.price_intelligence.scrape_runner import start_scheduler as _start_pi_scheduler

    app.include_router(price_intel_router)

    @app.on_event("startup")
    def _start_price_intel_scheduler() -> None:
        _start_pi_scheduler()

    @app.on_event("startup")
    def _warm_item_search_index() -> None:
        """Build the pin-picker search index on boot (daemon thread, non-blocking) so
        the first searches are fast without waiting for the nightly seed. Rebuilds only
        when the index is missing or older than ~20h, so restarts don't rebuild it."""
        def _run() -> None:
            try:
                from datetime import datetime, timezone, timedelta
                from app.services.price_intelligence import repository
                built = repository.item_search_built_at()
                if built is not None:
                    if built.tzinfo is None:
                        built = built.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - built < timedelta(hours=20):
                        return  # recent enough — don't rebuild on every restart
                repository.refresh_item_search_index()
            except Exception as e:
                print(f"pi: item search index warm-up failed (search falls back to live): {e}")
        threading.Thread(target=_run, daemon=True).start()


def _warm_replenishment_caches() -> None:
    """Populate small shared reference caches used across procurement surfaces.

    The legacy tagged-item inventory DataFrame is intentionally excluded: that
    heavier BigQuery/Pandas workload now loads only when a buyer explicitly opens
    the Inventory Dashboard tab.
    """
    try:
        from app.services.bigquery_sync import (
            fetch_lead_times,
            get_brand_sourcing_rules_map,
            fetch_brand_vendor_sourcing,
        )
        fetch_lead_times()
        get_brand_sourcing_rules_map()
        # Brand-level "Available from" sourcing for the Special Orders page.
        fetch_brand_vendor_sourcing()
    except Exception as e:
        print(f"Error warming replenishment caches: {e}")


@app.on_event("startup")
def _warm_dashboard_caches_on_startup() -> None:
    """Warm the Inventory dashboard caches on boot in a daemon thread so app
    startup isn't blocked by the BigQuery queries."""
    threading.Thread(target=_warm_replenishment_caches, daemon=True).start()


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Replenishment API is running"}

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}

@app.get("/api/replenishment/debug")
def get_replenishment_debug():
    try:
        from app.services.bigquery_sync import get_replenishment_debug_counts
        return {"status": "success", "debug": get_replenishment_debug_counts()}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/replenishment/debug/item/{item_id}")
def get_replenishment_item_debug(item_id: int):
    try:
        from app.services.bigquery_sync import get_item_replenishment_debug
        return {"status": "success", "debug": get_item_replenishment_debug(item_id)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/skus/template")
def download_sku_template():
    csv_content = "sku,item_id,product,brand,vendor,category\n"
    csv_content += "12345678,9999,Example Bike,Trek,Trek Bicycle,Bikes\n"
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=managed_skus_template.csv"}
    )

@app.get("/api/skus")
def fetch_managed_skus():
    skus = get_managed_skus()
    return skus

_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB cap — the SKU CSV is tiny; guard the 512MB instance.

@app.post("/api/skus/upload")
async def upload_skus(file: UploadFile = File(...)):
    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the 5 MB limit.")
    df = pd.read_csv(io.BytesIO(contents))
    
    # Expected columns: sku, item_id
    if 'sku' not in df.columns or 'item_id' not in df.columns:
        raise HTTPException(status_code=400, detail="Missing required column: 'sku' or 'item_id'")
    
    skus_to_log = df.to_dict('records')
    for s in skus_to_log:
        s['added_by'] = "UI_Upload"
        
    upsert_managed_skus(skus_to_log)
    return {"status": "success", "count": len(skus_to_log)}

@app.post("/api/skus/add-bulk")
def add_managed_skus_bulk(items: List[Dict[str, Any]]):
    skus_to_log = []
    for item in items:
        # system_id is usually item_id in Lightspeed
        item_id = item.get("system_id") or item.get("item_id") or ""
        sku = item.get("sku", "")
        if not sku or not item_id:
            continue
            
        skus_to_log.append({
            "sku": str(sku),
            "item_id": str(item_id),
            "product": str(item.get("description", "")),
            "brand": str(item.get("brand", "")),
            "vendor": str(item.get("vendor", "")),
            "category": str(item.get("category", "")),
            "added_by": item.get("added_by", "Dashboard_Bulk_Add")
        })
        
    if skus_to_log:
        upsert_managed_skus(skus_to_log)
        
    return {"status": "success", "added": len(skus_to_log)}

@app.get("/api/replenishment/data")
def get_replenishment_data(
    background_tasks: BackgroundTasks,
    forecast_period: int = None,
    safety_days: int = 7,
    growth_multiplier: float = 1.0,
    recent_30d_weight: float = None,
    weight_14d: float = None,
    weight_15_30d: float = None,
    weight_31_60d: float = None,
    adjustment_mode: str = "shrink",
    force_refresh: bool = False
):
    try:
        new_weights = (weight_14d, weight_15_30d, weight_31_60d)
        if any(weight is not None for weight in new_weights):
            if any(weight is None for weight in new_weights):
                raise HTTPException(status_code=400, detail="All demand weights must be provided together.")
            if any(weight < 0 or weight > 1 for weight in new_weights):
                raise HTTPException(status_code=400, detail="Demand weights must be between 0 and 1.")
            if abs(sum(new_weights) - 1.0) > 0.001:
                raise HTTPException(status_code=400, detail="Demand weights must total 1.0.")
        elif recent_30d_weight is None:
            weight_14d = 0.4
            weight_15_30d = 0.4
            weight_31_60d = 0.2

        if safety_days < 0:
            raise HTTPException(status_code=400, detail="safety_days must be 0 or greater.")
        if forecast_period is not None and forecast_period <= 0:
            raise HTTPException(status_code=400, detail="forecast_period must be a positive integer.")
        if growth_multiplier <= 0:
            raise HTTPException(status_code=400, detail="growth_multiplier must be greater than 0.")
        if adjustment_mode not in ("shrink", "min_days", "cap", "raw"):
            raise HTTPException(status_code=400, detail="adjustment_mode must be one of 'shrink', 'min_days', 'cap', or 'raw'.")

        # 1. Fetch BigQuery Data & Lead Times
        from app.services.bigquery_sync import (
            fetch_tagged_items_metrics,
            fetch_lead_times,
            get_brand_sourcing_rules_map,
        )
        # We handle caching via BigQuery functions or use simple manual cache dict if needed.
        # For now, fetch_tagged_items_metrics will hit BQ directly on every call unless we add caching.
        # In a real production scenario, caching this result is recommended.
        raw_data = fetch_tagged_items_metrics("auto-replen", force_refresh=force_refresh).to_dict(orient="records")
        lead_times = fetch_lead_times().to_dict(orient="records")
        brand_sourcing_rules = get_brand_sourcing_rules_map()
        
        # 1.5 Fetch Overrides
        overrides = get_sku_overrides()

        # 2. Process Recommendations
        from app.services.replenishment_engine import process_recommendations, calculate_inventory_status
                
        recommendations = process_recommendations(
            raw_data, 
            lead_times,
            brand_sourcing_rules=brand_sourcing_rules,
            safety_days=safety_days, 
            override_forecast=forecast_period,
            growth_multiplier=growth_multiplier,
            recent_30d_weight=recent_30d_weight,
            weight_14d=weight_14d,
            weight_15_30d=weight_15_30d,
            weight_31_60d=weight_31_60d,
            adjustment_mode=adjustment_mode,
        )

        # 3. Apply Overrides (Locked, Manual ROP/DL)
        for rec in recommendations:
            key = f"{rec['sku']}_{rec['location']}"
            if key in overrides:
                ov = overrides[key]
                if ov.get('locked'):
                    rec['locked'] = True
                if ov.get('manual_reorder_point') is not None:
                    rec['recommended_reorder_point'] = ov['manual_reorder_point']
                if ov.get('manual_desired_level') is not None:
                    rec['recommended_desired_level'] = ov['manual_desired_level']

                inventory_status = calculate_inventory_status(
                    float(rec.get('on_hand') or 0),
                    float(rec.get('on_order') or 0),
                    float(rec.get('recommended_reorder_point') or 0),
                    float(rec.get('recommended_desired_level') or 0),
                )
                rec.update(inventory_status)
                rec['qty_to_order'] = max(
                    0,
                    int(float(rec.get('recommended_desired_level') or 0) - inventory_status['inventory_position'])
                )
                
                # Re-calculate change_needed
                rec['change_needed'] = (
                    rec['recommended_reorder_point'] != rec['current_reorder_point'] or 
                    rec['recommended_desired_level'] != rec['current_desired_level']
                )
        
        # 4. Save NEW snapshots and run info to BigQuery. These are two streaming
        # inserts that don't affect the response payload, so defer them to run
        # AFTER the response is sent — they no longer sit in front of every load
        # (and every debounced slider change).
        run_id = str(uuid.uuid4())
        run_log = {
            "run_id": run_id,
            "run_type": "manual",
            "triggered_by": "UI_User",
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "status": "completed",
            "row_count": len(recommendations)
        }
        background_tasks.add_task(log_recommendation_run, run_log)

        snapshots = []
        for rec in recommendations:
            snapshots.append({
                "system_id": str(rec['system_id']),
                "sku": str(rec['sku']),
                "location": rec['location'],
                "daily_sales": float(rec['raw_daily_sales'] if 'raw_daily_sales' in rec else rec['daily_sales']),
                "created_at": datetime.utcnow().isoformat()
            })
        background_tasks.add_task(log_velocity_snapshots, snapshots)

        # Organize by location
        by_location = {
            "Bici Adanac": [],
            "Victoria": [],
            "Langford": []
        }
        
        for rec in recommendations:
            by_location.setdefault(rec['location'], []).append(rec)
                
        return {
            "status": "success",
            "run_id": run_id,
            "locations": list(by_location.keys()),
            "data": by_location
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/replenishment/push")
def push_replenishment_updates(updates: List[Dict[str, Any]]):
    from app.services.lightspeed_client import LightspeedClient
    client = LightspeedClient()
    results = []
    
    for update in updates:
        success = client.sync_recommendation(update)
        
        # Log to BigQuery
        triggered_by = update.get('pushed_by') or "UI_Manual_Push"
        log_data = {
            "sku": str(update.get('sku')),
            "location_id": str(update.get('location')),
            "old_reorder_point": _safe_int(update.get('current_reorder_point')),
            "new_reorder_point": _safe_int(update.get('recommended_reorder_point')),
            "old_desired_inventory": _safe_int(update.get('current_desired_level')),
            "new_desired_inventory": _safe_int(update.get('recommended_desired_level')),
            "triggered_by": triggered_by,
            "status": "success" if success else "failed",
            "error_message": None if success else "Lightspeed API Write Failure",
            "created_at": datetime.utcnow().isoformat()
        }
        log_writeback(log_data)
        results.append({"sku": update.get('sku'), "success": success})
        
    return {"status": "completed", "results": results}

@app.get("/api/replenishment/runs")
def get_recommendation_history(limit: int = 50):
    runs = get_recommendation_runs(limit)
    return runs

@app.get("/api/replenishment/logs")
def get_audit_logs(limit: int = 100):
    logs = fetch_writeback_logs(limit)
    formatted_logs = []
    for log in logs:
        rop_changed = log.get('new_reorder_point') != log.get('old_reorder_point')
        dl_changed = log.get('new_desired_inventory') != log.get('old_desired_inventory')
        
        # If ROP changed, or if neither changed (e.g. baseline or failed attempt fallback)
        if rop_changed or (not rop_changed and not dl_changed):
            formatted_logs.append({
                "id": str(uuid.uuid4()),
                "timestamp": log.get('created_at').isoformat() if hasattr(log.get('created_at'), 'isoformat') else str(log.get('created_at')),
                "user": log.get('triggered_by'),
                "sku": log.get('sku'),
                "location": log.get('location_id'),
                "field": "reorder_point",
                "oldValue": log.get('old_reorder_point'),
                "newValue": log.get('new_reorder_point'),
                "status": log.get('status'),
                "errorMessage": log.get('error_message')
            })
            
        if dl_changed:
            formatted_logs.append({
                "id": str(uuid.uuid4()),
                "timestamp": log.get('created_at').isoformat() if hasattr(log.get('created_at'), 'isoformat') else str(log.get('created_at')),
                "user": log.get('triggered_by'),
                "sku": log.get('sku'),
                "location": log.get('location_id'),
                "field": "desired_level",
                "oldValue": log.get('old_desired_inventory'),
                "newValue": log.get('new_desired_inventory'),
                "status": log.get('status'),
                "errorMessage": log.get('error_message')
            })
    return formatted_logs

@app.post("/api/replenishment/override")
def save_override(override: Dict[str, Any]):
    upsert_sku_override(override)
    return {"status": "success"}

@app.get("/api/replenishment/active-vendor-lead-times")
def get_active_vendor_lead_times(force_refresh: bool = False):
    try:
        from app.services.bigquery_sync import fetch_active_vendor_lead_times
        result = fetch_active_vendor_lead_times(active_days=90, force_refresh=force_refresh)
        return {
            "status": "success",
            "data": to_json_safe(result["data"]),
            "meta": to_json_safe(result["meta"]),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/replenishment/brand-sourcing-rules")
def get_brand_sourcing_rules(force_refresh: bool = False):
    try:
        from app.services.bigquery_sync import fetch_brand_sourcing_rules
        data = fetch_brand_sourcing_rules(force_refresh=force_refresh)
        return {"status": "success", "data": to_json_safe(data)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/replenishment/brand-sourcing-rules")
def save_brand_sourcing_rule(rule: Dict[str, Any]):
    if not rule.get("brand_name"):
        raise HTTPException(status_code=400, detail="brand_name is required")
    try:
        from app.services.bigquery_sync import upsert_brand_sourcing_rule
        upsert_brand_sourcing_rule(rule)
        return {"status": "success"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")

# ---------------------------------------------------------------------------
# Demand & Seasonality (forecast visualization layer)
#
# Read-only endpoints that expose the already-built seasonal/forecast signal for
# charting. They visualize the profile and a projected forecast only -- they do
# NOT flip the opt-in seasonality engine on for live ROP/DL writeback.
# ---------------------------------------------------------------------------

@app.get("/api/forecast/seasonal-profiles")
def get_seasonal_profiles(
    years: int = 3, smoothing: float = 0.0, location: str = None, measure: str = "units"
):
    measure_columns = {"units": "total_units_sold", "cogs": "total_cogs", "revenue": "total_revenue"}
    if measure not in measure_columns:
        raise HTTPException(status_code=400, detail="measure must be units, cogs, or revenue")
    try:
        from app.services.bigquery_sync import fetch_monthly_category_history
        from app.services.forecasting import build_seasonal_profile_response
        df = fetch_monthly_category_history(years=years)
        if location is not None and len(df) and "location_id" in df.columns:
            df = df[df["location_id"].astype(str) == str(location)]
        if measure != "units":
            df = df.copy()
            df["total_units_sold"] = df[measure_columns[measure]]
        records = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
        profiles = build_seasonal_profile_response(records, smoothing=smoothing)
        return {
            "status": "success",
            "data": to_json_safe(profiles),
            "meta": {"years": years, "category_count": len(profiles), "location": location, "measure": measure},
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/forecast/history")
def get_demand_history(
    scope: str,
    id: str,
    location: str = None,
    years: int = 3,
    horizon_months: int = 12,
    lead_time_days: float = 14.0,
    coverage_days: float = 30.0,
    measure: str = "units",
):
    """Monthly sales history + forward forecast for a category or a single SKU.

    The forecast is a flat monthly baseline scaled by the (blended, for SKUs)
    seasonal index per month. ``lead_time_window`` marks the months a PO placed
    now would cover, so the chart can shade "buy ahead of the ramp".
    """
    if scope not in ("category", "sku"):
        raise HTTPException(status_code=400, detail="scope must be 'category' or 'sku'")
    measure_columns = {"units": "total_units_sold", "cogs": "total_cogs", "revenue": "total_revenue"}
    if measure not in measure_columns:
        raise HTTPException(status_code=400, detail="measure must be units, cogs, or revenue")
    try:
        from app.services.bigquery_sync import (
            fetch_monthly_category_history,
            fetch_item_monthly_history,
        )
        from app.services.forecasting import (
            seasonality_indices,
            blend_seasonal_indices,
            project_monthly_forecast,
            lead_time_window_months,
        )

        def _filter_location(frame):
            if location is not None and len(frame) and "location_id" in frame.columns:
                return frame[frame["location_id"].astype(str) == str(location)]
            return frame

        # Pull only what the scope needs server-side: a single item's rows for a
        # SKU, or category-grain rows for a category. Neither materializes the full
        # catalog the way the old per-item pull did.
        if scope == "sku":
            rows = _filter_location(fetch_item_monthly_history(id, years=years))
        else:
            cat_df = _filter_location(fetch_monthly_category_history(years=years))
            rows = cat_df[
                (cat_df["category_top_level"].astype(str) == str(id))
                | (cat_df.get("category_path", pd.Series(dtype=str)).astype(str) == str(id))
                | (cat_df.get("category_level_2", pd.Series(dtype=str)).astype(str) == str(id))
            ]

        def period_totals(frame):
            grouped = frame.groupby("month_of_year")[measure_columns[measure]].sum()
            return {int(m): float(v) for m, v in grouped.items()}

        own_totals = period_totals(rows)
        # Distinct (year, month) buckets observed -- the baseline divisor + blend weight.
        months_observed = int(rows.groupby(["sales_year", "month_of_year"]).ngroups) if len(rows) else 0

        if scope == "sku":
            cat_label = str(rows["category_top_level"].dropna().iloc[0]) if len(rows.dropna(subset=["category_top_level"])) else None
            if cat_label:
                cat_df = _filter_location(fetch_monthly_category_history(years=years))
                cat_rows = cat_df[cat_df["category_top_level"].astype(str) == cat_label]
            else:
                cat_rows = rows
            own_indices = seasonality_indices(own_totals)
            category_indices = seasonality_indices(period_totals(cat_rows))
            indices = blend_seasonal_indices(own_indices, category_indices, own_history_periods=months_observed)
        else:
            indices = seasonality_indices(own_totals)

        history = [
            {"year": int(r.sales_year), "month": int(r.month_of_year), "units": round(float(r.units), 2)}
            for r in (
                rows.groupby(["sales_year", "month_of_year"])[measure_columns[measure]].sum()
                .reset_index(name="units")
                .sort_values(["sales_year", "month_of_year"])
                .itertuples()
            )
        ]

        # History ends at the last COMPLETE month, so the forecast starts at the
        # current (in-progress) month -- the two stay contiguous with no gap.
        current_month = datetime.now().month
        last_complete_month = current_month - 1 or 12
        forecast = project_monthly_forecast(
            own_totals, months_observed, indices, last_complete_month,
            horizon_months=horizon_months,
            # Anchor on recent run-rate + apply the damped/capped growth trend.
            monthly_level_series=history,
        )
        # The PO-coverage window is anchored to TODAY (a PO placed now), not the
        # forecast's last-complete-month anchor.
        window = lead_time_window_months(current_month, lead_time_days, coverage_days)

        return {
            "status": "success",
            "data": {
                "history": to_json_safe(history),
                "forecast": to_json_safe(forecast),
                "lead_time_window": window,
            },
            "meta": {
                "scope": scope,
                "id": id,
                "months_observed": months_observed,
                "reference_month": last_complete_month,
                "measure": measure,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/forecast/coverage")
def get_forward_coverage(
    location: str = None,
    horizon_months: int = 12,
    limit: int = None,
    force_refresh: bool = False,
):
    """Forward weeks-of-cover per SKU x location for the next ``horizon_months``.

    Projects on-hand + on-order draw-down using each SKU's velocity scaled by its
    category's seasonal shape, so the heatmap surfaces *future* (often seasonal)
    stockouts before they happen. Rows are sorted with the soonest stockouts first.
    """
    try:
        from app.services.bigquery_sync import (
            fetch_tagged_items_metrics,
            fetch_lead_times,
            get_brand_sourcing_rules_map,
            fetch_monthly_category_history,
        )
        from app.services.replenishment_engine import process_recommendations
        from app.services.forecasting import build_seasonal_profiles, project_weeks_of_cover

        raw_data = fetch_tagged_items_metrics("auto-replen", force_refresh=force_refresh).to_dict(orient="records")
        lead_times = fetch_lead_times().to_dict(orient="records")
        brand_sourcing_rules = get_brand_sourcing_rules_map()
        recommendations = process_recommendations(
            raw_data, lead_times, brand_sourcing_rules=brand_sourcing_rules,
            weight_14d=0.4, weight_15_30d=0.4, weight_31_60d=0.2,
        )

        # One seasonal profile per category, merged across levels (most specific wins).
        history_records = fetch_monthly_category_history(years=3).to_dict("records")
        level_fields = ("category_path", "category_level_2", "category_top_level")
        profiles = build_seasonal_profiles(history_records, level_fields)
        merged_profiles = {}
        for lf in ("category_top_level", "category_level_2", "category_path"):
            merged_profiles.update(profiles.get(lf, {}))

        # Forecast from the last complete month so the projection lines up with the
        # history+forecast chart (which excludes the in-progress month).
        last_complete_month = datetime.now().month - 1 or 12
        # Location filter accepts a Lightspeed shop id; map it to the rec's name.
        shop_map = {3: "Bici Adanac", 2: "Victoria", 20: "Langford"}
        target_location = shop_map.get(_safe_int(location)) if location is not None else None
        rows = []
        for rec in recommendations:
            if target_location is not None and rec.get("location") != target_location:
                continue
            indices = merged_profiles.get(rec.get("category"))
            cover = project_weeks_of_cover(
                on_hand=rec.get("on_hand") or 0,
                on_order=rec.get("on_order") or 0,
                daily_velocity=rec.get("daily_sales") or 0,
                indices=indices,
                reference_month=last_complete_month,
                horizon_months=horizon_months,
            )
            rows.append({
                "sku": rec.get("sku"),
                "lightspeed_item_id": rec.get("lightspeed_item_id"),
                "product": rec.get("description"),
                "location": rec.get("location"),
                "weeks_of_cover": cover,
            })

        # Soonest stockout first: index of the first critical month (12 = none).
        def first_critical(row):
            for i, month in enumerate(row["weeks_of_cover"]):
                if month["stockout_risk"] == "critical":
                    return i
            return len(row["weeks_of_cover"])
        rows.sort(key=first_critical)
        if limit is not None and limit > 0:
            rows = rows[:limit]

        return {
            "status": "success",
            "data": {"rows": to_json_safe(rows)},
            "meta": {"reference_month": last_complete_month, "row_count": len(rows)},
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Purchase Order Master Dashboard
# ---------------------------------------------------------------------------

def _planning_v2_enabled() -> bool:
    return os.getenv("PLANNING_V2_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _require_planning_v2() -> None:
    if not _planning_v2_enabled():
        raise HTTPException(status_code=404, detail="Planning v2 is disabled.")


def _draft_line_from_recommendation(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "recommendation_id": rec.get("recommendation_id"),
        "sku": rec.get("sku"),
        "description": rec.get("description"),
        "brand": rec.get("brand"),
        "category_top_level": rec.get("category_top_level"),
        "item_id": rec.get("item_id"),
        "location_id": rec.get("location_id"),
        "quantity": _safe_int(rec.get("recommended_quantity")),
        "unit_cost": rec.get("landed_unit_cost"),
        "landed_cost": rec.get("landed_unit_cost"),
        "currency": rec.get("currency") or "CAD",
        "source": "recommendation",
        "reconciliation": "new_po",
        "need_by_week": rec.get("need_by_week"),
        "case_pack": rec.get("case_pack"),
        "moq": rec.get("moq"),
        "constraint_warning": "Supplier constraints added units" if rec.get("constraint_extra_units") else None,
    }


@app.post("/api/planning/runs")
def create_planning_run_endpoint(payload: Dict[str, Any] = None):
    _require_planning_v2()
    payload = payload or {}
    try:
        from app.services.planning_service import create_planning_run
        run = create_planning_run(
            as_of_date=payload.get("as_of_date"),
            horizon_weeks=_safe_int(payload.get("horizon_weeks"), 52),
            location_ids=payload.get("location_ids"),
            force_refresh=bool(payload.get("force_refresh")),
            scope_type=payload.get("scope_type") or "auto_replen",
            scope_value=payload.get("scope_value"),
            item_ids=payload.get("item_ids"),
            config=payload.get("config"),
        )
        return {"status": "success", "data": to_json_safe(run)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to create planning run.")


@app.get("/api/planning/runs/latest")
def latest_planning_run_endpoint():
    _require_planning_v2()
    from app.services.planning_service import get_latest_planning_run
    run = get_latest_planning_run()
    return {"status": "success", "data": to_json_safe(run)}


@app.get("/api/planning/models")
def planning_models_endpoint():
    _require_planning_v2()
    from app.services.demand_planning import MODEL_LEGEND
    return {"status": "success", "data": MODEL_LEGEND}


@app.get("/api/planning/runs/{run_id}/recommendations")
def planning_recommendations_endpoint(run_id: str, location_id: str = None, blocked: bool = None):
    _require_planning_v2()
    from app.services.planning_service import get_planning_run, get_recommendations
    run = get_planning_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Planning run not found.")
    rows = get_recommendations(run_id, location_id=location_id, blocked=blocked)
    return {
        "status": "success",
        "data": to_json_safe(rows),
        "meta": {
            "run_id": run_id,
            "model_version": run["model_version"],
            "assumption_version": run["assumption_version"],
            "source_snapshot_at": run["source_snapshot_at"],
            "monthly_rollups": to_json_safe(run["monthly_rollups"]),
        },
    }


@app.get("/api/planning/forecast/{item_id}/{location_id}")
def planning_forecast_endpoint(item_id: str, location_id: str, run_id: str):
    _require_planning_v2()
    from app.services.planning_service import get_forecast, get_planning_run
    if not get_planning_run(run_id):
        raise HTTPException(status_code=404, detail="Planning run not found.")
    forecast = get_forecast(run_id, item_id, location_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail="Forecast not found.")
    return {"status": "success", "data": to_json_safe(forecast)}


@app.post("/api/planning/overrides")
def create_planning_override_endpoint(payload: Dict[str, Any]):
    _require_planning_v2()
    try:
        from app.services.planning_store import get_planning_store
        return {"status": "success", "data": to_json_safe(get_planning_store().create_override(payload))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/po/draft")
def create_po_draft_endpoint(payload: Dict[str, Any]):
    return create_po_drafts_endpoint(payload)


@app.post("/api/po/drafts")
def create_po_drafts_endpoint(payload: Dict[str, Any]):
    """Create local drafts from only the recommendations selected by a buyer."""
    _require_planning_v2()
    run_id = payload.get("run_id")
    recommendation_ids = payload.get("recommendation_ids") or []
    manual_lines = payload.get("lines") or []
    if not recommendation_ids and not manual_lines:
        raise HTTPException(status_code=400, detail="Select recommendations or provide manual lines.")
    from app.services.planning_service import get_planning_run, get_recommendations_by_id
    from app.services.planning_store import get_planning_store
    run = get_planning_run(run_id) if run_id else None
    recs = []
    if recommendation_ids:
        if not run:
            raise HTTPException(status_code=404, detail="Planning run not found.")
        recs = get_recommendations_by_id(run_id, recommendation_ids)
        if len(recs) != len(set(map(str, recommendation_ids))):
            raise HTTPException(status_code=400, detail="One or more recommendations were not found.")
        blocked = [rec["recommendation_id"] for rec in recs if rec.get("blocked")]
        if blocked:
            raise HTTPException(status_code=409, detail={
                "message": "Resolve blocking vendor/cost exceptions first.",
                "recommendation_ids": blocked,
            })

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for rec in recs:
        key = (str(rec.get("vendor_id")), str(rec.get("location_id")))
        grouped.setdefault(key, {"vendor_name": rec.get("vendor_name"), "lines": []})
        grouped[key]["lines"].append(_draft_line_from_recommendation(rec))
    for line in manual_lines:
        vendor_id = line.get("vendor_id") or payload.get("vendor_id")
        shop_id = line.get("location_id") or payload.get("shop_id")
        if not vendor_id or not shop_id or not line.get("item_id"):
            raise HTTPException(status_code=400, detail="Manual lines require vendor_id, location_id and item_id.")
        if line.get("unit_cost") is None and line.get("landed_cost") is None:
            raise HTTPException(status_code=409, detail="Manual lines require a landed/unit cost.")
        key = (str(vendor_id), str(shop_id))
        grouped.setdefault(key, {"vendor_name": line.get("vendor_name"), "lines": []})
        grouped[key]["lines"].append({**line, "source": "manual", "reconciliation": "new_po"})

    store = get_planning_store()
    drafts = []
    for (vendor_id, shop_id), group in grouped.items():
        drafts.append(store.create_draft({
            "vendor_id": vendor_id,
            "vendor_name": group["vendor_name"],
            "shop_id": shop_id,
            "created_by": payload.get("created_by") or "UI_User",
            "run_id": run_id,
            "model_version": run.get("model_version") if run else None,
            "source_snapshot_at": run.get("source_snapshot_at") if run else None,
        }, group["lines"]))
    return {"status": "success", "data": to_json_safe(drafts)}

@app.get("/api/po/drafts")
def list_po_drafts(status: str = None):
    try:
        from app.services.planning_store import get_planning_store
        return {"status": "success", "data": to_json_safe(get_planning_store().list_drafts(status))}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch PO drafts.")

@app.get("/api/po/draft/{draft_id}")
def get_po_draft_endpoint(draft_id: str):
    return get_po_draft_v2_endpoint(draft_id)


@app.get("/api/po/drafts/{draft_id}")
def get_po_draft_v2_endpoint(draft_id: str):
    try:
        from app.services.planning_store import get_planning_store
        draft = get_planning_store().get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found.")
        return {"status": "success", "data": to_json_safe(draft)}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch PO draft.")

@app.put("/api/po/draft/{draft_id}")
def update_po_draft_endpoint(draft_id: str, payload: Dict[str, Any]):
    return update_po_draft_v2_endpoint(draft_id, payload)


@app.patch("/api/po/drafts/{draft_id}")
def update_po_draft_v2_endpoint(draft_id: str, payload: Dict[str, Any]):
    """Optimistically replace editable lines and return the new version."""
    lines = payload.get("lines")
    expected_version = payload.get("expected_version")
    if lines is None or expected_version is None:
        raise HTTPException(status_code=400, detail="lines and expected_version are required.")
    try:
        from app.services.planning_store import PlanningConflict, get_planning_store
        draft = get_planning_store().replace_lines(draft_id, int(expected_version), lines)
        return {"status": "success", "data": to_json_safe(draft)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Draft not found.")
    except PlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/po/drafts/{draft_id}/target-order")
def set_po_draft_target_endpoint(draft_id: str, payload: Dict[str, Any]):
    """Persist the buyer's explicit unsent-PO selection; performs no LS write."""
    try:
        from app.services.lightspeed_gateway import LiveLightspeedReadGateway, LightspeedReadError
        from app.services.planning_store import PlanningConflict, get_planning_store
        target = payload.get("order_id")
        if target not in (None, "", "new"):
            draft = get_planning_store().get_draft(draft_id)
            if not draft:
                raise HTTPException(status_code=404, detail="Draft not found.")
            candidates = LiveLightspeedReadGateway().list_purchase_orders(
                vendor_id=draft["vendor_id"], shop_id=draft["shop_id"]
            )
            selected = next((row for row in candidates if str(row.get("orderID")) == str(target)), None)
            if not selected or selected.get("po_state") != "unsent":
                raise HTTPException(status_code=409, detail="The selected Lightspeed PO is no longer eligible.")
        updated = get_planning_store().set_target_order(
            draft_id, int(payload["expected_version"]), target
        )
        return {"status": "success", "data": to_json_safe(updated)}
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail="Draft not found or version is missing.")
    except PlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LightspeedReadError as exc:
        raise HTTPException(status_code=503, detail=f"Complete Lightspeed PO snapshot unavailable: {exc}")

@app.delete("/api/po/draft/{draft_id}")
def delete_po_draft_endpoint(draft_id: str):
    response = get_po_draft_v2_endpoint(draft_id)
    return transition_po_draft_endpoint(draft_id, {
        "expected_version": response["data"]["version"], "status": "cancelled"
    })


@app.post("/api/po/drafts/{draft_id}/transition")
def transition_po_draft_endpoint(draft_id: str, payload: Dict[str, Any]):
    try:
        from app.services.planning_store import PlanningConflict, get_planning_store
        draft = get_planning_store().transition(
            draft_id, int(payload["expected_version"]), str(payload["status"])
        )
        return {"status": "success", "data": to_json_safe(draft)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Draft not found or transition fields missing.")
    except PlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/po/drafts/{draft_id}/reconcile")
def reconcile_po_draft_endpoint(draft_id: str):
    """Read every PO page and return proposed routing without an LS mutation."""
    try:
        from app.services.lightspeed_gateway import LiveLightspeedReadGateway, LightspeedReadError
        from app.services.planning_store import get_planning_store
        from app.services.po_service import preview_draft
        draft = get_planning_store().get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found.")
        return {"status": "success", "data": to_json_safe(preview_draft(draft, LiveLightspeedReadGateway()))}
    except HTTPException:
        raise
    except LightspeedReadError as exc:
        raise HTTPException(status_code=503, detail=f"Complete Lightspeed PO snapshot unavailable: {exc}")


@app.post("/api/po/drafts/{draft_id}/preview")
def preview_po_draft_endpoint(draft_id: str):
    """Generate exact normalized LS mutations without invoking a write method."""
    try:
        from app.services.lightspeed_gateway import LiveLightspeedReadGateway, LightspeedReadError
        from app.services.planning_store import PlanningConflict, get_planning_store
        from app.services.po_service import preview_draft
        store = get_planning_store()
        draft = store.get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found.")
        if draft["status"] not in {"approved", "previewed"}:
            raise HTTPException(status_code=409, detail="Approve the draft before generating a preview.")
        preview = preview_draft(draft, LiveLightspeedReadGateway())
        preview["idempotency_actions"] = store.record_actions(draft, preview["operations"])
        if draft["status"] == "approved":
            draft = store.transition(draft_id, int(draft["version"]), "previewed")
        preview["draft"] = draft
        return {"status": "success", "data": to_json_safe(preview)}
    except HTTPException:
        raise
    except LightspeedReadError as exc:
        raise HTTPException(status_code=503, detail=f"Complete Lightspeed PO snapshot unavailable: {exc}")
    except PlanningConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

# ---------------------------------------------------------------------------
# PO Tracker: placed-but-unreceived POs triaged against expected arrival dates
# ---------------------------------------------------------------------------

@app.get("/api/po/watch")
def get_po_watch(ordered_within_days: int = 300, force_refresh: bool = False):
    """Ordered/partially-received POs with lateness triage, lead-time context,
    ack state and a triage summary. ``ordered_within_days`` bounds how far back
    the orderedDate window reaches (clamped to 1-730)."""
    try:
        from app.services.po_watch_service import get_po_watchlist
        return {"status": "success", **to_json_safe(get_po_watchlist(
            ordered_within_days=ordered_within_days, force_refresh=force_refresh
        ))}
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Failed to load the PO tracker from Lightspeed.")


@app.get("/api/po/watch/{order_id}/lines")
def get_po_watch_lines(order_id: str):
    try:
        from app.services.po_watch_service import get_po_lines
        detail = get_po_lines(order_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Purchase order not found.")
        return {"status": "success", "data": to_json_safe(detail)}
    except HTTPException:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Failed to load PO lines from Lightspeed.")


@app.post("/api/po/watch/{order_id}/ack")
def ack_po_watch_alert(order_id: str, payload: Dict[str, Any] = None):
    """Acknowledge/snooze a late-PO alert. Body: { acked_by?, note?, snooze_days?,
    expected_date? }. snooze_days omitted/null = snooze until the expected date
    changes in Lightspeed or the PO is received."""
    payload = payload or {}
    snooze_until = None
    snooze_days = payload.get("snooze_days")
    if snooze_days is not None:
        days = _safe_int(snooze_days)
        if days <= 0:
            raise HTTPException(status_code=400, detail="snooze_days must be a positive integer.")
        from datetime import timedelta, date as _date
        snooze_until = (_date.today() + timedelta(days=days)).isoformat()
    from app.services.planning_store import get_planning_store
    record = get_planning_store().upsert_po_ack(
        order_id,
        acked_by=payload.get("acked_by"),
        note=payload.get("note"),
        snooze_until=snooze_until,
        expected_date=payload.get("expected_date") or None,
    )
    return {"status": "success", "data": to_json_safe(record)}


@app.delete("/api/po/watch/{order_id}/ack")
def unack_po_watch_alert(order_id: str):
    from app.services.planning_store import get_planning_store
    get_planning_store().delete_po_ack(order_id)
    return {"status": "success"}


@app.post("/api/po/watch/slack-digest")
def trigger_po_watch_digest(payload: Dict[str, Any] = None):
    """Manually posts the late-PO Slack digest (bypasses the enabled flag and the
    once-per-day scheduler guard; still requires the webhook env var)."""
    from app.services.po_watch_service import send_slack_digest
    return {"status": "success", "data": send_slack_digest(force=True)}


@app.on_event("startup")
def _start_po_watch_scheduler() -> None:
    if os.getenv("PO_WATCH_SLACK_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
        from app.services.po_watch_service import start_scheduler
        start_scheduler()


def _warm_po_snapshot_cache() -> None:
    """Populate the read-only PO header cache before the buyer opens a draft."""
    try:
        from app.services.po_snapshot_service import get_po_snapshot_cache
        result = get_po_snapshot_cache().get_orders()
        print(
            f"Warmed complete Lightspeed PO header snapshot: "
            f"{result['meta']['total_order_count']} orders."
        )
    except Exception as exc:
        # The endpoint remains fail-closed and will retry on demand. Startup must
        # not fail merely because an external read is temporarily unavailable.
        print(f"Lightspeed PO snapshot warm-up failed: {exc}")


@app.on_event("startup")
def _warm_po_snapshot_cache_on_startup() -> None:
    threading.Thread(target=_warm_po_snapshot_cache, daemon=True).start()


@app.get("/api/po/open-orders")
def list_open_orders(
    vendor_id: str = None, shop_id: str = None, refresh: bool = False
):
    """Filter one complete shared Lightspeed PO snapshot for the workbench."""
    try:
        from app.services.lightspeed_gateway import LightspeedReadError
        from app.services.po_snapshot_service import get_po_snapshot_cache
        result = get_po_snapshot_cache().get_orders(
            vendor_id=vendor_id, shop_id=shop_id, force_refresh=refresh
        )
        return {
            "status": "success",
            "data": to_json_safe(result["orders"]),
            "meta": to_json_safe(result["meta"]),
        }
    except LightspeedReadError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Complete Lightspeed PO snapshot unavailable: {exc}",
        )
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch open orders.")

@app.post("/api/po/push/{draft_id}")
def push_po_draft_endpoint(draft_id: str, payload: Dict[str, Any] = None):
    raise HTTPException(
        status_code=403,
        detail="Live Lightspeed PO synchronization is disabled. Generate a preview instead.",
    )


@app.post("/api/po/drafts/{draft_id}/push")
def push_po_draft_v2_endpoint(draft_id: str, payload: Dict[str, Any] = None):
    return push_po_draft_endpoint(draft_id, payload)

@app.get("/api/health/lightspeed")
def check_lightspeed_health():
    from app.services.lightspeed_client import LightspeedClient
    client = LightspeedClient()
    is_connected = client.check_health()
    if is_connected:
        return {"status": "connected"}
    else:
        raise HTTPException(status_code=503, detail="Disconnected from Lightspeed")

@app.get("/api/health/lightspeed-po")
def check_lightspeed_po_access():
    """
    Reports whether the current Lightspeed token can access purchase orders.
    Used to confirm the token was re-authorized with employee:purchase_orders
    before relying on the PO push path.
    """
    from app.services.lightspeed_client import LightspeedClient
    client = LightspeedClient()
    if client.check_po_access():
        return {"status": "ok", "po_access": True}
    raise HTTPException(
        status_code=503,
        detail="Lightspeed token cannot access purchase orders. Re-authorize with the employee:purchase_orders scope (see backend/reauthorize_lightspeed.py).",
    )

@app.get("/api/health/shopify")
def check_shopify_health():
    from app.services.shopify_client import ShopifyClient
    client = ShopifyClient()
    if client.check_health():
        return {"status": "connected"}
    raise HTTPException(status_code=503, detail="Disconnected from Shopify")

@app.get("/api/health/bigquery")
def check_bigquery_health():
    from app.services.bigquery_sync import get_bq_client
    try:
        client = get_bq_client()
        # Force an actual API call by iterating over the results
        list(client.list_datasets(max_results=1))
        return {"status": "connected"}
    except Exception as e:
        print(f"BigQuery health check failed: {e}")
        raise HTTPException(status_code=503, detail="Disconnected from BigQuery")

@app.get("/api/replenishment/ls-link/{item_id}")
def get_lightspeed_link(item_id: str):
    return RedirectResponse(url=build_lightspeed_item_url(item_id))


# In-process TTL cache so each Special Order page load doesn't re-walk the whole
# Lightspeed SO graph (and hit rate limits). The frontend "Sync" button passes
# refresh=true to force a live re-fetch. Past the TTL the cached payload is still
# served immediately while a background task refreshes it (stale-while-revalidate),
# so only a genuine cold start (empty cache) or an explicit Sync ever blocks.
_special_orders_cache: Dict[str, Any] = {"data": None, "fetched_at": 0.0}
_SPECIAL_ORDERS_TTL_SECONDS = 300
# Serializes rebuilds so concurrent cold/stale requests share one Lightspeed walk
# instead of stampeding the API.
_special_orders_lock = threading.Lock()


def _rebuild_special_orders_cache(force: bool = False) -> Dict[str, Any]:
    """Runs the live Lightspeed walk under the lock and stores the result. If
    another thread refreshed a still-fresh payload while we waited for the lock,
    that payload is returned instead of walking again."""
    from app.services.special_order_service import get_special_order_dashboard

    with _special_orders_lock:
        cached = _special_orders_cache.get("data")
        age = time.time() - _special_orders_cache.get("fetched_at", 0.0)
        if not force and cached is not None and age < _SPECIAL_ORDERS_TTL_SECONDS:
            return cached
        result = get_special_order_dashboard()
        result["fetched_at"] = datetime.utcnow().isoformat() + "Z"
        _special_orders_cache["data"] = result
        _special_orders_cache["fetched_at"] = time.time()
        return result


def _refresh_special_orders_in_background() -> None:
    """Best-effort background refresh. Skips if a rebuild is already running, so a
    burst of stale requests only triggers one walk."""
    if not _special_orders_lock.acquire(blocking=False):
        return
    try:
        from app.services.special_order_service import get_special_order_dashboard
        result = get_special_order_dashboard()
        result["fetched_at"] = datetime.utcnow().isoformat() + "Z"
        _special_orders_cache["data"] = result
        _special_orders_cache["fetched_at"] = time.time()
    except Exception as e:
        print(f"Error refreshing special-order dashboard: {e}")
    finally:
        _special_orders_lock.release()


@app.on_event("startup")
def _warm_special_orders_cache() -> None:
    """Build the special-orders cache on boot in a daemon thread so the first user
    after a deploy/restart doesn't pay the full Lightspeed walk."""
    threading.Thread(target=_refresh_special_orders_in_background, daemon=True).start()


@app.get("/api/special-orders")
def get_special_orders(background_tasks: BackgroundTasks, refresh: bool = False):
    """
    Returns open special orders with derived overdue/aging fields and the summary
    counts that drive the dashboard KPIs:
      { "orders": [...], "summary": {...}, "fetched_at": <iso8601> }
    """
    cached = _special_orders_cache.get("data")
    age = time.time() - _special_orders_cache.get("fetched_at", 0.0)

    if not refresh and cached is not None:
        if age < _SPECIAL_ORDERS_TTL_SECONDS:
            return cached
        # Stale: serve the cached payload now, refresh it after the response.
        background_tasks.add_task(_refresh_special_orders_in_background)
        return cached

    # Cold start or an explicit Sync: block for a fresh walk (deduped via the lock).
    try:
        return _rebuild_special_orders_cache(force=refresh)
    except Exception as e:
        print(f"Error building special-order dashboard: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _refresh_special_orders_after_write() -> None:
    """After a write that only changes the Shopify/matching side (ETA edit, manual
    match/unmatch): rebuild the cached dashboard payload cheaply by re-running matching over
    the last full walk's normalized rows — no Lightspeed re-walk. Falls back to just marking
    the cache stale (so the old bug of serving a pre-write payload can't recur silently)."""
    from app.services import special_order_service

    fresh = None
    try:
        fresh = special_order_service.re_enrich_dashboard()
    except Exception as e:
        print(f"Fast special-order re-enrich failed: {e}")
    if fresh is not None:
        fresh["fetched_at"] = datetime.utcnow().isoformat() + "Z"
        _special_orders_cache["data"] = fresh
        _special_orders_cache["fetched_at"] = time.time()
    else:
        _special_orders_cache["data"] = None
        _special_orders_cache["fetched_at"] = 0.0


@app.post("/api/special-orders/eta")
def update_special_order_eta(payload: Dict[str, Any]):
    """
    Writes the customer-promised ETA back to Shopify (the `custom.special_order_eta` order
    metafield) via the Admin API, then rebuilds the special-order cache so the change is live
    on the next read. A null/empty `eta` CLEARS the metafield (removes the promise date).
    Body: { shopify_order_id, eta (YYYY-MM-DD or null), updated_by? }.
    """
    from app.services import special_order_service
    from app.services.shopify_client import ShopifyClient
    from app.services.bigquery_sync import log_shopify_eta_writeback

    order_id = str(payload.get("shopify_order_id") or "").strip()
    eta = payload.get("eta") or None
    if not order_id:
        raise HTTPException(status_code=400, detail="shopify_order_id is required")
    if eta is not None:
        try:
            datetime.strptime(eta, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="eta must be an ISO date (YYYY-MM-DD)")

    triggered_by = payload.get("updated_by") or ("UI_Manual_Clear" if eta is None else "UI_Manual_Edit")
    try:
        if eta is None:
            ShopifyClient().delete_order_eta(order_id)
        else:
            ShopifyClient().set_order_eta(order_id, eta)
    except Exception as e:
        log_shopify_eta_writeback({
            "shopify_order_id": order_id,
            "new_eta": eta,
            "triggered_by": triggered_by,
            "status": "failed",
            "error_message": str(e),
            "created_at": datetime.utcnow().isoformat(),
        })
        raise HTTPException(status_code=502, detail="Shopify update failed")

    # Read and write share one source of truth: drop the Shopify pull cache, then rebuild the
    # response payload from the cached Lightspeed rows so the next GET is both fresh AND cheap.
    special_order_service.invalidate_shopify_cache()
    _refresh_special_orders_after_write()

    log_shopify_eta_writeback({
        "shopify_order_id": order_id,
        "new_eta": eta,
        "triggered_by": triggered_by,
        "status": "success",
        "error_message": None,
        "created_at": datetime.utcnow().isoformat(),
    })
    return {"status": "success", "shopify_order_id": order_id, "eta": eta}


def _so_override_request(payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    """Shared body for the manual match/unmatch endpoints: persist the override, then
    rebuild the cached dashboard so the pairing is live on the next read."""
    from app.services.bigquery_sync import save_so_match_override

    special_order_id = str(payload.get("special_order_id") or "").strip()
    shopify_order_id = str(payload.get("shopify_order_id") or "").strip()
    if not special_order_id:
        raise HTTPException(status_code=400, detail="special_order_id is required")
    if not shopify_order_id:
        raise HTTPException(status_code=400, detail="shopify_order_id is required")

    try:
        save_so_match_override(
            special_order_id,
            shopify_order_id,
            action,
            created_by=payload.get("updated_by") or "UI_Manual_Match",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to save match override")

    _refresh_special_orders_after_write()
    return {
        "status": "success",
        "action": action,
        "special_order_id": special_order_id,
        "shopify_order_id": shopify_order_id,
    }


@app.post("/api/special-orders/match")
def match_special_order_manual(payload: Dict[str, Any]):
    """
    Manually links an LS special order to a Shopify order (overrides auto-matching; the
    newest link for an SO supersedes older ones). Body: { special_order_id,
    shopify_order_id, updated_by? }.
    """
    return _so_override_request(payload, "link")


@app.post("/api/special-orders/unmatch")
def unmatch_special_order_manual(payload: Dict[str, Any]):
    """
    Manually forbids an LS SO <-> Shopify order pairing (undoes a manual or automatic
    match; auto-matching will never propose that pair again). Body: { special_order_id,
    shopify_order_id, updated_by? }.
    """
    return _so_override_request(payload, "unlink")
