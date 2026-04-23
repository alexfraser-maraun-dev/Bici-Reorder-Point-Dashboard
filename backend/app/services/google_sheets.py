import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os
import json
import math
from typing import List, Dict, Any

# Scopes required for Google Sheets and Drive
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

TOKEN_FILE = 'token.json'

def get_gspread_client():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    elif os.getenv("GOOGLE_REFRESH_TOKEN"):
        # Headless production authentication
        creds = Credentials(
            token=None,
            refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=SCOPES
        )
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_id = os.getenv("GOOGLE_CLIENT_ID")
            client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
            
            if not client_id or not client_secret:
                raise Exception("GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not found in .env")
            
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8085/"]
                }
            }
            
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=8085, prompt='consent')
            
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return gspread.authorize(creds)

def clean_float(val: Any) -> float:
    try:
        if not val or str(val).strip() == "" or str(val).strip() == "-":
            return 0.0
        # Remove commas, currency symbols, and percentage signs
        clean_val = str(val).replace(",", "").replace("$", "").replace("%", "").strip()
        return float(clean_val)
    except:
        return 0.0

def fetch_sheet_data(spreadsheet_id: str) -> List[Dict[str, Any]]:
    client = get_gspread_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    # The main replenishment sheet is usually the first one (index 0)
    worksheet = spreadsheet.get_worksheet(0)
    
    all_values = worksheet.get_all_values()
    if len(all_values) < 3:
        return []

    row1 = all_values[0] # Locations
    row2 = all_values[1] # Metrics
    data_rows = all_values[2:]

    headers = []
    current_location = ""
    
    for i in range(len(row2)):
        loc = row1[i].strip() if i < len(row1) else ""
        if loc:
            current_location = loc
        
        metric = row2[i].strip()
        
        if current_location and current_location not in ["", "Multi-Shop Store"]:
            headers.append(f"{current_location}|{metric}")
        else:
            headers.append(metric)

    parsed_data = []
    for row in data_rows:
        item = {}
        for i in range(len(headers)):
            val = row[i] if i < len(row) else ""
            item[headers[i]] = val
        parsed_data.append(item)
        
    return parsed_data

def fetch_vendor_lead_times(spreadsheet_id: str) -> List[Dict[str, Any]]:
    client = get_gspread_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    # GID 1291728188 - we'll find the worksheet by that ID or search by common names
    try:
        worksheet = None
        for ws in spreadsheet.worksheets():
            if str(ws.id) == "1291728188" or ws.title.strip() == "Vendor Lead Times":
                worksheet = ws
                break
        
        if not worksheet:
            return []
            
        all_values = worksheet.get_all_values()
        # Based on inspection, data starts at row 5 (index 4)
        if len(all_values) < 5:
            return []
            
        vendors = []
        for row in all_values[4:]:
            if not row or not row[0]: continue 
            vendors.append({
                "vendor": row[0],
                "adanac_lead": row[1],
                "adanac_pos": row[2],
                "langford_lead": row[3],
                "langford_pos": row[4],
                "victoria_lead": row[5],
                "victoria_pos": row[6]
            })
        return vendors
    except Exception as e:
        print(f"Error fetching vendor lead times: {e}")
        return []

