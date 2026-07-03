"""Seeds pi_tracked_products from the top-revenue SKUs (feature b).

Source is the latest master-snapshot date (the view is a daily per-item+shop
series — aggregating without the date filter inflates revenue by the number of
snapshot dates) joined to item_history for UPC/EAN, which the snapshot lacks.

The MERGE refreshes descriptive/market fields but never touches the manual
controls (pinned / excluded / min_price_override / map_price). Auto-seeded rows
that fall out of the top N are deleted unless pinned or excluded; manual rows are
always kept. is_map comes from the Lightspeed `map` item tag (comma-separated in
item_tags) — harmlessly false everywhere until that tagging effort lands.
"""
from google.cloud import bigquery

from app.services.bigquery_sync import get_bq_client, LS_DATASET
from . import config, repository


def refresh_tracked_products(top_n: int = None) -> int:
    """Runs the seed query and MERGEs into pi_tracked_products. Returns the
    number of source rows."""
    repository.ensure_pi_tables()
    top_n = top_n or config.TOP_REVENUE_COUNT
    client = get_bq_client()

    seed_query = f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        snap AS (
            SELECT
                CAST(item_id AS STRING) AS item_id,
                ANY_VALUE(COALESCE(product_display_name, item_description)) AS title,
                ANY_VALUE(brand_name) AS brand,
                ANY_VALUE(manufacturer_sku) AS sku,
                ANY_VALUE(CAST(system_sku AS STRING)) AS system_sku,
                MAX(item_current_price) AS current_retail,
                MAX(COALESCE(item_default_cost, item_avg_cost)) AS current_cost,
                SUM(COALESCE(sales_revenue_l90d, 0)) AS trailing_revenue_90d,
                LOGICAL_OR(REGEXP_CONTAINS(
                    LOWER(COALESCE(item_tags, '')), r'(^|,)\\s*map\\s*(,|$)'
                )) AS is_map
            FROM `{LS_DATASET}.v_master_snapshot_latest` s
            CROSS JOIN latest
            WHERE s.snapshot_date_local = latest.d
              AND COALESCE(item_archived, FALSE) = FALSE
            GROUP BY item_id
        ),
        upc AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc
            FROM `{LS_DATASET}.item_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        )
        SELECT
            s.item_id, s.title, s.brand, s.sku, s.system_sku,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized,
            s.current_retail, s.current_cost, s.trailing_revenue_90d, s.is_map,
            ROW_NUMBER() OVER (ORDER BY s.trailing_revenue_90d DESC) AS revenue_rank
        FROM snap s
        LEFT JOIN upc u USING (item_id)
        WHERE s.trailing_revenue_90d > 0
        ORDER BY s.trailing_revenue_90d DESC
        LIMIT @top_n
    """

    merge_query = f"""
        MERGE `{repository.T_TRACKED}` T
        USING ({seed_query}) S
        ON T.item_id = S.item_id
        WHEN MATCHED THEN UPDATE SET
            T.title = S.title,
            T.brand = S.brand,
            T.sku = S.sku,
            T.system_sku = S.system_sku,
            T.upc_normalized = S.upc_normalized,
            T.current_retail = S.current_retail,
            T.current_cost = S.current_cost,
            T.trailing_revenue_90d = S.trailing_revenue_90d,
            T.revenue_rank = S.revenue_rank,
            T.is_map = S.is_map,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            updated_at
        ) VALUES (
            S.item_id, S.sku, S.system_sku, S.upc_normalized, S.brand, S.title,
            'auto_top_revenue', FALSE, FALSE, S.revenue_rank,
            S.trailing_revenue_90d, S.current_retail, S.current_cost,
            NULL, S.is_map, NULL, CURRENT_TIMESTAMP()
        )
        WHEN NOT MATCHED BY SOURCE
            AND T.source = 'auto_top_revenue'
            AND COALESCE(T.pinned, FALSE) = FALSE
            AND COALESCE(T.excluded, FALSE) = FALSE
        THEN DELETE
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("top_n", "INT64", int(top_n)),
    ])
    result = client.query(merge_query, job_config=job_config).result()
    repository.invalidate_pi_caches()
    return getattr(result, "num_dml_affected_rows", 0) or 0


def add_manual_tracked_product(item_id: str) -> dict:
    """Pins a single item (from item search) into pi_tracked_products."""
    repository.ensure_pi_tables()
    client = get_bq_client()
    query = f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        snap AS (
            SELECT
                CAST(item_id AS STRING) AS item_id,
                ANY_VALUE(COALESCE(product_display_name, item_description)) AS title,
                ANY_VALUE(brand_name) AS brand,
                ANY_VALUE(manufacturer_sku) AS sku,
                ANY_VALUE(CAST(system_sku AS STRING)) AS system_sku,
                MAX(item_current_price) AS current_retail,
                MAX(COALESCE(item_default_cost, item_avg_cost)) AS current_cost,
                SUM(COALESCE(sales_revenue_l90d, 0)) AS trailing_revenue_90d,
                LOGICAL_OR(REGEXP_CONTAINS(
                    LOWER(COALESCE(item_tags, '')), r'(^|,)\\s*map\\s*(,|$)'
                )) AS is_map
            FROM `{LS_DATASET}.v_master_snapshot_latest` s
            CROSS JOIN latest
            WHERE s.snapshot_date_local = latest.d
              AND CAST(item_id AS STRING) = @item_id
            GROUP BY item_id
        ),
        upc AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc
            FROM `{LS_DATASET}.item_history`
            WHERE CAST(id AS STRING) = @item_id
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        )
        SELECT s.*, NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
            AS upc_normalized
        FROM snap s LEFT JOIN upc u USING (item_id)
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
    ])
    rows = [dict(r) for r in client.query(query, job_config=job_config).result()]
    if not rows:
        raise ValueError(f"Item {item_id} not found in the master snapshot")
    src = rows[0]

    merge_query = f"""
        MERGE `{repository.T_TRACKED}` T
        USING (SELECT @item_id AS item_id) S
        ON T.item_id = S.item_id
        WHEN MATCHED THEN UPDATE SET
            T.pinned = TRUE, T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            updated_at
        ) VALUES (
            @item_id, @sku, @system_sku, @upc_normalized, @brand, @title,
            'manual', TRUE, FALSE, NULL, @rev, @retail, @cost, NULL, @is_map,
            NULL, CURRENT_TIMESTAMP()
        )
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("sku", "STRING", src.get("sku")),
        bigquery.ScalarQueryParameter("system_sku", "STRING", src.get("system_sku")),
        bigquery.ScalarQueryParameter("upc_normalized", "STRING", src.get("upc_normalized")),
        bigquery.ScalarQueryParameter("brand", "STRING", src.get("brand")),
        bigquery.ScalarQueryParameter("title", "STRING", src.get("title")),
        bigquery.ScalarQueryParameter("rev", "FLOAT64", float(src.get("trailing_revenue_90d") or 0)),
        bigquery.ScalarQueryParameter("retail", "FLOAT64", src.get("current_retail")),
        bigquery.ScalarQueryParameter("cost", "FLOAT64", src.get("current_cost")),
        bigquery.ScalarQueryParameter("is_map", "BOOL", bool(src.get("is_map"))),
    ])
    client.query(merge_query, job_config=job_config).result()
    repository.invalidate_pi_caches()
    return src
