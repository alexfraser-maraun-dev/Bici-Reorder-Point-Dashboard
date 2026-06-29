from dotenv import load_dotenv
load_dotenv()

import os
import time
import threading
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
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
from fastapi.responses import Response, RedirectResponse

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
app = FastAPI(title="SKU Reorder Point Automation API")

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


def _warm_replenishment_caches() -> None:
    """Populate the heavy BigQuery-backed caches (tagged item metrics, lead times,
    brand sourcing) on boot so the first Inventory dashboard load reads a hot cache
    instead of running the 60-day stockout query cold. Best-effort; failures here
    just mean the first real request pays the cold cost as before."""
    try:
        from app.services.bigquery_sync import (
            fetch_tagged_items_metrics,
            fetch_lead_times,
            get_brand_sourcing_rules_map,
            fetch_brand_vendor_sourcing,
        )
        fetch_tagged_items_metrics("auto-replen")
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/replenishment/debug/item/{item_id}")
def get_replenishment_item_debug(item_id: int):
    try:
        from app.services.bigquery_sync import get_item_replenishment_debug
        return {"status": "success", "debug": get_item_replenishment_debug(item_id)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/api/skus/upload")
async def upload_skus(file: UploadFile = File(...)):
    contents = await file.read()
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
        raise HTTPException(status_code=500, detail=str(e))

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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/replenishment/brand-sourcing-rules")
def get_brand_sourcing_rules(force_refresh: bool = False):
    try:
        from app.services.bigquery_sync import fetch_brand_sourcing_rules
        data = fetch_brand_sourcing_rules(force_refresh=force_refresh)
        return {"status": "success", "data": to_json_safe(data)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

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
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Demand & Seasonality (forecast visualization layer)
#
# Read-only endpoints that expose the already-built seasonal/forecast signal for
# charting. They visualize the profile and a projected forecast only -- they do
# NOT flip the opt-in seasonality engine on for live ROP/DL writeback.
# ---------------------------------------------------------------------------

@app.get("/api/forecast/seasonal-profiles")
def get_seasonal_profiles(years: int = 3, smoothing: float = 0.0, location: str = None):
    try:
        from app.services.bigquery_sync import fetch_monthly_category_history
        from app.services.forecasting import build_seasonal_profile_response
        df = fetch_monthly_category_history(years=years)
        if location is not None and len(df) and "location_id" in df.columns:
            df = df[df["location_id"].astype(str) == str(location)]
        records = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
        profiles = build_seasonal_profile_response(records, smoothing=smoothing)
        return {
            "status": "success",
            "data": to_json_safe(profiles),
            "meta": {"years": years, "category_count": len(profiles), "location": location},
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/forecast/history")
def get_demand_history(
    scope: str,
    id: str,
    location: str = None,
    years: int = 3,
    horizon_months: int = 12,
    lead_time_days: float = 14.0,
    coverage_days: float = 30.0,
):
    """Monthly sales history + forward forecast for a category or a single SKU.

    The forecast is a flat monthly baseline scaled by the (blended, for SKUs)
    seasonal index per month. ``lead_time_window`` marks the months a PO placed
    now would cover, so the chart can shade "buy ahead of the ramp".
    """
    if scope not in ("category", "sku"):
        raise HTTPException(status_code=400, detail="scope must be 'category' or 'sku'")
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
            grouped = frame.groupby("month_of_year")["total_units_sold"].sum()
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
                rows.groupby(["sales_year", "month_of_year"])["total_units_sold"].sum()
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
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Purchase Order Master Dashboard
# ---------------------------------------------------------------------------

@app.post("/api/po/draft")
def create_po_draft_endpoint(payload: Dict[str, Any]):
    """
    Builds PO drafts from selected recommendation rows, grouped by vendor + shop
    and reconciled against currently-open Lightspeed POs so repeated runs don't
    create duplicates. Body: {"recommendations": [...], "created_by": "..."}.
    """
    recs = payload.get("recommendations") or []
    if not recs:
        raise HTTPException(status_code=400, detail="No recommendations provided.")
    created_by = payload.get("created_by") or "UI_User"
    try:
        from app.services.lightspeed_client import LightspeedClient
        from app.services.po_service import reconcile_recommendations
        from app.services.bigquery_sync import create_po_draft

        client = LightspeedClient()
        drafts = reconcile_recommendations(recs, client, created_by=created_by)
        for draft in drafts:
            lines = draft["lines"]
            header = {k: v for k, v in draft.items() if k != "lines"}
            create_po_draft(header, lines)
        return {"status": "success", "drafts": to_json_safe(drafts)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to create PO drafts.")

@app.get("/api/po/drafts")
def list_po_drafts(status: str = None):
    try:
        from app.services.bigquery_sync import get_po_drafts
        return {"status": "success", "data": to_json_safe(get_po_drafts(status))}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch PO drafts.")

@app.get("/api/po/draft/{draft_id}")
def get_po_draft_endpoint(draft_id: str):
    try:
        from app.services.bigquery_sync import get_po_draft
        draft = get_po_draft(draft_id)
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
    """Replaces the line items on a draft. Body: {"lines": [...]}."""
    lines = payload.get("lines")
    if lines is None:
        raise HTTPException(status_code=400, detail="lines is required.")
    try:
        from app.services.bigquery_sync import update_po_draft_lines
        normalized = []
        for line in lines:
            normalized.append({
                "draft_id": draft_id,
                "sku": line.get("sku"),
                "item_id": str(line.get("item_id")) if line.get("item_id") is not None else None,
                "location_id": str(line.get("location_id")) if line.get("location_id") is not None else None,
                "quantity": _safe_int(line.get("quantity")),
                "unit_cost": line.get("unit_cost"),
                "source": line.get("source") or "manual",
                "recommendation_run_id": line.get("recommendation_run_id"),
                "reconciliation": line.get("reconciliation"),
                "target_lightspeed_order_id": line.get("target_lightspeed_order_id"),
            })
        update_po_draft_lines(draft_id, normalized)
        return {"status": "success"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to update PO draft.")

@app.delete("/api/po/draft/{draft_id}")
def delete_po_draft_endpoint(draft_id: str):
    try:
        from app.services.bigquery_sync import delete_po_draft
        delete_po_draft(draft_id)
        return {"status": "success"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to delete PO draft.")

@app.get("/api/po/open-orders")
def list_open_orders(vendor_id: str = None, shop_id: str = None):
    """Lists open (unsent) Lightspeed POs with their line quantities."""
    try:
        from app.services.lightspeed_client import LightspeedClient
        client = LightspeedClient()
        orders = client.get_open_orders(vendor_id=vendor_id, shop_id=shop_id)
        return {"status": "success", "data": to_json_safe(orders)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch open orders.")

@app.post("/api/po/push/{draft_id}")
def push_po_draft_endpoint(draft_id: str, payload: Dict[str, Any] = None):
    """
    Pushes a draft to Lightspeed: creates a new PO and/or appends/top-ups lines on
    existing open POs per each line's reconciliation. Idempotent — a draft already
    pushed is rejected, and open-PO state is re-checked at push time.
    """
    triggered_by = (payload or {}).get("pushed_by") or "UI_User"
    try:
        from app.services.lightspeed_client import LightspeedClient
        from app.services.po_service import push_draft
        from app.services.bigquery_sync import (
            get_po_draft, log_po_push, update_po_draft_status,
        )

        draft = get_po_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found.")
        if draft.get("status") == "pushed":
            raise HTTPException(status_code=409, detail="Draft has already been pushed.")

        client = LightspeedClient()
        audit = push_draft(draft, client, triggered_by=triggered_by)
        log_po_push(audit)

        failed = any(row.get("status") == "failed" for row in audit)
        new_status = "failed" if failed else "pushed"
        pushed_order_id = next(
            (row.get("lightspeed_order_id") for row in audit
             if row.get("status") == "success" and row.get("lightspeed_order_id")),
            None,
        )
        update_po_draft_status(draft_id, new_status, pushed_order_id)
        return {"status": new_status, "results": to_json_safe(audit)}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to push PO draft.")

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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/special-orders/eta")
def update_special_order_eta(payload: Dict[str, Any]):
    """
    Writes the customer-promised ETA back to Shopify (the `custom.special_order_eta` order
    metafield) via the Admin API, then busts the special-order caches so the change is live on
    the next read. Body: { shopify_order_id, eta (YYYY-MM-DD), updated_by? }.
    """
    from app.services import special_order_service
    from app.services.shopify_client import ShopifyClient
    from app.services.bigquery_sync import log_shopify_eta_writeback

    order_id = str(payload.get("shopify_order_id") or "").strip()
    eta = payload.get("eta")
    if not order_id:
        raise HTTPException(status_code=400, detail="shopify_order_id is required")
    if not eta:
        raise HTTPException(status_code=400, detail="eta is required")
    try:
        datetime.strptime(eta, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="eta must be an ISO date (YYYY-MM-DD)")

    try:
        ShopifyClient().set_order_eta(order_id, eta)
    except Exception as e:
        log_shopify_eta_writeback({
            "shopify_order_id": order_id,
            "new_eta": eta,
            "triggered_by": payload.get("updated_by") or "UI_Manual_Edit",
            "status": "failed",
            "error_message": str(e),
            "created_at": datetime.utcnow().isoformat(),
        })
        raise HTTPException(status_code=502, detail=f"Shopify update failed: {e}")

    # Read and write now share one source of truth: drop the Shopify pull cache and the
    # special-orders response cache so the next GET re-pulls the just-written value live.
    special_order_service.invalidate_shopify_cache()
    _special_orders_cache["fetched_at"] = 0.0

    log_shopify_eta_writeback({
        "shopify_order_id": order_id,
        "new_eta": eta,
        "triggered_by": payload.get("updated_by") or "UI_Manual_Edit",
        "status": "success",
        "error_message": None,
        "created_at": datetime.utcnow().isoformat(),
    })
    return {"status": "success", "shopify_order_id": order_id, "eta": eta}
