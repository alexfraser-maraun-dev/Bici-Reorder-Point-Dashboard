from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.db.database import engine, Base, get_db
from app.db.models import VelocitySnapshot, RecommendationRun, WritebackLog, ManagedSKU
from sqlalchemy import desc, func
from app.services import google_sheets
import pandas as pd
import io
from typing import List, Dict, Any

# Create database tables
Base.metadata.create_all(bind=engine)

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

from fastapi.responses import Response

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
def get_managed_skus(db: Session = Depends(get_db)):
    skus = db.query(ManagedSKU).all()
    # Format to match the frontend expectations
    return [{
        "id": str(sku.id),
        "sku": sku.sku,
        "product": sku.product or "Unknown Product",
        "brand": sku.brand or "Unknown",
        "vendor": sku.vendor or "Unknown",
        "category": sku.category or "Unknown",
        "active": sku.active,
        "addedAt": sku.created_at.isoformat() if sku.created_at else None,
        "addedBy": sku.added_by or "System"
    } for sku in skus]

@app.post("/api/skus/upload")
async def upload_skus_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed")
    
    try:
        contents = await file.read()
        df = pd.read_csv(io.BytesIO(contents))
        
        # Check required columns (at minimum we need 'sku' or 'system_sku')
        sku_col = None
        for col in ['sku', 'system_sku', 'SKU', 'System SKU']:
            if col in df.columns:
                sku_col = col
                break
                
        if not sku_col:
            raise HTTPException(status_code=400, detail="CSV must contain a 'sku' or 'system_sku' column")
            
        added_count = 0
        updated_count = 0
        
        for _, row in df.iterrows():
            sku_val = str(row[sku_col]).strip()
            if not sku_val or sku_val == 'nan':
                continue
                
            # Safely get optional fields
            item_id = str(row.get('item_id', ''))
            product = str(row.get('product', row.get('description', '')))
            brand = str(row.get('brand', row.get('manufacturer', '')))
            vendor = str(row.get('vendor', ''))
            category = str(row.get('category', ''))
            
            existing = db.query(ManagedSKU).filter(ManagedSKU.sku == sku_val).first()
            if existing:
                existing.product = product if product and product != 'nan' else existing.product
                existing.brand = brand if brand and brand != 'nan' else existing.brand
                existing.vendor = vendor if vendor and vendor != 'nan' else existing.vendor
                existing.category = category if category and category != 'nan' else existing.category
                existing.active = True
                updated_count += 1
            else:
                new_sku = ManagedSKU(
                    sku=sku_val,
                    item_id=item_id if item_id and item_id != 'nan' else None,
                    product=product if product and product != 'nan' else None,
                    brand=brand if brand and brand != 'nan' else None,
                    vendor=vendor if vendor and vendor != 'nan' else None,
                    category=category if category and category != 'nan' else None,
                    active=True,
                    added_by="CSV Import"
                )
                db.add(new_sku)
                added_count += 1
                
        db.commit()
        return {"status": "success", "added": added_count, "updated": updated_count}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sync/sheets")
