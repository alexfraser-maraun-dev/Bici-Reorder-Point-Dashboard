from google.cloud import bigquery
import pandas as pd
import os
import json
import time

# Initialize BigQuery client
client = bigquery.Client()

# NOTE: You will need to replace 'YOUR_PROJECT.YOUR_DATASET' with your actual GCP project and dataset name.
BQ_DATASET = os.getenv("BQ_DATASET", "bici-klaviyo-datasync.light_speed_retailne") # Using assumed default
CACHE_FILE = "bq_metrics_cache.json"
CACHE_EXPIRY_SECONDS = 86400 # 24 hours

def get_cached_bq_metrics(trailing_days: int = 60) -> dict:
    """
    Returns cached BQ metrics if they are less than 24 hours old.
    Otherwise, fetches fresh from BQ and saves to cache.
    Returns a dict mapping 'system_id_shop_id' to metrics.
    """
    if os.path.exists(CACHE_FILE):
        file_age = time.time() - os.path.getmtime(CACHE_FILE)
        if file_age < CACHE_EXPIRY_SECONDS:
            try:
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load cache: {e}")
                
    # If we get here, we need to fetch fresh data
    print("Fetching fresh metrics from BigQuery (this may take 10-15 seconds)...")
    try:
        df = fetch_unified_metrics(trailing_days)
        # Convert DataFrame to a dictionary optimized for fast lookups
        # Format: {"system_id_location_id": {"days_out_of_stock": x, "total_units_sold": y}}
        result_dict = {}
        for _, row in df.iterrows():
            # BigQuery might return system_sku as string and location_id as int
            key = f"{row['sku']}_{row['location_id']}"
            result_dict[key] = {
                "days_out_of_stock": int(row['days_out_of_stock']),
                "total_units_sold": float(row['total_units_sold'])
            }
            
        # Save to cache
        with open(CACHE_FILE, 'w') as f:
            json.dump(result_dict, f)
            
        return result_dict
    except Exception as e:
        print(f"BigQuery fetch failed: {e}")
        # Try to fall back to old cache if available
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        return {}

def fetch_sales_history(trailing_days: int) -> pd.DataFrame:
    """
    Fetches trailing sales data by item and location from BigQuery.
    Filters out voided sales and only includes completed ones.
    """
    query = f"""
        SELECT
            sl.item_id,
            sl.shop_id AS location_id,
            SUM(sl.unit_quantity) AS trailing_units_sold
        FROM
            `{BQ_DATASET}.sale_line_history` sl
        JOIN
            `{BQ_DATASET}.sale_history` s ON sl.sale_id = s.id
        WHERE
            s.completed = TRUE
            AND s.voided = FALSE
            AND s.complete_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @trailing_days DAY)
        GROUP BY
            sl.item_id,
            sl.shop_id
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("trailing_days", "INT64", trailing_days),
        ]
    )
    return client.query(query, job_config=job_config).to_dataframe()

def fetch_inventory_snapshot() -> pd.DataFrame:
    """
    Fetches current on_hand from item_shop_history and calculates on_order_units 
    from open purchase orders. Includes default_vendor_id from item_history.
    """
    query = f"""
        WITH latest_item_shop AS (
            SELECT * FROM `{BQ_DATASET}.item_shop_history`
            QUALIFY ROW_NUMBER() OVER(PARTITION BY item_id, shop_id ORDER BY updated_time DESC) = 1
        ),
        latest_item AS (
            SELECT * FROM `{BQ_DATASET}.item_history`
            QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_inventory AS (
            SELECT
                ish.item_id,
                ish.shop_id,
                ish.qoh AS on_hand_units,
                ish.reorder_point AS current_reorder_point,
                ish.reorder_level AS current_desired_inventory,
                ih.default_vendor_id,
                ih.system_sku AS sku
            FROM
                latest_item_shop ish
            JOIN
                latest_item ih ON ish.item_id = ih.id
        ),
        open_pos AS (
            SELECT
                ol.item_id,
                o.shop_id,
                SUM(ol.quantity - COALESCE(ol.num_received, 0)) AS on_order_units
            FROM
                `{BQ_DATASET}.order_line_history` ol
            JOIN
                `{BQ_DATASET}.order_history` o ON ol.order_id = o.id
            WHERE
                o.complete = FALSE 
                AND o.archived = FALSE
            GROUP BY
                ol.item_id, 
                o.shop_id
        )
        SELECT
            i.item_id,
            i.shop_id AS location_id,
            i.on_hand_units,
            COALESCE(p.on_order_units, 0) AS on_order_units,
            i.current_reorder_point,
            i.current_desired_inventory,
            i.default_vendor_id,
            i.sku
        FROM
            latest_inventory i
        LEFT JOIN
            open_pos p ON i.item_id = p.item_id AND i.shop_id = p.shop_id
    """
    return client.query(query).to_dataframe()

def fetch_lead_times(lookback_months: int = 12) -> pd.DataFrame:
    """
    Fetches average vendor lead times per location from the po_report table.
    """
    query = f"""
        SELECT
            vendor_id,
            shop_id AS location_id,
            CEIL(AVG(TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY))) AS lead_time_days,
            COUNT(order_id) AS po_count
        FROM
            `{BQ_DATASET}.po_report`
        WHERE
            po_ordered_at IS NOT NULL
            AND first_received_at IS NOT NULL
            AND DATE(po_ordered_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_months MONTH)
            -- Filter out obvious data entry errors and pre-booked POs (limit to replenishment POs)
            AND TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY) BETWEEN 0 AND 50
        GROUP BY
            vendor_id, 
            shop_id
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lookback_months", "INT64", lookback_months),
        ]
    )
    return client.query(query, job_config=job_config).to_dataframe()


