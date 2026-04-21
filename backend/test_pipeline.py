import os
import sys
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bigquery_sync import fetch_sales_history, fetch_inventory_snapshot, fetch_lead_times
from app.services.recommendation_engine import calculate_recommendation

def run_test():
    print("🚀 Initializing BigQuery fetch pipeline...")
    
    try:
        print("1. Fetching trailing sales history (30 days)...")
        sales_df = fetch_sales_history(trailing_days=30)
        print(f"   ✓ Pulled {len(sales_df)} sales records.")

        print("2. Fetching inventory snapshot & open POs...")
        inventory_df = fetch_inventory_snapshot()
        print(f"   ✓ Pulled {len(inventory_df)} inventory records.")

        print("3. Fetching vendor lead times...")
        lead_time_df = fetch_lead_times()
        print(f"   ✓ Pulled {len(lead_time_df)} lead time records.")
    except Exception as e:
        print(f"\n❌ Error connecting to BigQuery: {e}")
        print("Ensure you have GOOGLE_APPLICATION_CREDENTIALS mapped properly in your environment.")
        return

    print("\n🧠 Processing recommendations...")
    
    # Merge sales into inventory
    df = pd.merge(inventory_df, sales_df, on=["item_id", "location_id"], how="left")
    df["trailing_units_sold"] = df["trailing_units_sold"].fillna(0)
    
    # Merge lead times based on default_vendor_id
    df = pd.merge(
        df, 
        lead_time_df, 
        left_on=["default_vendor_id", "location_id"], 
        right_on=["vendor_id", "location_id"], 
        how="left"
    )
    
    # Apply Global Default Lead Time for missing vendors (e.g., 14 days)
    df["lead_time_days"] = df["lead_time_days"].fillna(14)
    
    # Drop rows without on_hand info just to be safe
    df = df.dropna(subset=["on_hand_units"])
    
    # Limit to top 50 rows for terminal output readability
    # Sorting by trailing sales so we see interesting fast-movers rather than dead stock
    test_df = df.sort_values(by="trailing_units_sold", ascending=False).head(50)
    
    results = []
    for _, row in test_df.iterrows():
        rec = calculate_recommendation(
            trailing_units_sold=row["trailing_units_sold"],
            trailing_days=30,
            lead_time_days=int(row["lead_time_days"]),
            forecast_days=60, # Desired inventory coverage
            safety_days=7,    # Safety stock buffer
            on_hand_units=int(row["on_hand_units"]),
            on_order_units=int(row["on_order_units"])
        )
        
        results.append({
            "SKU": row["sku"],
            "Location": row["location_id"],
            "Vendor": row["default_vendor_id"],
            "Sales/Mo": row["trailing_units_sold"],
            "Lead Time": int(row["lead_time_days"]),
            "Current QOH": int(row["on_hand_units"]),
            "On Order": int(row["on_order_units"]),
            "Curr Reorder Pt": row["current_reorder_point"],
            "New Reorder Pt": rec["reorder_point"],
            "Buy Qty": rec["suggested_buy_qty"],
            "Needs Order": rec["needs_order"]
        })
        
    results_df = pd.DataFrame(results)
    
    print("\n" + "="*80)
    print("📊 TEST PIPELINE RESULTS (Top 50 Fast Movers)")
    print("="*80)
    print(results_df.to_string(index=False))
    print("="*80)
    print("✅ Pipeline test complete.")

if __name__ == "__main__":
    run_test()
