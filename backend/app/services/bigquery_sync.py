from google.cloud import bigquery
import pandas as pd
import os
import json
import time

# Initialize BigQuery client lazily to avoid startup crashes
_client = None

def get_bq_client():
    global _client
    if _client is None:
        # This will look for GOOGLE_APPLICATION_CREDENTIALS env var
        _client = bigquery.Client()
    return _client

APP_DATASET = os.getenv("APP_DATASET", "bici-klaviyo-datasync.BiciReorderPointDashboard")
LS_DATASET = os.getenv("LS_DATASET", "bici-klaviyo-datasync.light_speed_retailne")
QUALIFIED_ITEMS_VIEW = os.getenv("QUALIFIED_ITEMS_VIEW", f"{APP_DATASET}.replen_qualified_items")
CACHE_FILE = "bq_metrics_cache.json"
CACHE_EXPIRY_SECONDS = 86400 # 24 hours

def log_recommendation_run(run_data: dict):
    """Streams a run summary to BigQuery."""
    table_id = f"{APP_DATASET}.replen_recommendation_runs"
    try:
        client = get_bq_client()
        errors = client.insert_rows_json(table_id, [run_data])
        if errors:
            print(f"BigQuery Run Log Errors: {errors}")
    except Exception as e:
        print(f"Failed to log run to BigQuery: {e}")

def log_velocity_snapshots(snapshots: list):
    """Streams velocity snapshots to BigQuery in batches."""
    table_id = f"{APP_DATASET}.replen_velocity_snapshots"
    try:
        client = get_bq_client()
        # Batch by 500 rows to avoid request size limits
        for i in range(0, len(snapshots), 500):
            batch = snapshots[i:i+500]
            errors = client.insert_rows_json(table_id, batch)
            if errors:
                print(f"BigQuery Snapshot Errors: {errors}")
    except Exception as e:
        print(f"Failed to log snapshots to BigQuery: {e}")

def log_writeback(log_data: dict):
    """Streams a writeback event to BigQuery."""
    table_id = f"{APP_DATASET}.replen_writeback_logs"
    try:
        client = get_bq_client()
        errors = client.insert_rows_json(table_id, [log_data])
        if errors:

            print(f"BigQuery Writeback Log Errors: {errors}")
    except Exception as e:
        print(f"Failed to log writeback to BigQuery: {e}")

def get_recommendation_runs(limit: int = 50):
    """Fetches historical runs from BigQuery."""
    query = f"""
        SELECT * FROM `{LS_DATASET}.replen_recommendation_runs`
        ORDER BY started_at DESC
        LIMIT {limit}
    """
    try:
        client = get_bq_client()
        query_job = client.query(query)
        rows = query_job.result()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Failed to fetch runs from BigQuery: {e}")
        return []

def get_writeback_logs(limit: int = 100):
    """Fetches writeback logs from BigQuery."""
    query = f"""
        SELECT * FROM `{APP_DATASET}.replen_writeback_logs`
        ORDER BY created_at DESC
        LIMIT {limit}
    """
    try:
        client = get_bq_client()
        query_job = client.query(query)
        rows = query_job.result()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Failed to fetch writeback logs from BigQuery: {e}")
        return []

def get_managed_skus():
    """Fetches list of managed SKUs from BigQuery."""
    query = f"""
        SELECT * FROM `{APP_DATASET}.replen_managed_skus`
    """
    try:
        client = get_bq_client()
        return client.query(query).to_dataframe().to_dict('records')
    except Exception as e:
        print(f"Failed to fetch managed SKUs: {e}")
        return []

