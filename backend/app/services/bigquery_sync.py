from google.cloud import bigquery
import pandas as pd
import os
import json
import time
import statistics

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
SHOPIFY_DATASET = os.getenv("SHOPIFY_DATASET", "bici-klaviyo-datasync.shopifybici")
QUALIFIED_ITEMS_VIEW = os.getenv("QUALIFIED_ITEMS_VIEW", f"{APP_DATASET}.replen_qualified_items")
TARGET_SHOP_IDS = (2, 3, 20)
# The business went through bankruptcy in fall 2023; sales before 2024 are
# distorted and unreliable for forecasting. Never let history reach earlier than
# this, so seasonal indices and the demand baseline use clean data only.
RELIABLE_HISTORY_START = os.getenv("RELIABLE_HISTORY_START", "2024-01-01")
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

def log_shopify_eta_writeback(log_data: dict):
    """Best-effort audit of a Shopify special-order ETA edit. Streams to a
    `shopify_eta_writeback_logs` table; swallows all errors (incl. a missing table) so an
    audit-logging hiccup never fails the user's edit."""
    table_id = f"{APP_DATASET}.shopify_eta_writeback_logs"
    try:
        client = get_bq_client()
        errors = client.insert_rows_json(table_id, [log_data])
        if errors:
            print(f"BigQuery Shopify ETA Log Errors: {errors}")
    except Exception as e:
        print(f"Failed to log Shopify ETA writeback to BigQuery: {e}")

def get_recommendation_runs(limit: int = 50):
    """Fetches historical runs from BigQuery."""
    query = f"""
        SELECT * FROM `{APP_DATASET}.replen_recommendation_runs`
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
    client = get_bq_client()
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

def get_shopify_special_orders():
    """
    Fetches open Shopify special orders (orders tagged 'SO', not yet fulfilled) from the
    Fivetran Shopify dataset, one row per (order x line SKU). The customer-promised expected
    date comes from the `special_order_eta` order metafield (namespace 'custom', type date);
    it is LEFT-joined, so orders without an ETA are still included.

    Returns a list of dicts: {order_id, order_name, email, fulfillment_status,
    financial_status, created_at (ISO), eta (ISO date or None), sku}. Returns [] on any
    failure so the (Lightspeed-based) special-order triage never breaks when Shopify/BigQuery
    is unavailable.
    """
    query = f"""
        WITH so AS (
          SELECT o.id AS order_id, o.name AS order_name, LOWER(TRIM(o.email)) AS email,
                 o.display_fulfillment_status AS fulfillment_status,
                 o.display_financial_status AS financial_status, o.created_at
          FROM `{SHOPIFY_DATASET}.order` o
          JOIN `{SHOPIFY_DATASET}.order_tag` ot ON ot.order_id = o.id
          WHERE LOWER(ot.value) = 'so'
            AND o.display_fulfillment_status != 'FULFILLED'
            -- Exclude refunded / voided / cancelled / archived / test / deleted orders so the
            -- Shopify consideration window only holds live, financially-sound special orders.
            AND o.display_financial_status NOT IN ('REFUNDED', 'PARTIALLY_REFUNDED', 'VOIDED')
            AND o.cancelled_at IS NULL
            AND COALESCE(o.closed, FALSE) = FALSE
            AND COALESCE(o.test, FALSE) = FALSE
            AND COALESCE(o._fivetran_deleted, FALSE) = FALSE
        ),
        eta AS (
          SELECT owner_id, value AS eta
          FROM `{SHOPIFY_DATASET}.metafield`
          WHERE key = 'special_order_eta' AND UPPER(owner_resource) = 'ORDER'
        )
        SELECT
          CAST(so.order_id AS STRING) AS order_id,
          so.order_name, so.email, so.fulfillment_status, so.financial_status,
          FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%S%z', so.created_at) AS created_at,
          eta.eta, ol.sku
        FROM so
        LEFT JOIN eta ON eta.owner_id = so.order_id
        JOIN `{SHOPIFY_DATASET}.order_line` ol ON ol.order_id = so.order_id
    """
    try:
        client = get_bq_client()
        return [dict(row) for row in client.query(query).result()]
    except Exception as e:
        print(f"Failed to fetch Shopify special orders: {e}")
        return []


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
    client = get_bq_client()
    client.query(merge_query, job_config=job_config).result()

def ensure_po_tables():
    """Creates the purchase-order draft, line, and push-log tables if absent."""
    client = get_bq_client()
    client.query(f"""
        CREATE TABLE IF NOT EXISTS `{APP_DATASET}.replen_po_drafts` (
            draft_id STRING NOT NULL,
            vendor_id STRING,
            vendor_name STRING,
            shop_id STRING,
            status STRING,
            created_by STRING,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            lightspeed_order_id STRING,
            notes STRING
        )
    """).result()
    client.query(f"""
        CREATE TABLE IF NOT EXISTS `{APP_DATASET}.replen_po_draft_lines` (
            draft_id STRING NOT NULL,
            sku STRING,
            item_id STRING,
            location_id STRING,
            quantity INT64,
            unit_cost FLOAT64,
            source STRING,
            recommendation_run_id STRING,
            reconciliation STRING,
            target_lightspeed_order_id STRING
        )
    """).result()
    client.query(f"""
        CREATE TABLE IF NOT EXISTS `{APP_DATASET}.replen_po_push_log` (
            push_id STRING,
            draft_id STRING,
            sku STRING,
            item_id STRING,
            location_id STRING,
            action STRING,
            quantity INT64,
            lightspeed_order_id STRING,
            lightspeed_order_line_id STRING,
            status STRING,
            error_message STRING,
            triggered_by STRING,
            created_at TIMESTAMP
        )
    """).result()

def create_po_draft(draft: dict, lines: list):
    """
    Persists a PO draft header and its lines. Uses load jobs (not streaming
    inserts) so the rows are immediately mutable by later edit/delete/push DML.
    """
    ensure_po_tables()
    client = get_bq_client()
    append = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_json([draft], f"{APP_DATASET}.replen_po_drafts", job_config=append).result()
    if lines:
        client.load_table_from_json(lines, f"{APP_DATASET}.replen_po_draft_lines", job_config=append).result()

def get_po_drafts(status: str = None):
    """Lists PO draft headers, newest first, optionally filtered by status."""
    ensure_po_tables()
    where = "WHERE status = @status" if status else ""
    query = f"""
        SELECT * FROM `{APP_DATASET}.replen_po_drafts`
        {where}
        ORDER BY created_at DESC
    """
    params = [bigquery.ScalarQueryParameter("status", "STRING", status)] if status else []
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        client = get_bq_client()
        return [dict(row) for row in client.query(query, job_config=job_config).result()]
    except Exception as e:
        print(f"Failed to fetch PO drafts: {e}")
        return []

def get_po_draft(draft_id: str):
    """Fetches a single PO draft header with its line items."""
    ensure_po_tables()
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("draft_id", "STRING", draft_id)]
    )
    header_rows = list(client.query(
        f"SELECT * FROM `{APP_DATASET}.replen_po_drafts` WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result())
    if not header_rows:
        return None
    draft = dict(header_rows[0])
    line_rows = client.query(
        f"SELECT * FROM `{APP_DATASET}.replen_po_draft_lines` WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result()
    draft["lines"] = [dict(row) for row in line_rows]
    return draft

def update_po_draft_lines(draft_id: str, lines: list):
    """Replaces all lines for a draft (delete + reload) and bumps updated_at."""
    ensure_po_tables()
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("draft_id", "STRING", draft_id)]
    )
    client.query(
        f"DELETE FROM `{APP_DATASET}.replen_po_draft_lines` WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result()
    if lines:
        append = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        client.load_table_from_json(lines, f"{APP_DATASET}.replen_po_draft_lines", job_config=append).result()
    client.query(
        f"UPDATE `{APP_DATASET}.replen_po_drafts` SET updated_at = CURRENT_TIMESTAMP() WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result()

def update_po_draft_status(draft_id: str, status: str, lightspeed_order_id: str = None):
    """Updates a draft's status (and optionally the resulting Lightspeed order id)."""
    ensure_po_tables()
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("draft_id", "STRING", draft_id),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("lightspeed_order_id", "STRING", lightspeed_order_id),
        ]
    )
    client.query(f"""
        UPDATE `{APP_DATASET}.replen_po_drafts`
        SET status = @status,
            lightspeed_order_id = COALESCE(@lightspeed_order_id, lightspeed_order_id),
            updated_at = CURRENT_TIMESTAMP()
        WHERE draft_id = @draft_id
    """, job_config=job_config).result()

