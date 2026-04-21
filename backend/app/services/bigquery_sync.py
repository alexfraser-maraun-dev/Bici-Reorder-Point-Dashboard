from google.cloud import bigquery
import pandas as pd
import os

# Initialize BigQuery client
client = bigquery.Client()

# NOTE: You will need to replace 'YOUR_PROJECT.YOUR_DATASET' with your actual GCP project and dataset name.
BQ_DATASET = os.getenv("BQ_DATASET", "bici-klaviyo-datasync.light_speed_retailne") # Using assumed default

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