def upsert_managed_skus(skus: list):
    """Upserts managed SKUs into BigQuery using MERGE."""
    table_id = f"{APP_DATASET}.replen_managed_skus"
    # Create temp table for merge
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    temp_table_id = f"{table_id}_temp"
    client.load_table_from_json(skus, temp_table_id, job_config=job_config).result()

    merge_query = f"""
        MERGE `{table_id}` T
        USING `{temp_table_id}` S
        ON T.sku = S.sku
        WHEN MATCHED THEN
            UPDATE SET T.product = S.product, T.brand = S.brand, T.vendor = S.vendor, 
                       T.category = S.category, T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (sku, item_id, product, brand, vendor, category, added_by, created_at, updated_at)
            VALUES (S.sku, S.item_id, S.product, S.brand, S.vendor, S.category, S.added_by, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    client.query(merge_query).result()
    client.delete_table(temp_table_id)

def get_sku_overrides():
    """Fetches manual overrides from BigQuery."""
    query = f"SELECT * FROM `{APP_DATASET}.replen_sku_overrides`"
    try:
        client = get_bq_client()
        df = client.query(query).to_dataframe()
        # Create lookup map: {sku_location: {rop: x, dl: y, locked: z}}
        overrides = {}
        for _, row in df.iterrows():
            key = f"{row['sku']}_{row['location_id']}"
            overrides[key] = {
                "manual_reorder_point": row['manual_reorder_point'],
                "manual_desired_level": row['manual_desired_level'],
                "locked": row['locked']
            }
        return overrides
    except Exception as e:
        print(f"Failed to fetch overrides: {e}")
        return {}

def upsert_sku_override(override_data: dict):
    """Upserts a single override into BigQuery."""
    table_id = f"{APP_DATASET}.replen_sku_overrides"
    merge_query = f"""
        MERGE `{table_id}` T
        USING (SELECT @sku as sku, @location_id as location_id) S
        ON T.sku = S.sku AND T.location_id = S.location_id
        WHEN MATCHED THEN
            UPDATE SET 
                manual_reorder_point = @rop,
                manual_desired_level = @dl,
                locked = @locked,
                updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (sku, location_id, manual_reorder_point, manual_desired_level, locked, updated_at)
            VALUES (@sku, @location_id, @rop, @dl, @locked, CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("sku", "STRING", override_data['sku']),
            bigquery.ScalarQueryParameter("location_id", "STRING", override_data['location_id']),
            bigquery.ScalarQueryParameter("rop", "INT64", override_data.get('manual_reorder_point')),
            bigquery.ScalarQueryParameter("dl", "INT64", override_data.get('manual_desired_level')),
            bigquery.ScalarQueryParameter("locked", "BOOL", override_data.get('locked', False)),
        ]
    )
    client.query(merge_query, job_config=job_config).result()

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
            `{LS_DATASET}.sale_line_history` sl
        JOIN
            `{LS_DATASET}.sale_history` s ON sl.sale_id = s.id
        WHERE
            s.completed = TRUE
            AND s.voided = FALSE
            AND s.complete_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @trailing_days DAY)
        GROUP BY
            sl.item_id,
            sl.shop_id
    """
    
    client = get_bq_client()
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
            SELECT * FROM `{LS_DATASET}.item_shop_history`
            QUALIFY ROW_NUMBER() OVER(PARTITION BY item_id, shop_id ORDER BY updated_time DESC) = 1
        ),
        latest_item AS (
            SELECT * FROM `{LS_DATASET}.item_history`
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
                `{LS_DATASET}.order_line_history` ol
            JOIN
                `{LS_DATASET}.order_history` o ON ol.order_id = o.id
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
    client = get_bq_client()
    return client.query(query).to_dataframe()

_bq_lead_time_cache = {}

def fetch_lead_times(lookback_months: int = 12, force_refresh: bool = False) -> pd.DataFrame:
    """
    Fetches average vendor lead times per location from the po_report table.
    """
    current_time = time.time()
    
    if not force_refresh and lookback_months in _bq_lead_time_cache:
        cached_data, timestamp = _bq_lead_time_cache[lookback_months]
        if current_time - timestamp < CACHE_TTL:
            return cached_data

    query = f"""
        SELECT
            vendor_id,
            shop_id AS location_id,
            CEIL(AVG(TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY))) AS lead_time_days,
            COUNT(order_id) AS po_count
        FROM
            `{LS_DATASET}.po_report`
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
    
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lookback_months", "INT64", lookback_months),
        ]
    )
    df = client.query(query, job_config=job_config).to_dataframe()
    _bq_lead_time_cache[lookback_months] = (df, time.time())
    return df


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
          FROM `{LS_DATASET}.LS_itemshop_history`
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
          FROM `{LS_DATASET}.sale_line_history` sl
          JOIN `{LS_DATASET}.sale_history` s ON sl.sale_id = s.id
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
        FROM `{LS_DATASET}.item_history` ih
        JOIN `{LS_DATASET}.LS_itemshop_history` current_ish ON ih.id = current_ish.itemID
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

_bq_tag_cache = {}
CACHE_TTL = 300 # 5 minutes

