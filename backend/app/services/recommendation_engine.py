import math

def calculate_recommendation(
    trailing_units_sold: int,
    trailing_days: int,
    lead_time_days: int,
    forecast_days: int,
    safety_days: int,
    on_hand_units: int,
    on_order_units: int,
    committed_units: int = 0
):
    """
    Executes the V1 formula logic defined in the brief.
    """
    daily_sales = trailing_units_sold / trailing_days if trailing_days > 0 else 0
    safety_stock = math.ceil(daily_sales * safety_days)
    reorder_point = math.ceil(daily_sales * lead_time_days + safety_stock)
    desired_inventory = math.ceil(daily_sales * forecast_days)
    
    inventory_position = on_hand_units + on_order_units - committed_units
    needs_order = inventory_position <= reorder_point
    suggested_buy_qty = max(0, desired_inventory - inventory_position)
    
    return {
        "daily_sales": daily_sales,
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "desired_inventory": desired_inventory,
        "inventory_position": inventory_position,
        "needs_order": needs_order,
        "suggested_buy_qty": suggested_buy_qty
    }