def delete_po_draft(draft_id: str):
    """Deletes a draft header and its lines."""
    ensure_po_tables()
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("draft_id", "STRING", draft_id)]
    )
    client.query(
        f"DELETE FROM `{APP_DATASET}.replen_po_draft_lines` WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result()
    client.query(
        f"DELETE FROM `{APP_DATASET}.replen_po_drafts` WHERE draft_id = @draft_id",
        job_config=job_config,
    ).result()

def log_po_push(log_rows: list):
    """Streams PO push audit rows to BigQuery (append-only, like writeback logs)."""
    if not log_rows:
        return
    table_id = f"{APP_DATASET}.replen_po_push_log"
    try:
        client = get_bq_client()
        errors = client.insert_rows_json(table_id, log_rows)
        if errors:
            print(f"BigQuery PO Push Log Errors: {errors}")
    except Exception as e:
        print(f"Failed to log PO push to BigQuery: {e}")

def ensure_brand_sourcing_rules_table():
    """Creates the brand sourcing rules table if it does not already exist."""
    query = f"""
        CREATE TABLE IF NOT EXISTS `{APP_DATASET}.replen_brand_sourcing_rules` (
            brand_name STRING NOT NULL,
            preferred_vendor_id STRING,
            preferred_vendor_name STRING,
            active BOOL,
            notes STRING,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            updated_by STRING
        )
    """
    client = get_bq_client()
    client.query(query).result()

def get_brand_sourcing_rules_map() -> dict:
    """Fetches active brand sourcing rules keyed by brand name."""
    cached = _cache_get(_brand_sourcing_rules_map_cache, "active")
    if cached is not None:
        return cached

    try:
        ensure_brand_sourcing_rules_table()
        query = f"""
            SELECT
                brand_name,
                preferred_vendor_id,
                preferred_vendor_name,
                active,
                notes,
                created_at,
                updated_at,
                updated_by
            FROM `{APP_DATASET}.replen_brand_sourcing_rules`
            WHERE active = TRUE
        """
        client = get_bq_client()
        rows = client.query(query).result()
        rules = {row["brand_name"]: dict(row) for row in rows}
        _cache_set(_brand_sourcing_rules_map_cache, "active", rules)
        return rules
    except Exception as e:
        print(f"Failed to fetch brand sourcing rules: {e}")
        return {}

def upsert_brand_sourcing_rule(rule_data: dict):
    """Upserts or deactivates a brand preferred vendor mapping."""
    ensure_brand_sourcing_rules_table()
    table_id = f"{APP_DATASET}.replen_brand_sourcing_rules"
    active = bool(rule_data.get("active", True))
    preferred_vendor_id = rule_data.get("preferred_vendor_id")
    preferred_vendor_name = rule_data.get("preferred_vendor_name")

    if not preferred_vendor_id:
        active = False
        preferred_vendor_id = None
        preferred_vendor_name = None

    merge_query = f"""
        MERGE `{table_id}` T
        USING (SELECT @brand_name AS brand_name) S
        ON T.brand_name = S.brand_name
        WHEN MATCHED THEN
            UPDATE SET
                preferred_vendor_id = @preferred_vendor_id,
                preferred_vendor_name = @preferred_vendor_name,
                active = @active,
                notes = @notes,
                updated_at = CURRENT_TIMESTAMP(),
                updated_by = @updated_by
        WHEN NOT MATCHED THEN
            INSERT (
                brand_name,
                preferred_vendor_id,
                preferred_vendor_name,
                active,
                notes,
                created_at,
                updated_at,
                updated_by
            )
            VALUES (
                @brand_name,
                @preferred_vendor_id,
                @preferred_vendor_name,
                @active,
                @notes,
                CURRENT_TIMESTAMP(),
                CURRENT_TIMESTAMP(),
                @updated_by
            )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("brand_name", "STRING", rule_data["brand_name"]),
            bigquery.ScalarQueryParameter("preferred_vendor_id", "STRING", preferred_vendor_id),
            bigquery.ScalarQueryParameter("preferred_vendor_name", "STRING", preferred_vendor_name),
            bigquery.ScalarQueryParameter("active", "BOOL", active),
            bigquery.ScalarQueryParameter("notes", "STRING", rule_data.get("notes")),
            bigquery.ScalarQueryParameter("updated_by", "STRING", rule_data.get("updated_by", "Dashboard")),
        ]
    )
    client = get_bq_client()
    client.query(merge_query, job_config=job_config).result()
    invalidate_brand_sourcing_cache()

def fetch_vendor_name_map(vendor_ids: list) -> dict:
    if not vendor_ids:
        return {}

    query = f"""
        WITH latest_snapshot_date AS (
            SELECT MAX(snapshot_date_local) AS snapshot_date_local
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        )
        SELECT
            CAST(po_vendor_id_any AS STRING) AS vendor_id,
            ARRAY_AGG(
                COALESCE(po_vendor_name_any, default_vendor) IGNORE NULLS
                ORDER BY COALESCE(po_vendor_name_any, default_vendor)
                LIMIT 1
            )[SAFE_OFFSET(0)] AS vendor_name
        FROM `{LS_DATASET}.v_master_snapshot_latest` s
        CROSS JOIN latest_snapshot_date lsd
        WHERE s.snapshot_date_local = lsd.snapshot_date_local
            AND CAST(po_vendor_id_any AS STRING) IN UNNEST(@vendor_ids)
            AND po_vendor_id_any IS NOT NULL
        GROUP BY po_vendor_id_any
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("vendor_ids", "STRING", [str(vendor_id) for vendor_id in vendor_ids]),
        ]
    )
    client = get_bq_client()
    rows = client.query(query, job_config=job_config).result()
    vendor_names = {}
    for row in rows:
        row_dict = dict(row)
        if row_dict.get("vendor_name"):
            vendor_names[str(row_dict["vendor_id"])] = row_dict["vendor_name"]
    return vendor_names