async def sync_from_google_sheets(db: Session = Depends(get_db)):
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        data = google_sheets.fetch_sheet_data(spreadsheet_id)
        
        # Identify locations from headers
        locations = set()
        for key in data[0].keys():
            if "|" in key:
                locations.add(key.split("|")[0])
        
        structured_data = google_sheets.get_location_metrics(data, list(locations))
        
        # For now, let's just return the count and first few items to verify
        return {
            "status": "success", 
            "rowCount": len(structured_data),
            "locationsFound": list(locations),
            "sample": structured_data[:2]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sync from Google Sheets: {str(e)}")
@app.get("/api/replenishment/data")
async def get_replenishment_data(forecast_period: int = None, safety_days: int = 7, growth_multiplier: float = 1.0, db: Session = Depends(get_db)):
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        # Create a new run record
        new_run = RecommendationRun(run_type="manual", status="running")
        db.add(new_run)
        db.commit()
        
        # 1. Fetch data from Google Sheets
        data = google_sheets.fetch_sheet_data(spreadsheet_id)
        
        # 2. Get previous velocity snapshots
        momentum_data = {}
        prev_snapshots = db.query(VelocitySnapshot).all()
        for s in prev_snapshots:
            momentum_data[f"{s.system_id}|{s.location}"] = s.daily_sales
            
        # 3. Process recommendations
        recommendations = google_sheets.process_recommendations(
            data, 
            safety_days=safety_days, 
            override_forecast=forecast_period,
            growth_multiplier=growth_multiplier,
            momentum_data=momentum_data
        )
        
        # 4. Save NEW snapshots
        for rec in recommendations:
            new_snap = VelocitySnapshot(
                system_id=rec['system_id'],
                location=rec['location'],
                daily_sales=rec['raw_daily_sales'] if 'raw_daily_sales' in rec else rec['daily_sales']
            )
            db.add(new_snap)
        
        # Update run status
        new_run.status = "completed"
        new_run.row_count = len(recommendations)
        new_run.completed_at = func.now()
        db.commit()
        
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
            "run_id": new_run.id,
            "locations": ["Bici Adanac", "Victoria", "Langford"],
            "data": by_location
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/replenishment/push")
async def push_replenishment_updates(updates: List[Dict[str, Any]], db: Session = Depends(get_db)):
    """
    Pushes a list of SKU recommendations to Lightspeed and logs results.
    """
    from app.services.lightspeed_client import LightspeedClient
    from app.db.models import WritebackLog
    
    client = LightspeedClient()
    results = []
    
    for update in updates:
        success = client.sync_recommendation(update)
        
        # Log to database
        log_entry = WritebackLog(
            sku=update.get('sku'),
            location_id=update.get('location'),
            old_reorder_point=update.get('current_reorder_point'),
            new_reorder_point=update.get('recommended_reorder_point'),
            old_desired_inventory=update.get('current_desired_level'),
            new_desired_inventory=update.get('recommended_desired_level'),
            status="success" if success else "failed",
            triggered_by="UI_Manual_Push"
        )
        db.add(log_entry)
        results.append({"sku": update.get('sku'), "success": success})
        
    db.commit()
    return {"status": "completed", "results": results}

@app.get("/api/replenishment/logs")
async def get_writeback_logs(limit: int = 100, db: Session = Depends(get_db)):
    from app.db.models import WritebackLog
    logs = db.query(WritebackLog).order_by(desc(WritebackLog.created_at)).limit(limit).all()
    # Format for frontend
    return [{
        "id": log.id,
        "timestamp": log.created_at.isoformat(),
        "user": log.triggered_by,
        "sku": log.sku,
        "location": log.location_id,
        "field": "reorder_point" if log.new_reorder_point != log.old_reorder_point else "desired_level",
        "oldValue": log.old_reorder_point if log.new_reorder_point != log.old_reorder_point else log.old_desired_inventory,
        "newValue": log.new_reorder_point if log.new_reorder_point != log.old_reorder_point else log.new_desired_inventory,
        "status": log.status,
        "errorMessage": log.error_message
    } for log in logs]

@app.get("/api/replenishment/runs")
async def get_recommendation_runs(limit: int = 50, db: Session = Depends(get_db)):
    from app.db.models import RecommendationRun, RecommendationRow
    from sqlalchemy import func
    
    runs = db.query(RecommendationRun).order_by(desc(RecommendationRun.started_at)).limit(limit).all()
    
    results = []
    for run in runs:
        # Calculate summaries from recommendation rows
        summary = db.query(
            func.count(RecommendationRow.id).label("total"),
            func.sum(func.cast(RecommendationRow.changed_flag, Integer)).label("changed"),
            func.sum(func.cast(RecommendationRow.needs_order, Integer)).label("needs_order")
        ).filter(RecommendationRow.run_id == run.id).first()
        
        duration = "N/A"
        if run.completed_at and run.started_at:
            delta = run.completed_at - run.started_at
            duration = f"{delta.total_seconds():.1f}s"
            
        results.append({
            "id": str(run.id),
            "runDate": run.started_at.isoformat(),
            "type": run.run_type,
            "triggeredBy": run.triggered_by or "System",
            "status": run.status,
            "totalRows": summary.total if summary and summary.total else (run.row_count or 0),
            "changedRows": summary.changed if summary and summary.changed else 0,
            "needsOrderCount": summary.needs_order if summary and summary.needs_order else 0,
            "duration": duration,
            "trailingDays": 30, # Defaulting for now as it's not in the run model
            "forecastDays": 60,
            "safetyDays": 7
        })
    return results

@app.get("/api/replenishment/vendor-lead-times")
async def get_vendor_lead_times():
    spreadsheet_id = "1awrwQd7D_XFq0R6n03kSxMMPsyrU0rVBCjLC_u7-5ak"
    try:
        data = google_sheets.fetch_vendor_lead_times(spreadsheet_id)
        return {
            "status": "success",
            "data": data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