def fetch_unified_metrics(trailing_days: int = 60) -> pd.DataFrame:
    """
    Unified query that returns sales totals, stockout days, and current inventory levels
    per item per location. Uses the correct LS_itemshop_history table fields.
    """
    query = f"""
        WITH date_spine AS (
          -- Generate a list of all dates in the trailing period
          SELECT day 
          FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL @trailing_days DAY), CURRENT_DATE())) AS day
        ),
        item_shop_history AS (
          -- Get all inventory changes. We use timeStamp as the change_date.
          SELECT 
            itemID as item_id, 
            shopID as location_id, 
            qoh, 
            DATE(timeStamp) as change_date
          FROM `{BQ_DATASET}.LS_itemshop_history`
          WHERE shopID > 0
        ),
        daily_qoh_mapped AS (
          -- Join every item/location to the date spine to fill in the "quiet" days using LAST_VALUE
          SELECT 
            d.day,
            ish.item_id,
            ish.location_id,
            LAST_VALUE(item_shop_history.qoh IGNORE NULLS) OVER (
              PARTITION BY ish.item_id, ish.location_id 
              ORDER BY d.day 
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) as daily_qoh
          FROM date_spine d
          CROSS JOIN (SELECT DISTINCT item_id, location_id FROM item_shop_history) ish
          LEFT JOIN item_shop_history ON item_shop_history.change_date = d.day 
            AND item_shop_history.item_id = ish.item_id 
            AND item_shop_history.location_id = ish.location_id
        ),
        stockout_counts AS (
          -- Count how many days each item was out of stock per location
          SELECT 
            item_id, 
            location_id, 
            COUNTIF(daily_qoh <= 0) as days_out_of_stock
          FROM daily_qoh_mapped
          GROUP BY 1, 2
        ),
        sales_totals AS (
          -- Get total units sold in the same period
          SELECT 
            sl.item_id, 
            sl.shop_id as location_id, 
            SUM(sl.unit_quantity) as total_units_sold
          FROM `{BQ_DATASET}.sale_line_history` sl
          JOIN `{BQ_DATASET}.sale_history` s ON sl.sale_id = s.id
          WHERE s.completed = TRUE
            AND s.voided = FALSE
            AND DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL @trailing_days DAY)
          GROUP BY 1, 2
        )
        -- Final Join
        SELECT 
          ih.system_sku as sku,
          ih.id as item_id,
          current_ish.shopID as location_id,
          COALESCE(st.total_units_sold, 0) as total_units_sold,
          COALESCE(sc.days_out_of_stock, 0) as days_out_of_stock,
          current_ish.qoh as current_qoh,
          current_ish.reorderPoint as current_reorder_point,
          current_ish.reorderLevel as current_desired_level
        FROM `{BQ_DATASET}.item_history` ih
        JOIN `{BQ_DATASET}.LS_itemshop_history` current_ish ON ih.id = current_ish.itemID
        LEFT JOIN sales_totals st ON ih.id = st.item_id AND current_ish.shopID = st.location_id
        LEFT JOIN stockout_counts sc ON ih.id = sc.item_id AND current_ish.shopID = sc.location_id
        WHERE current_ish.shopID > 0
        QUALIFY ROW_NUMBER() OVER(PARTITION BY ih.id, current_ish.shopID ORDER BY current_ish.timeStamp DESC) = 1
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("trailing_days", "INT64", trailing_days),
        ]
    )
    return client.query(query, job_config=job_config).to_dataframe()