def fetch_active_vendor_lead_times(active_days: int = 90, force_refresh: bool = False) -> dict:
    """
    Returns vendors with at least one usable received lead-time sample in the
    active window, plus median received lead times and configured brand mappings.
    """
    cache_key = active_days
    if not force_refresh:
        cached = _cache_get(_active_vendor_lead_time_cache, cache_key)
        if cached is not None:
            return cached

    query = f"""
        WITH lead_time_samples AS (
            SELECT
                CAST(vendor_id AS STRING) AS vendor_id,
                shop_id AS location_id,
                TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY) AS lead_time_day,
                order_id,
                po_ordered_at,
                first_received_at
            FROM `{LS_DATASET}.po_report`
            WHERE vendor_id IS NOT NULL
                AND po_ordered_at IS NOT NULL
                AND first_received_at IS NOT NULL
                AND DATE(first_received_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @active_days DAY)
                AND TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY) BETWEEN 0 AND 40
        ),
        active_vendors AS (
            SELECT
                vendor_id,
                COUNT(DISTINCT order_id) AS active_po_count,
                COUNT(DISTINCT order_id) AS active_sample_count,
                MAX(first_received_at) AS last_po_ordered_at
            FROM lead_time_samples
            GROUP BY vendor_id
        ),
        lead_times AS (
            SELECT
                vendor_id,
                location_id,
                CEIL(PERCENTILE_CONT(lead_time_day, 0.5) OVER (
                    PARTITION BY vendor_id, location_id
                )) AS lead_time_days,
                COUNT(DISTINCT order_id) OVER (
                    PARTITION BY vendor_id, location_id
                ) AS po_count
            FROM lead_time_samples
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY vendor_id, location_id
                ORDER BY vendor_id
            ) = 1
        )
        SELECT
            av.vendor_id,
            av.active_po_count,
            av.last_po_ordered_at,
            ARRAY(
                SELECT AS STRUCT
                    lt.location_id AS location_id,
                    lt.lead_time_days AS lead_time_days,
                    lt.po_count AS po_count
                FROM lead_times lt
                WHERE lt.vendor_id = av.vendor_id
                ORDER BY lt.location_id
            ) AS location_lead_times
        FROM active_vendors av
        ORDER BY av.vendor_id
    """
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("active_days", "INT64", active_days),
        ]
    )
    rows = client.query(query, job_config=job_config).result()

    warnings = []
    data = []
    for row in rows:
        vendor = dict(row)
        vendor["vendor_id"] = str(vendor["vendor_id"])
        vendor["vendor_name"] = f"Vendor {vendor['vendor_id']}"
        vendor["configured_brands"] = []
        data.append(vendor)

    vendor_ids = [vendor["vendor_id"] for vendor in data]

    try:
        vendor_names = fetch_vendor_name_map(vendor_ids)
        for vendor in data:
            if vendor_names.get(vendor["vendor_id"]):
                vendor["vendor_name"] = vendor_names[vendor["vendor_id"]]
    except Exception as e:
        warning = f"Vendor names could not be loaded: {e}"
        print(warning)
        warnings.append(warning)

    try:
        brand_rules = get_brand_sourcing_rules_map()
    except Exception as e:
        warning = f"Configured brand mappings could not be loaded: {e}"
        print(warning)
        warnings.append(warning)
        brand_rules = {}

    configured_brands_by_vendor = {}
    for rule in brand_rules.values():
        vendor_id = rule.get("preferred_vendor_id")
        brand_name = rule.get("brand_name")
        if vendor_id and brand_name:
            configured_brands_by_vendor.setdefault(str(vendor_id), []).append(brand_name)

    lead_time_vendor_count = 0
    for vendor in data:
        vendor["configured_brands"] = sorted(configured_brands_by_vendor.get(str(vendor["vendor_id"]), []))
        if vendor.get("location_lead_times"):
            lead_time_vendor_count += 1

    result = {
        "data": sorted(data, key=lambda vendor: (vendor.get("vendor_name") or "", vendor.get("vendor_id") or "")),
        "meta": {
            "active_days": active_days,
            "active_vendor_count": len(data),
            "lead_time_vendor_count": lead_time_vendor_count,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "warnings": warnings,
        },
    }
    _cache_set(_active_vendor_lead_time_cache, cache_key, result)
    return result

