from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.db.database import engine, Base, get_db
from app.db.models import VelocitySnapshot, RecommendationRun, WritebackLog, ManagedSKU
from sqlalchemy import desc, func
from app.services import google_sheets
from app.services.bigquery_sync import (
    log_recommendation_run, log_velocity_snapshots, log_writeback,
    get_managed_skus, upsert_managed_skus, get_sku_overrides, upsert_sku_override,
    get_recommendation_runs, get_writeback_logs
)
import pandas as pd
import io
import uuid
from datetime import datetime
from typing import List, Dict, Any
from fastapi.responses import Response, RedirectResponse

# Database initialization (Optional for local dev, logs to BQ in prod)
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Skipping local DB initialization: {e}")

app = FastAPI(title="SKU Reorder Point Automation API")

# Setup CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Update this in production
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

@app.get("/api/replenishment/data")
def get_replenishment_data(forecast_period: int = None, safety_days: int = 7, growth_multiplier: float = 1.0):
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        # 1. Fetch Google Sheet Data
        raw_data = google_sheets.fetch_sheet_data(spreadsheet_id)
        
        # 1.5 Fetch BigQuery Metrics and Overrides
        from app.services.bigquery_sync import get_cached_bq_metrics
        bq_metrics = get_cached_bq_metrics()
        overrides = get_sku_overrides()

        # 2. Process Recommendations
        from app.services.google_sheets import process_recommendations
        recommendations = process_recommendations(
            raw_data, 
            safety_days=safety_days, 
            override_forecast=forecast_period,
            growth_multiplier=growth_multiplier,
            bq_metrics=bq_metrics
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
            if rec['location'] in by_location:
                by_location[rec['location']].append(rec)
                
        return {
            "status": "success",
            "run_id": run_id,
            "locations": ["Bici Adanac", "Victoria", "Langford"],
            "data": by_location
        }
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
        log_data = {
            "sku": str(update.get('sku')),
            "location_id": str(update.get('location')),
            "old_reorder_point": int(update.get('current_reorder_point') or 0),
            "new_reorder_point": int(update.get('recommended_reorder_point') or 0),
            "old_desired_inventory": int(update.get('current_desired_level') or 0),
            "new_desired_inventory": int(update.get('recommended_desired_level') or 0),
            "triggered_by": "UI_Manual_Push",
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
def get_writeback_logs(limit: int = 100):
    logs = get_writeback_logs(limit)
    formatted_logs = []
    for log in logs:
        rop_changed = log.get('new_reorder_point') != log.get('old_reorder_point')
        formatted_logs.append({
            "id": str(uuid.uuid4()),
            "timestamp": log.get('created_at').isoformat() if hasattr(log.get('created_at'), 'isoformat') else str(log.get('created_at')),
            "user": log.get('triggered_by'),
            "sku": log.get('sku'),
            "location": log.get('location_id'),
            "field": "reorder_point" if rop_changed else "desired_level",
            "oldValue": log.get('old_reorder_point'),
            "newValue": log.get('new_reorder_point'),
            "status": log.get('status'),
            "errorMessage": log.get('error_message')
        })
    return formatted_logs

@app.post("/api/replenishment/override")
def save_override(override: Dict[str, Any]):
    upsert_sku_override(override)
    return {"status": "success"}

@app.get("/api/replenishment/vendor-lead-times")
def get_vendor_lead_times():
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        data = google_sheets.fetch_vendor_lead_times(spreadsheet_id)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health/lightspeed")
def check_lightspeed_health():
    from app.services.lightspeed_client import LightspeedClient
    client = LightspeedClient()
    is_connected = client.check_health()
    if is_connected:
        return {"status": "connected"}
    else:
        raise HTTPException(status_code=503, detail="Disconnected from Lightspeed")

@app.get("/api/health/bigquery")
def check_bigquery_health():
    from app.services.bigquery_sync import client as bq_client
    try:
        # Force an actual API call by iterating over the results
        datasets = list(bq_client.list_datasets(max_results=1))
        return {"status": "connected"}
    except Exception as e:
        print(f"BigQuery health check failed: {e}")
        raise HTTPException(status_code=503, detail="Disconnected from BigQuery")

@app.get("/api/health/sheets")
def check_sheets_health():
    import concurrent.futures
    try:
        from app.services.google_sheets import get_gspread_client
        
        # Use a thread pool to enforce a timeout on the gspread initialization
        # which can hang if it tries to refresh tokens on a slow network
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(get_gspread_client)
            client = future.result(timeout=5) # 5 second timeout
            
        if client:
            return {"status": "connected"}
        else:
            raise Exception("Failed to initialize gspread client")
    except Exception as e:
        print(f"Google Sheets health check failed: {e}")
        raise HTTPException(status_code=503, detail="Disconnected from Google Sheets")

@app.get("/api/replenishment/ls-link/{system_id}")
def get_lightspeed_link(system_id: str):
    from app.services.lightspeed_client import LightspeedClient
    client = LightspeedClient()
    try:
        items = client.get_item_by_sku(system_id)
        if not items:
            raise HTTPException(status_code=404, detail=f"Item with SKU {system_id} not found")
        item_id = items[0].get("itemID")
        ls_url = f"https://us.merchantos.com/?name=item.views.item.edit&id={item_id}"
        return RedirectResponse(url=ls_url)
    except Exception as e:
        search_url = f"https://us.merchantos.com/?name=item.views.item.edit&id={system_id}"
        return RedirectResponse(url=search_url)
