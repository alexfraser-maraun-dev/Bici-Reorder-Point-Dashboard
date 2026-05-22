import math
from typing import List, Dict, Any

STATUS_RANKS = {
    "critical": 1,
    "low": 2,
    "warning": 3,
    "healthy": 4,
    "incoming": 5,
    "on_target": 6,
    "high": 7,
    "overstock": 8,
    "no_demand": 9,
}

STATUS_LABELS = {
    "critical": "Critical",
    "low": "Low",
    "warning": "Warning",
    "healthy": "Healthy",
    "incoming": "Incoming",
    "on_target": "On Target",
    "high": "High",
    "overstock": "Overstock",
    "no_demand": "No Demand",
}

STATUS_URGENCY = {
    "critical": 5,
    "low": 4,
    "warning": 3,
    "healthy": 2,
    "incoming": 2,
    "on_target": 1,
    "high": 1,
    "overstock": 1,
    "no_demand": 0,
}

MOMENTUM_DEFINITIONS = {
    "surging": {
        "label": "Surging",
        "rank": 1,
        "definition": "14d adjusted velocity is sharply higher than both older windows.",
    },
    "rising": {
        "label": "Rising",
        "rank": 2,
        "definition": "Recent adjusted velocity is meaningfully higher than older demand.",
    },
    "spiky": {
        "label": "Spiky",
        "rank": 3,
        "definition": "A small number of recent units creates a large short-term velocity jump.",
    },
    "flat": {
        "label": "Flat",
        "rank": 4,
        "definition": "Adjusted velocity is broadly steady across demand windows.",
    },
    "cooling": {
        "label": "Cooling",
        "rank": 5,
        "definition": "Recent adjusted velocity is meaningfully lower than older demand.",
    },
    "insufficient_data": {
        "label": "Insufficient Data",
        "rank": 6,
        "definition": "There is not enough sales or in-stock evidence to classify momentum.",
    },
}


def calculate_inventory_status(
    on_hand: float,
    on_order: float,
    reorder_point: float,
    desired_level: float,
) -> Dict[str, Any]:
    effective_on_hand = max(0, on_hand)
    qoh_adjusted_for_math = on_hand < 0
    inventory_position = effective_on_hand + on_order

    if reorder_point <= 0 and desired_level <= 0:
        status = "no_demand"
        reason = "No recommended reorder point or desired level is set for this item."
    elif desired_level > 0 and inventory_position >= desired_level * 1.5:
        status = "overstock"
        reason = f"Inventory position is at least 150% of desired level ({inventory_position:g} vs {desired_level:g})."
    elif desired_level > 0 and inventory_position >= desired_level * 1.2:
        status = "high"
        reason = f"Inventory position is at least 120% of desired level ({inventory_position:g} vs {desired_level:g})."
    elif desired_level > 0 and inventory_position >= desired_level * 0.8 and reorder_point > 0 and effective_on_hand <= reorder_point:
        status = "incoming"
        reason = f"Pipeline covers target, but on-hand is at or below ROP ({effective_on_hand:g} vs {reorder_point:g})."
    elif desired_level > 0 and inventory_position >= desired_level * 0.8:
        status = "on_target"
        reason = f"Inventory position is within the target band for desired level ({inventory_position:g} vs {desired_level:g})."
    elif reorder_point > 0 and inventory_position > reorder_point * 1.15:
        status = "healthy"
        reason = f"Inventory position is more than 115% of ROP ({inventory_position:g} vs {reorder_point:g})."
    elif reorder_point > 0 and inventory_position > reorder_point:
        status = "warning"
        reason = f"Inventory position is between 100% and 115% of ROP ({inventory_position:g} vs {reorder_point:g})."
    elif reorder_point > 0 and inventory_position > reorder_point * 0.5:
        status = "low"
        reason = f"Inventory position is between 50% and 100% of ROP ({inventory_position:g} vs {reorder_point:g})."
    else:
        status = "critical"
        reason = f"Inventory position is at or below 50% of ROP ({inventory_position:g} vs {reorder_point:g})."

    if qoh_adjusted_for_math:
        reason = f"QOH is negative ({on_hand:g}), so replenishment math uses 0 on hand. {reason}"

    return {
        "effective_on_hand": effective_on_hand,
        "qoh_adjusted_for_math": qoh_adjusted_for_math,
        "inventory_position": inventory_position,
        "inventory_status": status,
        "inventory_status_label": STATUS_LABELS[status],
        "inventory_status_rank": STATUS_RANKS[status],
        "inventory_status_reason": reason,
        "urgency": STATUS_URGENCY[status],
    }