def fetch_brand_sourcing_rules(force_refresh: bool = False) -> list:
    """Returns all current brands with any configured preferred vendor mapping."""
    cache_key = "all"
    if not force_refresh:
        cached = _cache_get(_brand_sourcing_rules_cache, cache_key)
        if cached is not None:
            return cached

    brands_query = f"""
        WITH latest_snapshot_date AS (
            SELECT MAX(snapshot_date_local) AS snapshot_date_local
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        )
        SELECT
            brand_name,
            COUNT(DISTINCT item_id) AS item_count
        FROM `{LS_DATASET}.v_master_snapshot_latest` s
        CROSS JOIN latest_snapshot_date lsd
        WHERE s.snapshot_date_local = lsd.snapshot_date_local
            AND COALESCE(s.item_archived, FALSE) = FALSE
            AND brand_name IS NOT NULL
            AND TRIM(brand_name) != ''
        GROUP BY brand_name
        ORDER BY brand_name
    """
    client = get_bq_client()
    rows = client.query(brands_query).result()
    data = []
    for row in rows:
        brand = dict(row)
        brand.update({
            "preferred_vendor_id": None,
            "preferred_vendor_name": None,
            "active": False,
            "notes": None,
            "created_at": None,
            "updated_at": None,
            "updated_by": None,
        })
        data.append(brand)

    try:
        ensure_brand_sourcing_rules_table()
        rules_query = f"""
            SELECT
                brand_name,
                preferred_vendor_id,
                preferred_vendor_name,
                active,
                notes,
                created_at,
                updated_at,
                updated_by
            FROM `{APP_DATASET}.replen_brand_sourcing_rules`
        """
        rules = {row["brand_name"]: dict(row) for row in client.query(rules_query).result()}
        for brand in data:
            rule = rules.get(brand["brand_name"])
            if rule:
                brand.update(rule)
                brand["active"] = bool(rule.get("active"))
    except Exception as e:
        print(f"Failed to attach brand sourcing rules: {e}")

    _cache_set(_brand_sourcing_rules_cache, cache_key, data)
    return data

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

def fetch_monthly_sales_history(years: int = 3) -> pd.DataFrame:
    """
    Phase 2 (forecasting enrichment) -- multi-year MONTHLY sales aggregation used
    to build hierarchical category + per-item seasonal profiles (see
    ``app.services.forecasting``).

    Reads the canonical ``sales_master_view`` (LS-API-backed, favoured over raw
    Fivetran tables), which already represents completed sales and pre-decomposes
    the category tree. Per BICI's demand definition, special-order / layaway /
    workorder lines ARE counted (they are real demand once converted); only
    warranty workorder lines are excluded. Returns one row per
    (item_id, location_id, sales_month) with the category hierarchy attached so
    the caller can compute seasonal indices at any category level.

    Validated against live BigQuery (2026-06-16).
    """
    query = f"""
        SELECT
            item_id,
            shop_id_int AS location_id,
            ANY_VALUE(category_top_level) AS category_top_level,
            ANY_VALUE(category_level_2) AS category_level_2,
            ANY_VALUE(category_level_3) AS category_level_3,
            ANY_VALUE(category_level_4) AS category_level_4,
            ANY_VALUE(category_path) AS category_path,
            ANY_VALUE(brand_name) AS brand_name,
            DATE_TRUNC(sale_date, MONTH) AS sales_month,
            EXTRACT(MONTH FROM sale_date) AS month_of_year,
            EXTRACT(YEAR FROM sale_date) AS sales_year,
            SUM(units_sold) AS total_units_sold
        FROM
            `{LS_DATASET}.sales_master_view`
        WHERE
            -- Never reach earlier than the reliable-data start (post-bankruptcy);
            -- a plain N-year lookback would pull in distorted pre-2024 sales.
            sale_date >= GREATEST(DATE_SUB(CURRENT_DATE(), INTERVAL @years YEAR), DATE(@reliable_start))
            -- Exclude the in-progress current month: a partial month must never be
            -- treated as a complete one (it would deflate seasonal indices, the
            -- monthly baseline, and the last history bar).
            AND sale_date < DATE_TRUNC(CURRENT_DATE(), MONTH)
            AND shop_id_int IN {TARGET_SHOP_IDS}
            AND NOT COALESCE(is_workorder_warranty_line, FALSE)
        GROUP BY
            item_id,
            location_id,
            sales_month,
            month_of_year,
            sales_year
        ORDER BY
            item_id,
            location_id,
            sales_month
    """

    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("years", "INT64", years),
            bigquery.ScalarQueryParameter("reliable_start", "STRING", RELIABLE_HISTORY_START),
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
CACHE_TTL = 300          # 5 minutes — tagged items, lead times
ADMIN_CACHE_TTL_SECONDS = 900  # 15 minutes — brand sourcing rules, active vendor lead times
_active_vendor_lead_time_cache = {}
_brand_sourcing_rules_cache = {}
_brand_sourcing_rules_map_cache = {}
_brand_vendor_sourcing_cache = {}

