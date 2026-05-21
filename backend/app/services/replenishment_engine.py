import math
from typing import List, Dict, Any

def process_recommendations(
    items_df_dict: List[Dict[str, Any]], 
    lead_times_df_dict: List[Dict[str, Any]], 
    safety_days: int = 7, 
    override_forecast: int = None, 
    growth_multiplier: float = 1.0, 
    recent_30d_weight: float = 0.70,
    adjustment_mode: str = "shrink",
    momentum_data: Dict[str, float] = None
) -> List[Dict[str, Any]]:
    """
    Applies the custom BICI calculation logic to the BigQuery data.
    """
    shop_map = {3: "Bici Adanac", 2: "Victoria", 20: "Langford"}

    def normalize_id(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    def normalize_name(value):
        return str(value).strip().lower() if value is not None else None

    def guarded_daily_sales(units_sold, period_days, active_days):
        raw_daily = units_sold / period_days if period_days > 0 else 0
        unbounded_adjusted_daily = units_sold / max(1, active_days)

        if adjustment_mode == "raw":
            return unbounded_adjusted_daily

        if adjustment_mode == "min_days":
            return unbounded_adjusted_daily if active_days >= 7 else raw_daily

        if adjustment_mode == "cap":
            capped_period_demand = min(unbounded_adjusted_daily * period_days, units_sold * 2)
            return capped_period_demand / period_days if period_days > 0 else 0

        confidence = min(1.0, max(0.0, active_days / 7))
        return raw_daily + ((unbounded_adjusted_daily - raw_daily) * confidence)
    
    # Build Lead Time dictionary: (vendor_id, location_id) -> lead_time_days
    # Fallback to a default if not found
    lead_time_dict = {}
    for row in lead_times_df_dict:
        vid = normalize_id(row.get("vendor_id"))
        vendor_name = normalize_name(row.get("vendor_name"))
        lid = normalize_id(row.get("location_id"))
        lt = row.get("lead_time_days")
        if vid is not None and lid is not None and lt is not None:
            lead_time_dict[(vid, lid)] = lt
        if vendor_name and lid is not None and lt is not None:
            lead_time_dict[(vendor_name, lid)] = lt
            
    recommendations = []
    recent_30d_weight = min(1.0, max(0.0, recent_30d_weight))
    prior_30d_weight = 1.0 - recent_30d_weight
    if adjustment_mode not in {"shrink", "min_days", "cap", "raw"}:
        adjustment_mode = "shrink"
    
    for row in items_df_dict:
        system_id = row.get("item_id")
        if not system_id:
            continue
            
        location_id = normalize_id(row.get("location_id"))
        loc_name = shop_map.get(location_id, f"Shop {location_id}")
        vendor_id = normalize_id(row.get("vendor_id"))
        vendor_name = normalize_name(row.get("vendor"))
        
        # Determine lead time
        lead_time = lead_time_dict.get((vendor_id, location_id))
        if lead_time is None:
            lead_time = lead_time_dict.get((vendor_name, location_id), 14.0) # default to 14 days if no history
        
        # Raw Sales Data
        total_units_sold_30 = row.get("total_units_sold_30", 0)
        total_units_sold_60 = row.get("total_units_sold_60", 0)
        days_out_of_stock_30 = row.get("days_out_of_stock_30", 0)
        days_out_of_stock_60 = row.get("days_out_of_stock_60", 0)
        
        active_days_30 = max(1, 30 - days_out_of_stock_30)
        adjusted_daily_sales_30d = guarded_daily_sales(total_units_sold_30, 30, active_days_30)
        
        active_days_60 = max(1, 60 - days_out_of_stock_60)
        adjusted_daily_sales_60d = guarded_daily_sales(total_units_sold_60, 60, active_days_60)

        prior_units_sold_30 = max(0, total_units_sold_60 - total_units_sold_30)
        prior_days_out_of_stock_30 = min(30, max(0, days_out_of_stock_60 - days_out_of_stock_30))
        prior_active_days_30 = max(1, 30 - prior_days_out_of_stock_30)
        adjusted_daily_sales_prior_30d = guarded_daily_sales(prior_units_sold_30, 30, prior_active_days_30)
        
        # Blend recent and prior 30-day velocities for stability plus reactivity.
        base_daily_sales = (
            adjusted_daily_sales_30d * recent_30d_weight
            + adjusted_daily_sales_prior_30d * prior_30d_weight
        )
        
        # Apply Growth Multiplier (only affects forward-looking calcs)
        adjusted_daily_sales = base_daily_sales * growth_multiplier
        
        # Inventory Levels
        on_hand = row.get("current_qoh", 0)
        on_order = row.get("on_order", 0)
        current_rp = row.get("current_reorder_point", 0)
        current_dl = row.get("current_desired_level", 0)
        
        # Use provided forecast override or fall back to a default (e.g. 60 days)
        forecast_period = override_forecast if override_forecast else 60
        
        # CALCULATION
        # New Reorder Point = (Sales * Lead Time) + Safety Stock
        safety_stock = math.ceil(adjusted_daily_sales * safety_days)
        new_reorder_point = math.ceil((adjusted_daily_sales * lead_time) + safety_stock)
        
        # New Desired Level = Sales * Forecast Period
        new_desired_level = math.ceil(adjusted_daily_sales * forecast_period)
        
        # QTY to Order Calculation
        qty_to_order = max(0, int(new_desired_level - (on_hand + on_order)))
        
        # Calculate Urgency
        urgency = 0
        if new_desired_level > 0 and new_reorder_point > 0:
            if on_hand >= (new_desired_level * 0.8): 
                urgency = 1
            elif on_hand > (new_reorder_point * 1.15):
                urgency = 2
            elif on_hand > new_reorder_point:
                urgency = 3
            elif on_hand > (new_reorder_point * 0.5):
                urgency = 4
            else:
                urgency = 5
        elif new_reorder_point > 0:
            if on_hand > (new_reorder_point * 1.15): urgency = 2
            elif on_hand > new_reorder_point: urgency = 3
            else: urgency = 5
            
        # Momentum Indicator
        momentum = "stable"
        if momentum_data:
            key = f"{system_id}|{loc_name}"
            prev_velocity = momentum_data.get(key)
            if prev_velocity is not None and prev_velocity > 0:
                diff = (base_daily_sales - prev_velocity) / prev_velocity
                if diff > 0.05: momentum = "increasing"
                elif diff < -0.05: momentum = "decreasing"
                
        margin = "0%"

        recommendations.append({
            "system_id": system_id,
            "sku": row.get("sku"),
            "brand": row.get("brand"),
            "description": row.get("description"),
            "category": row.get("category"),
            "vendor": row.get("vendor"),
            "location": loc_name,
            "daily_sales": round(base_daily_sales, 2), # Weighted base velocity
            "adjusted_daily_sales": round(adjusted_daily_sales, 2), # Velocity w/ multiplier
            "days_out_of_stock": days_out_of_stock_60,
            "raw_daily_sales": base_daily_sales,
            "adjusted_daily_sales_30d": round(adjusted_daily_sales_30d, 3),
            "adjusted_daily_sales_prior_30d": round(adjusted_daily_sales_prior_30d, 3),
            "adjusted_daily_sales_60d": round(adjusted_daily_sales_60d, 3),
            "recent_30d_weight": round(recent_30d_weight, 2),
            "prior_30d_weight": round(prior_30d_weight, 2),
            "adjustment_mode": adjustment_mode,
            "lead_time": lead_time,
            "forecast_period": forecast_period,
            "safety_days": safety_days,
            "raw_units_sold_30d": total_units_sold_30,
            "raw_units_sold_60d": total_units_sold_60,
            "forecast_30d": round(adjusted_daily_sales_30d * 30, 1), # Stockout-adjusted 30d demand
            "forecast_prior_30d": round(adjusted_daily_sales_prior_30d * 30, 1),
            "forecast_60d": round(adjusted_daily_sales_60d * 60, 1), # Stockout-adjusted 60d demand
            "on_hand": on_hand,
            "on_order": on_order,
            "qty_to_order": qty_to_order,
            "days_stock": round(on_hand / base_daily_sales, 1) if base_daily_sales > 0 else 0,
            "qty_sold": total_units_sold_60,
            "margin": margin,
            "urgency": urgency,
            "momentum": momentum,
            "current_reorder_point": int(current_rp) if current_rp else 0,
            "current_desired_level": int(current_dl) if current_dl else 0,
            "recommended_reorder_point": int(new_reorder_point),
            "recommended_desired_level": int(new_desired_level),
            "change_needed": (int(new_reorder_point) != (int(current_rp) if current_rp else 0) or 
                             int(new_desired_level) != (int(current_dl) if current_dl else 0))
        })
            
    return recommendations