def process_recommendations(parsed_data: List[Dict[str, Any]], safety_days: int = 7, override_forecast: int = None, growth_multiplier: float = 1.0, momentum_data: Dict[str, float] = None) -> List[Dict[str, Any]]:
    """
    Applies the custom BICI calculation logic to the spreadsheet data.
    """
    locations = ["Bici Adanac", "Langford", "Victoria"]
    # Mapping location to their specific lead time column
    lead_time_map = {
        "Bici Adanac": "Vancouver Lead Time",
        "Victoria": "Victoria Lead Time",
        "Langford": "Langford Lead Time"
    }
    
    recommendations = []
    
    for row in parsed_data:
        system_id = row.get("Item System ID")
        if not system_id or str(system_id).strip() == "":
            continue
            
        # Use provided forecast override or fall back to sheet
        forecast_period = override_forecast if override_forecast else clean_float(row.get("Dynamic Reorder Forecast Period", 60))
        
        for loc in locations:
            # Objective Measures
            raw_daily_sales = clean_float(row.get(f"{loc}|Dynamic Reorder Trailing Average Daily Sales"))
            
            # Apply growth multiplier
            daily_sales = raw_daily_sales * growth_multiplier
            
            on_hand = clean_float(row.get(f"{loc}|Item Metrics Quantity on Hand"))
            on_order = clean_float(row.get(f"{loc}|Item Metrics Quantity on Order"))
            
            # Forecasts from sheet (assuming they exist or using daily sales)
            # If not explicitly in sheet, we calculate them:
            forecast_30d = daily_sales * 30
            forecast_60d = daily_sales * 60
            
            # Lead Time (using the independent columns A, B, C)
            lead_time_col = lead_time_map.get(loc)
            lead_time = clean_float(row.get(lead_time_col))
            
            # CALCULATION
            # New Reorder Point = (Sales * Lead Time) + Safety Stock
            safety_stock = math.ceil(daily_sales * safety_days)
            new_reorder_point = math.ceil((daily_sales * lead_time) + safety_stock)
            
            # New Desired Level = Sales * Forecast Period
            new_desired_level = math.ceil(daily_sales * forecast_period)
            
            # Current Levels in Lightspeed (from the sheet)
            current_rp = clean_float(row.get(f"{loc}|Item Metrics Reorder Point"))
            current_dl = clean_float(row.get(f"{loc}|Item Metrics Desired Inventory Level"))
            
            # Sale Line Margin and other objective measures
            margin = row.get(f"{loc}|Sale Line Margin", "0%")
            qty_sold = clean_float(row.get(f"{loc}|Sale Line Quantity Sold"))

            # Calculate Urgency (Gradient rating) - PROACTIVE CALIBRATION
            urgency = 0
            if new_desired_level > 0 and new_reorder_point > 0:
                if on_hand >= (new_desired_level * 0.8): 
                    urgency = 1 # Optimal (Stocked near Desired Level)
                elif on_hand > (new_reorder_point * 1.15):
                    urgency = 2 # Healthy (Safe buffer above ROP)
                elif on_hand > new_reorder_point:
                    urgency = 3 # Warning (Approaching ROP - Order Soon)
                elif on_hand > (new_reorder_point * 0.5):
                    urgency = 4 # Low Stock (Dipping into Safety Stock)
                else:
                    urgency = 5 # Critical (Dangerously low)
            elif new_reorder_point > 0:
                # Fallback if DL is not set
                if on_hand > (new_reorder_point * 1.15): urgency = 2
                elif on_hand > new_reorder_point: urgency = 3
                else: urgency = 5
            
            # Momentum Indicator
            momentum = "stable"
            if momentum_data:
                key = f"{system_id}|{loc}"
                prev_velocity = momentum_data.get(key)
                if prev_velocity is not None and prev_velocity > 0:
                    diff = (daily_sales - prev_velocity) / prev_velocity
                    if diff > 0.05: momentum = "increasing"
                    elif diff < -0.05: momentum = "decreasing"

            # QTY to Order Calculation
            # Rec. DL - (On Hand + On Order)
            qty_to_order = max(0, int(new_desired_level - (on_hand + on_order)))

            # We now include ALL rows from the sheet as requested
            recommendations.append({
                "system_id": system_id,
                "sku": row.get("Item UPC"),
                "brand": row.get("Item Brand"),
                "description": row.get("Item Description"),
                "category": row.get("Item Category"),
                "vendor": row.get("Item Default Vendor"),
                "location": loc,
                "daily_sales": round(daily_sales, 2),
                "raw_daily_sales": raw_daily_sales,
                "lead_time": lead_time,
                "forecast_period": forecast_period,
                "safety_days": safety_days,
                "forecast_30d": round(forecast_30d, 1),
                "forecast_60d": round(forecast_60d, 1),
                "on_hand": on_hand,
                "on_order": on_order,
                "qty_to_order": qty_to_order,
                "days_stock": round(on_hand / daily_sales, 1) if daily_sales > 0 else 0,
                "qty_sold": qty_sold,
                "margin": margin,
                "urgency": urgency,
                "momentum": momentum,
                "current_reorder_point": int(current_rp),
                "current_desired_level": int(current_dl),
                "recommended_reorder_point": int(new_reorder_point),
                "recommended_desired_level": int(new_desired_level),
                "change_needed": (int(new_reorder_point) != int(current_rp) or 
                                 int(new_desired_level) != int(current_dl))
            })
                
    return recommendations