def _cache_get(cache: dict, key):
    cached = cache.get(key)
    if not cached:
        return None
    data, timestamp = cached
    if time.time() - timestamp < ADMIN_CACHE_TTL_SECONDS:
        return data
    return None

def _cache_set(cache: dict, key, data):
    cache[key] = (data, time.time())

def invalidate_brand_sourcing_cache():
    _brand_sourcing_rules_cache.clear()
    _brand_sourcing_rules_map_cache.clear()
    _active_vendor_lead_time_cache.clear()
    _brand_vendor_sourcing_cache.clear()

def fetch_lead_times(lookback_months: int = 3, force_refresh: bool = False) -> pd.DataFrame:
    """
    Fetches median vendor lead times per location from the po_report table.
    """
    current_time = time.time()
    
    if not force_refresh and lookback_months in _bq_lead_time_cache:
        cached_data, timestamp = _bq_lead_time_cache[lookback_months]
        if current_time - timestamp < CACHE_TTL:
            return cached_data

    query = f"""
        WITH lead_time_samples AS (
            SELECT
                vendor_id,
                shop_id,
                order_id,
                TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY) AS lead_time_day
            FROM
                `{LS_DATASET}.po_report`
            WHERE
                po_ordered_at IS NOT NULL
                AND first_received_at IS NOT NULL
                AND DATE(po_ordered_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_months MONTH)
                -- Filter out obvious data entry errors and pre-booked POs (limit to replenishment POs)
                AND TIMESTAMP_DIFF(first_received_at, po_ordered_at, DAY) BETWEEN 0 AND 40
        )
        SELECT
            vendor_id,
            shop_id AS location_id,
            CEIL(PERCENTILE_CONT(lead_time_day, 0.5) OVER (
                PARTITION BY vendor_id, shop_id
            )) AS lead_time_days,
            COUNT(order_id) OVER (
                PARTITION BY vendor_id, shop_id
            ) AS po_count
        FROM
            lead_time_samples
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY vendor_id, shop_id
            ORDER BY vendor_id
        ) = 1
        ORDER BY
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


def build_lead_time_lookup(lookback_months: int = 3, force_refresh: bool = False) -> tuple:
    """
    Reshapes the working fetch_lead_times() result into two in-memory lookups for fast per-SKU
    sourcing decisions:
      - by_vendor_location: {(vendor_id, location_id): lead_time_days}  — exact store lead time
      - by_vendor:          {vendor_id: median lead_time_days}          — fallback when a store
                                                                          has no sample of its own
    Both keys are normalized to strings. (Mirrors fetch_lead_times rather than
    fetch_active_vendor_lead_times, which currently errors on a correlated subquery.)
    """
    records = fetch_lead_times(lookback_months=lookback_months, force_refresh=force_refresh).to_dict("records")
    by_vendor_location: dict = {}
    per_vendor_samples: dict = {}
    for row in records:
        vid = row.get("vendor_id")
        lid = row.get("location_id")
        lt = row.get("lead_time_days")
        if vid is None or lt is None:
            continue
        vid = str(vid)
        lt = float(lt)
        if lid is not None:
            by_vendor_location[(vid, str(lid))] = lt
        per_vendor_samples.setdefault(vid, []).append(lt)
    by_vendor = {vid: statistics.median(samples) for vid, samples in per_vendor_samples.items() if samples}
    return by_vendor_location, by_vendor


def fetch_item_brands(item_ids: list) -> dict:
    """{item_id (str): brand_name} for the given item ids, from the latest master snapshot.
    Used to resolve a special order's SKU to its brand so brand-level sourcing can be looked up."""
    ids = sorted({str(i) for i in item_ids if i is not None and str(i) not in ("", "0")})
    if not ids:
        return {}
    query = f"""
        SELECT DISTINCT
            CAST(item_id AS STRING) AS item_id,
            brand_name
        FROM `{LS_DATASET}.v_master_snapshot_latest`
        WHERE CAST(item_id AS STRING) IN UNNEST(@item_ids)
            AND brand_name IS NOT NULL AND brand_name != ''
    """
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("item_ids", "STRING", ids)]
    )
    rows = client.query(query, job_config=job_config).result()
    return {row["item_id"]: row["brand_name"] for row in rows}