def fetch_tagged_items_metrics(tag_name: str = "auto-replen", force_refresh: bool = False) -> pd.DataFrame:
    """
    Fetches inventory, sales totals (30d and 60d), stockout counts (30d and 60d),
    and metadata for items from the curated qualifying-items view.
    """
    current_time = time.time()
    
    if not force_refresh and tag_name in _bq_tag_cache:
        cached_data, timestamp = _bq_tag_cache[tag_name]
        if current_time - timestamp < CACHE_TTL:
            return cached_data

    query = f"""
        WITH 
        date_spine_60 AS (
          SELECT day FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY), CURRENT_DATE())) AS day
        ),
        date_spine_30 AS (
          SELECT day FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), CURRENT_DATE())) AS day
        ),
        qualified_items AS (
            SELECT item_id FROM `{QUALIFIED_ITEMS_VIEW}`
        ),
        latest_item AS (
            SELECT * FROM `{LS_DATASET}.item_history`
            WHERE id IN (SELECT item_id FROM qualified_items)
            QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_item_shop AS (
            SELECT * FROM `{LS_DATASET}.item_shop_history`
            WHERE item_id IN (SELECT id FROM latest_item)
            AND shop_id > 0
            QUALIFY ROW_NUMBER() OVER(PARTITION BY item_id, shop_id ORDER BY updated_time DESC) = 1
        ),
        item_shop_history_all AS (
          SELECT 
            item_id, 
            shop_id as location_id, 
            qoh, 
            DATE(updated_time) as change_date
          FROM `{LS_DATASET}.item_shop_history`
          WHERE item_id IN (SELECT id FROM latest_item)
          AND shop_id > 0
        ),
        daily_qoh_mapped_60 AS (
          SELECT 
            d.day,
            ish.item_id,
            ish.location_id,
            LAST_VALUE(item_shop_history_all.qoh IGNORE NULLS) OVER (
              PARTITION BY ish.item_id, ish.location_id 
              ORDER BY d.day 
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) as daily_qoh
          FROM date_spine_60 d
          CROSS JOIN (SELECT DISTINCT item_id, location_id FROM item_shop_history_all) ish
          LEFT JOIN item_shop_history_all ON item_shop_history_all.change_date = d.day 
            AND item_shop_history_all.item_id = ish.item_id 
            AND item_shop_history_all.location_id = ish.location_id
        ),
        stockouts AS (
          SELECT 
            item_id, 
            location_id, 
            COUNTIF(daily_qoh <= 0) as days_out_of_stock_60,
            COUNTIF(daily_qoh <= 0 AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)) as days_out_of_stock_30
          FROM daily_qoh_mapped_60
          GROUP BY 1, 2
        ),
        sales_totals AS (
          SELECT 
            sl.item_id, 
            sl.shop_id as location_id, 
            SUM(CASE WHEN DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) THEN sl.unit_quantity ELSE 0 END) as total_units_sold_30,
            SUM(sl.unit_quantity) as total_units_sold_60
          FROM `{LS_DATASET}.sale_line_history` sl
          JOIN `{LS_DATASET}.sale_history` s ON sl.sale_id = s.id
          WHERE sl.item_id IN (SELECT id FROM latest_item)
            AND s.completed = TRUE
            AND s.voided = FALSE
            AND DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
          GROUP BY 1, 2
        ),
        open_pos AS (
            SELECT
                ol.item_id,
                o.shop_id AS location_id,
                SUM(ol.quantity - COALESCE(ol.num_received, 0)) AS on_order_units
            FROM `{LS_DATASET}.order_line_history` ol
            JOIN `{LS_DATASET}.order_history` o ON ol.order_id = o.id
            WHERE ol.item_id IN (SELECT id FROM latest_item)
              AND o.complete = FALSE 
              AND o.archived = FALSE
            GROUP BY 1, 2
        ),
        vendors AS (
            SELECT id as vendor_id, name as vendor_name FROM `{LS_DATASET}.vendor_history` QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        brands AS (
            SELECT id as manufacturer_id, name as brand_name FROM `{LS_DATASET}.manufacturer_history` QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        categories AS (
            SELECT id as category_id, full_path_name as category_name FROM `{LS_DATASET}.category_history` QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        )
        
        SELECT 
          ih.system_sku as sku,
          ih.id as item_id,
          ih.description,
          ih.default_vendor_id as vendor_id,
          v.vendor_name as vendor,
          b.brand_name as brand,
          c.category_name as category,
          current_ish.shop_id as location_id,
          COALESCE(st.total_units_sold_30, 0) as total_units_sold_30,
          COALESCE(st.total_units_sold_60, 0) as total_units_sold_60,
          COALESCE(sc.days_out_of_stock_30, 0) as days_out_of_stock_30,
          COALESCE(sc.days_out_of_stock_60, 0) as days_out_of_stock_60,
          current_ish.qoh as current_qoh,
          COALESCE(p.on_order_units, 0) as on_order,
          current_ish.reorder_point as current_reorder_point,
          current_ish.reorder_level as current_desired_level
        FROM latest_item ih
        JOIN latest_item_shop current_ish ON ih.id = current_ish.item_id
        LEFT JOIN sales_totals st ON ih.id = st.item_id AND current_ish.shop_id = st.location_id
        LEFT JOIN stockouts sc ON ih.id = sc.item_id AND current_ish.shop_id = sc.location_id
        LEFT JOIN open_pos p ON ih.id = p.item_id AND current_ish.shop_id = p.location_id
        LEFT JOIN vendors v ON ih.default_vendor_id = v.vendor_id
        LEFT JOIN brands b ON ih.manufacturer_id = b.manufacturer_id
        LEFT JOIN categories c ON ih.category_id = c.category_id
    """
    client = get_bq_client()
    df = client.query(query).to_dataframe()
    _bq_tag_cache[tag_name] = (df, time.time())
    return df