def calculate_momentum_status(
    adjusted_daily_sales_14d: float,
    adjusted_daily_sales_15_30d: float,
    adjusted_daily_sales_31_60d: float,
    raw_units_sold_14d: float,
    raw_units_sold_60d: float,
    active_days_14d: int,
    active_days_15_30d: int,
    active_days_31_60d: int,
) -> Dict[str, Any]:
    def pct_higher(new_value, old_value, threshold):
        if old_value <= 0:
            return new_value > 0
        return new_value >= old_value * (1 + threshold)

    def pct_lower(new_value, old_value, threshold):
        if old_value <= 0:
            return False
        return new_value <= old_value * (1 - threshold)

    if raw_units_sold_60d <= 0 or active_days_14d < 3 or active_days_15_30d < 3 or active_days_31_60d < 7:
        status = "insufficient_data"
        reason = "Not enough sales or active in-stock days across the 14d, 15-30d, and 31-60d windows."
    elif (
        raw_units_sold_14d < 3
        and adjusted_daily_sales_14d > 0
        and pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_15_30d, 1.0)
        and pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_31_60d, 1.0)
    ):
        status = "spiky"
        reason = "The last 14 days are at least 2x both older windows, but fewer than 3 units sold recently."
    elif (
        pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_31_60d, 0.75)
        and pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_15_30d, 0.35)
    ):
        status = "surging"
        reason = "14d velocity is at least 75% above days 31-60 and at least 35% above days 15-30."
    elif (
        (
            pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_15_30d, 0.40)
            and adjusted_daily_sales_15_30d >= adjusted_daily_sales_31_60d
        )
        or (
            pct_higher(adjusted_daily_sales_14d, adjusted_daily_sales_31_60d, 0.25)
            and pct_higher(adjusted_daily_sales_15_30d, adjusted_daily_sales_31_60d, 0.25)
        )
    ):
        status = "rising"
        reason = "Recent velocity shows stronger multi-window growth before qualifying as rising."
    elif (
        (
            pct_lower(adjusted_daily_sales_14d, adjusted_daily_sales_15_30d, 0.40)
            and adjusted_daily_sales_15_30d <= adjusted_daily_sales_31_60d
        )
        or (
            pct_lower(adjusted_daily_sales_14d, adjusted_daily_sales_31_60d, 0.25)
            and pct_lower(adjusted_daily_sales_15_30d, adjusted_daily_sales_31_60d, 0.25)
        )
    ):
        status = "cooling"
        reason = "Recent velocity shows stronger multi-window decline before qualifying as cooling."
    else:
        status = "flat"
        reason = "Adjusted velocity is within the 25% movement threshold across the comparison windows."

    definition = MOMENTUM_DEFINITIONS[status]
    return {
        "momentum_status": status,
        "momentum_label": definition["label"],
        "momentum_rank": definition["rank"],
        "momentum_reason": reason,
    }


def replenishment_ceil(value: float) -> int:
    return math.ceil(value - 1e-9)