def fetch_brand_vendor_sourcing(
    lookback_days: int = 365, min_distinct_items: int = 3, force_refresh: bool = False
) -> dict:
    """
    Returns {brand_name: [{vendor_id, vendor_name, distinct_items}, ...]} — the vendors each brand
    has actually been purchased from in the recency window, ranked by how many distinct items of
    that brand they supplied.

    "Which vendors carry a brand" is stored nowhere, so it's derived empirically from PO line
    history joined to the item->brand map. A vendor must have supplied at least `min_distinct_items`
    distinct items of the brand to count, which drops one-off / mis-tagged noise vendors (e.g. a
    single Shimano item bought once from a non-distributor). Cached for ADMIN_CACHE_TTL_SECONDS.

    NOTE: the item->brand side is de-duplicated (SELECT DISTINCT) before the join — without it the
    snapshot's per-itemshop rows fan the join out massively.
    """
    cache_key = (lookback_days, min_distinct_items)
    if not force_refresh:
        cached = _cache_get(_brand_vendor_sourcing_cache, cache_key)
        if cached is not None:
            return cached

    query = f"""
        WITH item_brand AS (
            SELECT DISTINCT
                CAST(item_id AS STRING) AS item_id,
                brand_name
            FROM `{LS_DATASET}.v_master_snapshot_latest`
            WHERE brand_name IS NOT NULL AND brand_name != ''
        ),
        po_lines AS (
            SELECT
                CAST(orderLine_itemID AS STRING) AS item_id,
                CAST(order_vendorID AS STRING) AS vendor_id,
                vendor_name
            FROM `{LS_DATASET}.LS_purchase_order_line_history`
            WHERE order_vendorID IS NOT NULL
                AND order_vendorID != 0
                AND DATE(order_timeStamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)
        )
        SELECT
            ib.brand_name,
            p.vendor_id,
            ANY_VALUE(p.vendor_name) AS vendor_name,
            COUNT(DISTINCT p.item_id) AS distinct_items
        FROM po_lines p
        JOIN item_brand ib USING (item_id)
        GROUP BY ib.brand_name, p.vendor_id
        HAVING COUNT(DISTINCT p.item_id) >= @min_distinct_items
        ORDER BY ib.brand_name, distinct_items DESC
    """
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lookback_days", "INT64", lookback_days),
            bigquery.ScalarQueryParameter("min_distinct_items", "INT64", min_distinct_items),
        ]
    )
    rows = client.query(query, job_config=job_config).result()

    sourcing: dict = {}
    for row in rows:
        d = dict(row)
        sourcing.setdefault(d["brand_name"], []).append({
            "vendor_id": str(d["vendor_id"]),
            "vendor_name": d.get("vendor_name") or f"Vendor {d['vendor_id']}",
            "distinct_items": int(d["distinct_items"]),
        })

    _cache_set(_brand_vendor_sourcing_cache, cache_key, sourcing)
    return sourcing


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
    client = get_bq_client()
    return client.query(query, job_config=job_config).to_dataframe()

_bq_tag_cache = {}

def fetch_tagged_items_metrics(tag_name: str = "auto-replen", force_refresh: bool = False) -> pd.DataFrame:
    """
    Fetches product, inventory, sales, and PO metrics from the trusted latest
    master snapshot view for qualified replenishment items. Stockout counts are
    still calculated from item_shop_history because the snapshot view does not
    expose trailing stockout days.
    """
    current_time = time.time()
    
    if not force_refresh and tag_name in _bq_tag_cache:
        cached_data, timestamp = _bq_tag_cache[tag_name]
        if current_time - timestamp < CACHE_TTL:
            return cached_data

    query = f"""
        WITH 
        date_spine_60 AS (
          SELECT day FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL 59 DAY), CURRENT_DATE())) AS day
        ),
        qualified_items AS (
          SELECT item_id FROM `{QUALIFIED_ITEMS_VIEW}`
        ),
        latest_snapshot_date AS (
          SELECT MAX(snapshot_date_local) AS snapshot_date_local
          FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        snapshot AS (
          SELECT s.*
          FROM `{LS_DATASET}.v_master_snapshot_latest` s
          CROSS JOIN latest_snapshot_date lsd
          WHERE s.item_id IN (SELECT item_id FROM qualified_items)
            AND s.shop_id IN {TARGET_SHOP_IDS}
            AND s.snapshot_date_local = lsd.snapshot_date_local
            AND COALESCE(s.item_archived, FALSE) = FALSE
        ),
        item_shop_history_all AS (
          SELECT 
            item_id,
            shop_id AS location_id,
            qoh,
            DATE(updated_time) AS change_date
          FROM `{LS_DATASET}.item_shop_history`
          WHERE item_id IN (SELECT item_id FROM qualified_items)
            AND shop_id IN {TARGET_SHOP_IDS}
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
            ) AS daily_qoh
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
            COUNTIF(daily_qoh <= 0) AS days_out_of_stock_60,
            COUNTIF(daily_qoh <= 0 AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 29 DAY)) AS days_out_of_stock_30,
            COUNTIF(daily_qoh <= 0 AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 13 DAY)) AS days_out_of_stock_14
          FROM daily_qoh_mapped_60
          GROUP BY 1, 2
        ),
        latest_sale_lines AS (
          SELECT *
          FROM `{LS_DATASET}.sale_line_history`
          WHERE item_id IN (SELECT item_id FROM qualified_items)
            AND shop_id IN {TARGET_SHOP_IDS}
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_sales AS (
          SELECT *
          FROM `{LS_DATASET}.sale_history`
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        sales_14 AS (
          SELECT
            sl.item_id,
            sl.shop_id AS location_id,
            SUM(sl.unit_quantity) AS total_units_sold_14,
            COUNT(DISTINCT DATE(sale.complete_time)) AS distinct_sale_days_14
          FROM latest_sale_lines sl
          JOIN latest_sales sale ON sl.sale_id = sale.id
          WHERE sale.completed = TRUE
            AND sale.voided = FALSE
            AND DATE(sale.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 13 DAY)
          GROUP BY 1, 2
        ),
        sale_day_counts AS (
          SELECT
            sl.item_id,
            sl.shop_id AS location_id,
            COUNT(DISTINCT CASE WHEN DATE(sale.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 29 DAY) THEN DATE(sale.complete_time) END) AS distinct_sale_days_30,
            COUNT(DISTINCT DATE(sale.complete_time)) AS distinct_sale_days_60
          FROM latest_sale_lines sl
          JOIN latest_sales sale ON sl.sale_id = sale.id
          WHERE sale.completed = TRUE
            AND sale.voided = FALSE
            AND DATE(sale.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 59 DAY)
          GROUP BY 1, 2
        )
        SELECT
          CAST(s.system_sku AS STRING) AS sku,
          s.item_id,
          s.item_description AS description,
          s.po_vendor_id_any AS vendor_id,
          COALESCE(s.po_vendor_name_any, s.default_vendor) AS vendor,
          s.brand_name AS brand,
          s.category_name AS category,
          s.shop_id AS location_id,
          COALESCE(s14.total_units_sold_14, 0) AS total_units_sold_14,
          COALESCE(s.sales_units_l30d, 0) AS total_units_sold_30,
          COALESCE(s.sales_units_l60d, 0) AS total_units_sold_60,
          COALESCE(s14.distinct_sale_days_14, 0) AS distinct_sale_days_14,
          COALESCE(sdc.distinct_sale_days_30, 0) AS distinct_sale_days_30,
          COALESCE(sdc.distinct_sale_days_60, 0) AS distinct_sale_days_60,
          COALESCE(sc.days_out_of_stock_14, 0) AS days_out_of_stock_14,
          COALESCE(sc.days_out_of_stock_30, 0) AS days_out_of_stock_30,
          COALESCE(sc.days_out_of_stock_60, 0) AS days_out_of_stock_60,
          COALESCE(s.qoh, 0) AS current_qoh,
          COALESCE(s.po_units_remaining, 0) AS on_order,
          COALESCE(s.reorderPoint, 0) AS current_reorder_point,
          COALESCE(s.reorderLevel, 0) AS current_desired_level
        FROM snapshot s
        LEFT JOIN stockouts sc ON s.item_id = sc.item_id AND s.shop_id = sc.location_id
        LEFT JOIN sales_14 s14 ON s.item_id = s14.item_id AND s.shop_id = s14.location_id
        LEFT JOIN sale_day_counts sdc ON s.item_id = sdc.item_id AND s.shop_id = sdc.location_id
    """
    client = get_bq_client()
    df = client.query(query).to_dataframe()
    _bq_tag_cache[tag_name] = (df, time.time())
    return df


