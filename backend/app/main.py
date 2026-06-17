from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, UploadFile, File, HTTPException
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
        if adjustment_mode not in ("shrink", "grow"):
            raise HTTPException(status_code=400, detail="adjustment_mode must be 'shrink' or 'grow'.")

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
            momentum_data={}
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
        
        # 4. Save NEW snapshots and run info to BigQuery
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
        log_recommendation_run(run_log)

        snapshots = []
        for rec in recommendations:
            snapshots.append({
                "system_id": str(rec['system_id']),
                "sku": str(rec['sku']),
                "location": rec['location'],
                "daily_sales": float(rec['raw_daily_sales'] if 'raw_daily_sales' in rec else rec['daily_sales']),
                "created_at": datetime.utcnow().isoformat()
            })
        log_velocity_snapshots(snapshots)
        
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