def process_recommendations(
    items_df_dict: List[Dict[str, Any]], 
    lead_times_df_dict: List[Dict[str, Any]], 
    safety_days: int = 7, 
    override_forecast: int = None, 
    growth_multiplier: float = 1.0, 
    recent_30d_weight: float = 0.70,
    weight_14d: float = None,
    weight_15_30d: float = None,
    weight_31_60d: float = None,
    adjustment_mode: str = "shrink",
    momentum_data: Dict[str, float] = None,
    brand_sourcing_rules: Dict[str, Any] = None,
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

        confidence = min(1.0, max(0.0, active_days / 10))
        return raw_daily + ((unbounded_adjusted_daily - raw_daily) * confidence)

    def effective_active_days(period_days, qoh_oos_days, distinct_sale_days):
        return max(1, min(period_days, max(period_days - qoh_oos_days, distinct_sale_days, 3)))
    
    # Build Lead Time dictionary: (vendor_id, location_id) -> lead_time_days
    # Fallback to a default if not found
    lead_time_dict = {}
    lead_time_po_count_dict = {}
    for row in lead_times_df_dict:
        vid = normalize_id(row.get("vendor_id"))
        lid = normalize_id(row.get("location_id"))
        lt = row.get("lead_time_days")
        if vid is not None and lid is not None and lt is not None:
            lead_time_dict[(vid, lid)] = lt
            lead_time_po_count_dict[(vid, lid)] = row.get("po_count")

    brand_rule_dict = {}
    for brand_name, rule in (brand_sourcing_rules or {}).items():
        normalized_brand = normalize_name(brand_name)
        preferred_vendor_id = normalize_id(rule.get("preferred_vendor_id")) if rule else None
        if normalized_brand and preferred_vendor_id is not None:
            brand_rule_dict[normalized_brand] = {
                "preferred_vendor_id": preferred_vendor_id,
                "preferred_vendor_name": rule.get("preferred_vendor_name"),
            }
            
    recommendations = []
    if weight_14d is None and weight_15_30d is None and weight_31_60d is None:
        recent_30d_weight = min(1.0, max(0.0, recent_30d_weight))
        weight_14d = 0.0
        weight_15_30d = recent_30d_weight
        weight_31_60d = 1.0 - recent_30d_weight
    else:
        if None in (weight_14d, weight_15_30d, weight_31_60d):
            raise ValueError("All demand weights must be provided together.")
        weight_14d = float(weight_14d)
        weight_15_30d = float(weight_15_30d)
        weight_31_60d = float(weight_31_60d)
        if any(weight < 0 or weight > 1 for weight in (weight_14d, weight_15_30d, weight_31_60d)):
            raise ValueError("Demand weights must be between 0 and 1.")
        if abs((weight_14d + weight_15_30d + weight_31_60d) - 1.0) > 0.001:
            raise ValueError("Demand weights must total 1.0.")
    recent_30d_weight = weight_14d + weight_15_30d
    prior_30d_weight = weight_31_60d
    if adjustment_mode not in {"shrink", "min_days", "cap", "raw"}:
        adjustment_mode = "shrink"
    
    for row in items_df_dict:
        lightspeed_item_id = row.get("item_id")
        if not lightspeed_item_id:
            continue
        system_id = lightspeed_item_id
            
        location_id = normalize_id(row.get("location_id"))
        loc_name = shop_map.get(location_id, f"Shop {location_id}")
        vendor_id = normalize_id(row.get("vendor_id"))
        brand_rule = brand_rule_dict.get(normalize_name(row.get("brand")))
        
        # Determine lead time
        lead_time = 14.0
        lead_time_source = "default"
        lead_time_vendor_id = None
        lead_time_vendor = None
        lead_time_po_count = None

        if brand_rule:
            preferred_vendor_id = brand_rule["preferred_vendor_id"]
            preferred_key = (preferred_vendor_id, location_id)
            if preferred_key in lead_time_dict:
                lead_time = lead_time_dict[preferred_key]
                lead_time_source = "preferred_vendor"
                lead_time_vendor_id = preferred_vendor_id
                lead_time_vendor = brand_rule.get("preferred_vendor_name")
                lead_time_po_count = lead_time_po_count_dict.get(preferred_key)

        item_vendor_key = (vendor_id, location_id)
        if lead_time_source == "default" and item_vendor_key in lead_time_dict:
            lead_time = lead_time_dict[item_vendor_key]
            lead_time_source = "item_vendor"
            lead_time_vendor_id = vendor_id
            lead_time_vendor = row.get("vendor")
            lead_time_po_count = lead_time_po_count_dict.get(item_vendor_key)
        
        # Raw Sales Data
        total_units_sold_14 = row.get("total_units_sold_14", 0)
        total_units_sold_30 = row.get("total_units_sold_30", 0)
        total_units_sold_60 = row.get("total_units_sold_60", 0)
        distinct_sale_days_14 = row.get("distinct_sale_days_14", 0)
        distinct_sale_days_30 = row.get("distinct_sale_days_30", 0)
        distinct_sale_days_60 = row.get("distinct_sale_days_60", 0)
        days_out_of_stock_14 = row.get("days_out_of_stock_14", 0)
        days_out_of_stock_30 = row.get("days_out_of_stock_30", 0)
        days_out_of_stock_60 = row.get("days_out_of_stock_60", 0)
        
        active_days_14 = effective_active_days(14, days_out_of_stock_14, distinct_sale_days_14)
        adjusted_daily_sales_14d = guarded_daily_sales(total_units_sold_14, 14, active_days_14)

        mid_units_sold_16 = max(0, total_units_sold_30 - total_units_sold_14)
        mid_days_out_of_stock_16 = min(16, max(0, days_out_of_stock_30 - days_out_of_stock_14))
        mid_distinct_sale_days_16 = min(16, max(0, distinct_sale_days_30 - distinct_sale_days_14))
        mid_active_days_16 = effective_active_days(16, mid_days_out_of_stock_16, mid_distinct_sale_days_16)
        adjusted_daily_sales_15_30d = guarded_daily_sales(mid_units_sold_16, 16, mid_active_days_16)

        active_days_30 = effective_active_days(30, days_out_of_stock_30, distinct_sale_days_30)
        adjusted_daily_sales_30d = guarded_daily_sales(total_units_sold_30, 30, active_days_30)
        
        active_days_60 = effective_active_days(60, days_out_of_stock_60, distinct_sale_days_60)
        adjusted_daily_sales_60d = guarded_daily_sales(total_units_sold_60, 60, active_days_60)

        prior_units_sold_30 = max(0, total_units_sold_60 - total_units_sold_30)
        prior_days_out_of_stock_30 = min(30, max(0, days_out_of_stock_60 - days_out_of_stock_30))
        prior_distinct_sale_days_30 = min(30, max(0, distinct_sale_days_60 - distinct_sale_days_30))
        prior_active_days_30 = effective_active_days(30, prior_days_out_of_stock_30, prior_distinct_sale_days_30)
        adjusted_daily_sales_31_60d = guarded_daily_sales(prior_units_sold_30, 30, prior_active_days_30)
        
        # Blend 14d, 15-30d, and 31-60d velocities for sensitivity plus stability.
        base_daily_sales = (
            adjusted_daily_sales_14d * weight_14d
            + adjusted_daily_sales_15_30d * weight_15_30d
            + adjusted_daily_sales_31_60d * weight_31_60d
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
        safety_stock = replenishment_ceil(adjusted_daily_sales * safety_days)
        new_reorder_point = replenishment_ceil((adjusted_daily_sales * lead_time) + safety_stock)
        
        # New Desired Level = Sales * Forecast Period
        new_desired_level = replenishment_ceil(adjusted_daily_sales * forecast_period)
        
        # QTY to Order Calculation
        inventory_status = calculate_inventory_status(
            on_hand,
            on_order,
            new_reorder_point,
            new_desired_level,
        )
        qty_to_order = max(0, int(new_desired_level - inventory_status["inventory_position"]))
            
        momentum_status = calculate_momentum_status(
            adjusted_daily_sales_14d,
            adjusted_daily_sales_15_30d,
            adjusted_daily_sales_31_60d,
            total_units_sold_14,
            total_units_sold_60,
            active_days_14,
            mid_active_days_16,
            prior_active_days_30,
        )
                
        margin = "0%"

        recommendations.append({
            "system_id": system_id,
            "lightspeed_item_id": lightspeed_item_id,
            "sku": row.get("sku"),
            "brand": row.get("brand"),
            "description": row.get("description"),
            "category": row.get("category"),
            "vendor": row.get("vendor"),
            "vendor_id": row.get("vendor_id"),
            "location": loc_name,
            "daily_sales": round(base_daily_sales, 2), # Weighted base velocity
            "adjusted_daily_sales": round(adjusted_daily_sales, 2), # Velocity w/ multiplier
            "days_out_of_stock": days_out_of_stock_60,
            "raw_daily_sales": base_daily_sales,
            "adjusted_daily_sales_14d": round(adjusted_daily_sales_14d, 3),
            "adjusted_daily_sales_30d": round(adjusted_daily_sales_30d, 3),
            "adjusted_daily_sales_15_30d": round(adjusted_daily_sales_15_30d, 3),
            "adjusted_daily_sales_prior_30d": round(adjusted_daily_sales_31_60d, 3),
            "adjusted_daily_sales_31_60d": round(adjusted_daily_sales_31_60d, 3),
            "adjusted_daily_sales_60d": round(adjusted_daily_sales_60d, 3),
            "weight_14d": round(weight_14d, 2),
            "weight_15_30d": round(weight_15_30d, 2),
            "weight_31_60d": round(weight_31_60d, 2),
            "recent_30d_weight": round(recent_30d_weight, 2),
            "prior_30d_weight": round(prior_30d_weight, 2),
            "adjustment_mode": adjustment_mode,
            "lead_time": lead_time,
            "lead_time_source": lead_time_source,
            "lead_time_vendor_id": lead_time_vendor_id,
            "lead_time_vendor": lead_time_vendor,
            "lead_time_po_count": lead_time_po_count,
            "forecast_period": forecast_period,
            "safety_days": safety_days,
            "raw_units_sold_14d": total_units_sold_14,
            "raw_units_sold_30d": total_units_sold_30,
            "raw_units_sold_60d": total_units_sold_60,
            "raw_units_sold_15_30d": mid_units_sold_16,
            "raw_units_sold_31_60d": prior_units_sold_30,
            "days_out_of_stock_14": days_out_of_stock_14,
            "active_days_14": active_days_14,
            "distinct_sale_days_14": distinct_sale_days_14,
            "days_out_of_stock_30": days_out_of_stock_30,
            "active_days_30": active_days_30,
            "distinct_sale_days_30": distinct_sale_days_30,
            "days_out_of_stock_15_30": mid_days_out_of_stock_16,
            "active_days_15_30": mid_active_days_16,
            "distinct_sale_days_15_30": mid_distinct_sale_days_16,
            "days_out_of_stock_60": days_out_of_stock_60,
            "active_days_60": active_days_60,
            "distinct_sale_days_60": distinct_sale_days_60,
            "days_out_of_stock_31_60": prior_days_out_of_stock_30,
            "active_days_31_60": prior_active_days_30,
            "distinct_sale_days_31_60": prior_distinct_sale_days_30,
            "forecast_14d": round(adjusted_daily_sales_14d * 14, 1),
            "forecast_30d": round(adjusted_daily_sales_30d * 30, 1), # Stockout-adjusted 30d demand
            "forecast_15_30d": round(adjusted_daily_sales_15_30d * 16, 1),
            "forecast_prior_30d": round(adjusted_daily_sales_31_60d * 30, 1),
            "forecast_31_60d": round(adjusted_daily_sales_31_60d * 30, 1),
            "forecast_60d": round(adjusted_daily_sales_60d * 60, 1), # Stockout-adjusted 60d demand
            "on_hand": on_hand,
            "effective_on_hand": inventory_status["effective_on_hand"],
            "qoh_adjusted_for_math": inventory_status["qoh_adjusted_for_math"],
            "on_order": on_order,
            "inventory_position": inventory_status["inventory_position"],
            "inventory_status": inventory_status["inventory_status"],
            "inventory_status_label": inventory_status["inventory_status_label"],
            "inventory_status_rank": inventory_status["inventory_status_rank"],
            "inventory_status_reason": inventory_status["inventory_status_reason"],
            "qty_to_order": qty_to_order,
            "days_stock": round(inventory_status["effective_on_hand"] / base_daily_sales, 1) if base_daily_sales > 0 else 0,
            "qty_sold": total_units_sold_60,
            "margin": margin,
            "urgency": inventory_status["urgency"],
            "momentum": momentum_status["momentum_status"],
            "momentum_status": momentum_status["momentum_status"],
            "momentum_label": momentum_status["momentum_label"],
            "momentum_rank": momentum_status["momentum_rank"],
            "momentum_reason": momentum_status["momentum_reason"],
            "current_reorder_point": int(current_rp) if current_rp else 0,
            "current_desired_level": int(current_dl) if current_dl else 0,
            "recommended_reorder_point": int(new_reorder_point),
            "recommended_desired_level": int(new_desired_level),
            "change_needed": (int(new_reorder_point) != (int(current_rp) if current_rp else 0) or 
                             int(new_desired_level) != (int(current_dl) if current_dl else 0))
        })
            
    return recommendations