def get_replenishment_debug_counts() -> dict:
    """Returns production-safe counts showing where qualifying items drop out."""
    def to_plain_json(value):
        if isinstance(value, list):
            return [to_plain_json(item) for item in value]
        if hasattr(value, "items"):
            return {key: to_plain_json(val) for key, val in value.items()}
        return value

    query = f"""
        WITH
        examples AS (
          SELECT * FROM UNNEST([
            STRUCT(32856 AS item_id, 'should qualify' AS expected),
            STRUCT(98993 AS item_id, 'should qualify' AS expected),
            STRUCT(112947 AS item_id, 'should qualify' AS expected),
            STRUCT(49232 AS item_id, 'should not qualify' AS expected),
            STRUCT(12406 AS item_id, 'should not qualify' AS expected),
            STRUCT(106555 AS item_id, 'should not qualify' AS expected)
          ])
        ),
        qualified_items AS (
          SELECT item_id FROM `{QUALIFIED_ITEMS_VIEW}`
        ),
        latest_item AS (
          SELECT *
          FROM `{LS_DATASET}.item_history`
          WHERE id IN (SELECT item_id FROM qualified_items)
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_target_item_shop AS (
          SELECT *
          FROM `{LS_DATASET}.item_shop_history`
          WHERE item_id IN (SELECT id FROM latest_item)
            AND shop_id IN {TARGET_SHOP_IDS}
          QUALIFY ROW_NUMBER() OVER(PARTITION BY item_id, shop_id ORDER BY updated_time DESC) = 1
        ),
        target_rows_by_shop AS (
          SELECT
            shop_id AS location_id,
            COUNT(*) AS row_count
          FROM latest_target_item_shop
          GROUP BY shop_id
        ),
        example_status AS (
          SELECT
            e.item_id,
            e.expected,
            q.item_id IS NOT NULL AS in_qualified_view,
            li.id IS NOT NULL AS in_latest_item,
            ARRAY_AGG(DISTINCT lis.shop_id IGNORE NULLS ORDER BY lis.shop_id) AS target_shop_ids
          FROM examples e
          LEFT JOIN qualified_items q ON e.item_id = q.item_id
          LEFT JOIN latest_item li ON e.item_id = li.id
          LEFT JOIN latest_target_item_shop lis ON e.item_id = lis.item_id
          GROUP BY e.item_id, e.expected, q.item_id, li.id
        )
        SELECT
          '{APP_DATASET}' AS app_dataset,
          '{LS_DATASET}' AS lightspeed_dataset,
          '{QUALIFIED_ITEMS_VIEW}' AS qualified_items_view,
          (SELECT COUNT(*) FROM qualified_items) AS qualified_item_count,
          (SELECT COUNT(*) FROM latest_item) AS latest_item_count,
          (SELECT COUNT(*) FROM latest_target_item_shop) AS target_item_shop_row_count,
          ARRAY(
            SELECT AS STRUCT location_id, row_count
            FROM target_rows_by_shop
            ORDER BY location_id
          ) AS target_rows_by_shop,
          ARRAY(
            SELECT AS STRUCT item_id, expected, in_qualified_view, in_latest_item, target_shop_ids
            FROM example_status
            ORDER BY item_id
          ) AS example_status
    """
    client = get_bq_client()
    row = dict(next(iter(client.query(query).result())))
    return to_plain_json(row)


def get_item_replenishment_debug(item_id: int) -> dict:
    """Returns raw vs deduped sales and PO counts for a single item."""
    def to_plain_json(value):
        if isinstance(value, list):
            return [to_plain_json(item) for item in value]
        if hasattr(value, "items"):
            return {key: to_plain_json(val) for key, val in value.items()}
        return value

    query = f"""
        WITH
        latest_sale_lines AS (
          SELECT *
          FROM `{LS_DATASET}.sale_line_history`
          WHERE item_id = @item_id
            AND shop_id IN {TARGET_SHOP_IDS}
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_sales AS (
          SELECT *
          FROM `{LS_DATASET}.sale_history`
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        raw_sales AS (
          SELECT
            sl.shop_id AS location_id,
            COUNT(*) AS raw_history_rows,
            COUNT(DISTINCT sl.id) AS distinct_sale_line_ids,
            SUM(CASE WHEN DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) THEN sl.unit_quantity ELSE 0 END) AS raw_units_30d,
            SUM(sl.unit_quantity) AS raw_units_60d
          FROM `{LS_DATASET}.sale_line_history` sl
          JOIN `{LS_DATASET}.sale_history` s ON sl.sale_id = s.id
          WHERE sl.item_id = @item_id
            AND sl.shop_id IN {TARGET_SHOP_IDS}
            AND s.completed = TRUE
            AND s.voided = FALSE
            AND DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
          GROUP BY sl.shop_id
        ),
        deduped_sales AS (
          SELECT
            sl.shop_id AS location_id,
            COUNT(*) AS deduped_rows,
            SUM(CASE WHEN DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) THEN sl.unit_quantity ELSE 0 END) AS deduped_units_30d,
            SUM(sl.unit_quantity) AS deduped_units_60d
          FROM latest_sale_lines sl
          JOIN latest_sales s ON sl.sale_id = s.id
          WHERE s.completed = TRUE
            AND s.voided = FALSE
            AND DATE(s.complete_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
          GROUP BY sl.shop_id
        ),
        latest_order_lines AS (
          SELECT *
          FROM `{LS_DATASET}.order_line_history`
          WHERE item_id = @item_id
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        latest_orders AS (
          SELECT *
          FROM `{LS_DATASET}.order_history`
          QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        raw_open_pos AS (
          SELECT
            o.shop_id AS location_id,
            COUNT(*) AS raw_history_rows,
            COUNT(DISTINCT ol.id) AS distinct_order_line_ids,
            SUM(ol.quantity - COALESCE(ol.num_received, 0)) AS raw_on_order
          FROM `{LS_DATASET}.order_line_history` ol
          JOIN `{LS_DATASET}.order_history` o ON ol.order_id = o.id
          WHERE ol.item_id = @item_id
            AND o.shop_id IN {TARGET_SHOP_IDS}
            AND o.complete = FALSE
            AND o.archived = FALSE
          GROUP BY o.shop_id
        ),
        deduped_open_pos AS (
          SELECT
            o.shop_id AS location_id,
            COUNT(*) AS deduped_rows,
            SUM(ol.quantity - COALESCE(ol.num_received, 0)) AS deduped_on_order
          FROM latest_order_lines ol
          JOIN latest_orders o ON ol.order_id = o.id
          WHERE o.shop_id IN {TARGET_SHOP_IDS}
            AND o.complete = FALSE
            AND o.archived = FALSE
          GROUP BY o.shop_id
        ),
        locations AS (
          SELECT * FROM UNNEST([2, 3, 20]) AS location_id
        )
        SELECT
          @item_id AS item_id,
          ARRAY(
            SELECT AS STRUCT
              l.location_id,
              COALESCE(rs.raw_history_rows, 0) AS raw_sales_history_rows,
              COALESCE(rs.distinct_sale_line_ids, 0) AS distinct_sale_line_ids,
              COALESCE(rs.raw_units_30d, 0) AS raw_history_units_30d,
              COALESCE(ds.deduped_rows, 0) AS deduped_sales_rows,
              COALESCE(ds.deduped_units_30d, 0) AS deduped_units_30d,
              COALESCE(rs.raw_units_60d, 0) AS raw_history_units_60d,
              COALESCE(ds.deduped_units_60d, 0) AS deduped_units_60d
            FROM locations l
            LEFT JOIN raw_sales rs USING (location_id)
            LEFT JOIN deduped_sales ds USING (location_id)
            ORDER BY l.location_id
          ) AS sales_by_location,
          ARRAY(
            SELECT AS STRUCT
              l.location_id,
              COALESCE(rp.raw_history_rows, 0) AS raw_po_history_rows,
              COALESCE(rp.distinct_order_line_ids, 0) AS distinct_order_line_ids,
              COALESCE(rp.raw_on_order, 0) AS raw_history_on_order,
              COALESCE(dp.deduped_rows, 0) AS deduped_po_rows,
              COALESCE(dp.deduped_on_order, 0) AS deduped_on_order
            FROM locations l
            LEFT JOIN raw_open_pos rp USING (location_id)
            LEFT JOIN deduped_open_pos dp USING (location_id)
            ORDER BY l.location_id
          ) AS po_by_location
    """
    client = get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("item_id", "INT64", item_id),
        ]
    )
    row = dict(next(iter(client.query(query, job_config=job_config).result())))
    return to_plain_json(row)
