"""Seeds pi_tracked_products (feature b).

Two modes (PI_SEED_MODE): 'track_tag' (default) tracks exactly the items tagged
with the Lightspeed `track` item tag; 'top_revenue' is the legacy top-N-by-90d-
revenue behavior. Source is the latest master-snapshot date (the view is a daily
per-item+shop series — aggregating without the date filter inflates revenue by
the number of snapshot dates) joined to item_history for UPC/EAN + matrix, which
the snapshot lacks.

The MERGE refreshes descriptive/market fields but never touches the manual
controls (pinned / excluded / min_price_override / map_price). Items that fall
out of the seed set are ARCHIVED, never deleted — their observations, links, and
events stay put, and removing/re-adding the tag toggles tracking with history
intact (a reactivation restarts the discovery window via activated_at). is_map
comes from the Lightspeed `map` item tag (comma-separated in item_tags).
"""
from google.cloud import bigquery

from app.services.bigquery_sync import get_bq_client, LS_DATASET
from . import config, repository


def refresh_tracked_products(top_n: int = None) -> int:
    if config.SEED_MODE == "track_tag":
        count = refresh_from_track_tag()
    else:
        count = refresh_from_top_revenue(top_n)
    # Self-sync subscribed matrices AFTER the primary seed (so its source-scoped
    # archival never fights the tag/top-revenue archival) and BEFORE the
    # descriptive refresh (which then backfills any newly-inserted variant rows).
    expand_tracked_matrices()
    refresh_descriptive_fields()
    # Rebuild the full-catalog search index (backs the pin picker). Wholesale
    # CREATE OR REPLACE, so it just refreshes to current catalog state.
    try:
        repository.refresh_item_search_index()
    except Exception as e:
        print(f"pi: item search index rebuild failed (search falls back to live): {e}")
    return count


def refresh_descriptive_fields() -> int:
    """Refreshes descriptive/matrix/price fields for EVERY active tracked row,
    whatever its source. The tag/revenue seeds only touch rows in their own seed
    set, so manually added rows (search-picker pins, URL-override creations)
    would otherwise keep whatever fields existed at creation time — including
    NULL attributes on rows that predate the matrix columns. Never touches
    manual controls, rank, archived, or activated_at."""
    repository.ensure_pi_tables()
    client = get_bq_client()
    merge_query = f"""
        MERGE `{repository.T_TRACKED}` T
        USING (
            WITH latest AS (
                SELECT MAX(snapshot_date_local) AS d
                FROM `{LS_DATASET}.v_master_snapshot_latest`
            ),
            item_hist AS (
                SELECT
                    CAST(id AS STRING) AS item_id,
                    COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc,
                    CAST(NULLIF(item_matrix_id, 0) AS STRING) AS item_matrix_id,
                    attribute_1, attribute_2, attribute_3
                FROM `{LS_DATASET}.item_history`
                QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
            ),
            matrix AS (
                SELECT CAST(id AS STRING) AS matrix_id, description AS matrix_description
                FROM `{LS_DATASET}.item_matrix_history`
                QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
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
                    LOGICAL_OR(REGEXP_CONTAINS(
                        LOWER(COALESCE(item_tags, '')), r'(^|,)\\s*map\\s*(,|$)'
                    )) AS is_map
                FROM `{LS_DATASET}.v_master_snapshot_latest` s
                CROSS JOIN latest
                WHERE s.snapshot_date_local = latest.d
                  AND CAST(item_id AS STRING) IN (
                      SELECT item_id FROM `{repository.T_TRACKED}`
                      WHERE COALESCE(archived, FALSE) = FALSE
                  )
                GROUP BY item_id
            )
            SELECT
                s.*, u.item_matrix_id, m.matrix_description,
                u.attribute_1, u.attribute_2, u.attribute_3,
                NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
                    AS upc_normalized
            FROM snap s
            LEFT JOIN item_hist u USING (item_id)
            LEFT JOIN matrix m ON m.matrix_id = u.item_matrix_id
        ) S
        ON T.item_id = S.item_id
        WHEN MATCHED THEN UPDATE SET
            T.title = S.title,
            T.brand = S.brand,
            T.sku = S.sku,
            T.system_sku = S.system_sku,
            T.upc_normalized = S.upc_normalized,
            T.current_retail = S.current_retail,
            T.current_cost = S.current_cost,
            T.is_map = S.is_map,
            T.item_matrix_id = S.item_matrix_id,
            T.matrix_description = S.matrix_description,
            T.attribute_1 = S.attribute_1,
            T.attribute_2 = S.attribute_2,
            T.attribute_3 = S.attribute_3,
            T.updated_at = CURRENT_TIMESTAMP()
    """
    result = client.query(merge_query).result()
    repository.invalidate_pi_caches()
    return getattr(result, "num_dml_affected_rows", 0) or 0


def refresh_from_track_tag() -> int:
    """Seeds from items carrying the `track` tag. Returns the number of tagged
    items, or 0 (no-op) when none are found — an empty tag set is treated as a
    sync glitch rather than an instruction to archive everything."""
    repository.ensure_pi_tables()
    client = get_bq_client()
    tag_filter = (
        "REGEXP_CONTAINS(LOWER(COALESCE(item_tags, '')), "
        "CONCAT(r'(^|,)\\s*', @tag, r'\\s*(,|$)'))"
    )
    params = [bigquery.ScalarQueryParameter("tag", "STRING", config.TRACK_TAG)]

    count_rows = [dict(r) for r in client.query(f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        )
        SELECT COUNT(DISTINCT item_id) AS n
        FROM `{LS_DATASET}.v_master_snapshot_latest` s CROSS JOIN latest
        WHERE s.snapshot_date_local = latest.d AND {tag_filter}
    """, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()]
    tagged = count_rows[0]["n"] if count_rows else 0
    if not tagged:
        print(f"pi: no items carry the '{config.TRACK_TAG}' tag — keeping the "
              "current tracked list unchanged")
        return 0

    seed_query = f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        item_hist AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc,
                CAST(NULLIF(item_matrix_id, 0) AS STRING) AS item_matrix_id,
                attribute_1, attribute_2, attribute_3
            FROM `{LS_DATASET}.item_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        matrix AS (
            SELECT CAST(id AS STRING) AS matrix_id, description AS matrix_description
            FROM `{LS_DATASET}.item_matrix_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
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
              AND {tag_filter}
            GROUP BY item_id
        )
        SELECT
            s.item_id, s.title, s.brand, s.sku, s.system_sku,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized,
            s.current_retail, s.current_cost, s.trailing_revenue_90d, s.is_map,
            u.item_matrix_id, m.matrix_description,
            u.attribute_1, u.attribute_2, u.attribute_3,
            ROW_NUMBER() OVER (ORDER BY s.trailing_revenue_90d DESC) AS revenue_rank
        FROM snap s
        LEFT JOIN item_hist u USING (item_id)
        LEFT JOIN matrix m ON m.matrix_id = u.item_matrix_id
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
            T.item_matrix_id = S.item_matrix_id,
            T.matrix_description = S.matrix_description,
            T.attribute_1 = S.attribute_1,
            T.attribute_2 = S.attribute_2,
            T.attribute_3 = S.attribute_3,
            T.source = 'track_tag',
            -- reactivation restarts the discovery window
            T.activated_at = IF(COALESCE(T.archived, FALSE),
                                CURRENT_TIMESTAMP(),
                                COALESCE(T.activated_at, CURRENT_TIMESTAMP())),
            T.archived = FALSE,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            item_matrix_id, matrix_description, attribute_1, attribute_2,
            attribute_3, archived, activated_at, updated_at
        ) VALUES (
            S.item_id, S.sku, S.system_sku, S.upc_normalized, S.brand, S.title,
            'track_tag', FALSE, FALSE, S.revenue_rank,
            S.trailing_revenue_90d, S.current_retail, S.current_cost,
            NULL, S.is_map, NULL, S.item_matrix_id, S.matrix_description,
            S.attribute_1, S.attribute_2, S.attribute_3, FALSE,
            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
        )
        WHEN NOT MATCHED BY SOURCE
            -- The tag is the source of truth for every unpinned row, whatever
            -- created it: manual adds must be tagged (or pinned) to stay
            -- tracked, or they'd sneak untagged items into the dashboard.
            AND COALESCE(T.pinned, FALSE) = FALSE
            AND COALESCE(T.archived, FALSE) = FALSE
        THEN UPDATE SET T.archived = TRUE, T.updated_at = CURRENT_TIMESTAMP()
    """
    client.query(
        merge_query, job_config=bigquery.QueryJobConfig(query_parameters=params)
    ).result()
    repository.invalidate_pi_caches()
    return tagged


def refresh_from_top_revenue(top_n: int = None) -> int:
    """Legacy mode: seeds from the top-N 90d-revenue items. Returns the
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
        item_hist AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc,
                CAST(NULLIF(item_matrix_id, 0) AS STRING) AS item_matrix_id,
                attribute_1, attribute_2, attribute_3
            FROM `{LS_DATASET}.item_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        matrix AS (
            SELECT CAST(id AS STRING) AS matrix_id, description AS matrix_description
            FROM `{LS_DATASET}.item_matrix_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        )
        SELECT
            s.item_id, s.title, s.brand, s.sku, s.system_sku,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized,
            s.current_retail, s.current_cost, s.trailing_revenue_90d, s.is_map,
            u.item_matrix_id, m.matrix_description,
            u.attribute_1, u.attribute_2, u.attribute_3,
            ROW_NUMBER() OVER (ORDER BY s.trailing_revenue_90d DESC) AS revenue_rank
        FROM snap s
        LEFT JOIN item_hist u USING (item_id)
        LEFT JOIN matrix m ON m.matrix_id = u.item_matrix_id
        WHERE s.trailing_revenue_90d > 0
          {"AND u.raw_upc IS NOT NULL" if config.REQUIRE_UPC else ""}
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
            T.item_matrix_id = S.item_matrix_id,
            T.matrix_description = S.matrix_description,
            T.attribute_1 = S.attribute_1,
            T.attribute_2 = S.attribute_2,
            T.attribute_3 = S.attribute_3,
            T.activated_at = IF(COALESCE(T.archived, FALSE),
                                CURRENT_TIMESTAMP(),
                                COALESCE(T.activated_at, CURRENT_TIMESTAMP())),
            T.archived = FALSE,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            item_matrix_id, matrix_description, attribute_1, attribute_2,
            attribute_3, archived, activated_at, updated_at
        ) VALUES (
            S.item_id, S.sku, S.system_sku, S.upc_normalized, S.brand, S.title,
            'auto_top_revenue', FALSE, FALSE, S.revenue_rank,
            S.trailing_revenue_90d, S.current_retail, S.current_cost,
            NULL, S.is_map, NULL, S.item_matrix_id, S.matrix_description,
            S.attribute_1, S.attribute_2, S.attribute_3, FALSE,
            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
        )
        WHEN NOT MATCHED BY SOURCE
            AND T.source = 'auto_top_revenue'
            AND COALESCE(T.pinned, FALSE) = FALSE
            AND COALESCE(T.excluded, FALSE) = FALSE
            AND COALESCE(T.archived, FALSE) = FALSE
        THEN UPDATE SET T.archived = TRUE, T.updated_at = CURRENT_TIMESTAMP()
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("top_n", "INT64", int(top_n)),
    ])
    result = client.query(merge_query, job_config=job_config).result()
    repository.invalidate_pi_caches()
    return getattr(result, "num_dml_affected_rows", 0) or 0


def _snapshot_source_query(where_source: str) -> str:
    """The canonical snapshot ⟕ item_history ⟕ item_matrix_history expansion used by
    the manual pins and the matrix self-sync. Yields one row per snapshot variant
    (with matrix/attributes/upc) matching `where_source` (references `s.*`, or the
    `u.` item_history alias for matrix/attribute filters)."""
    return f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d
            FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        item_hist AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc,
                CAST(NULLIF(item_matrix_id, 0) AS STRING) AS item_matrix_id,
                attribute_1, attribute_2, attribute_3
            FROM `{LS_DATASET}.item_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
        ),
        matrix AS (
            SELECT CAST(id AS STRING) AS matrix_id, description AS matrix_description
            FROM `{LS_DATASET}.item_matrix_history`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_time DESC) = 1
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
        )
        SELECT
            s.*, u.item_matrix_id, m.matrix_description,
            u.attribute_1, u.attribute_2, u.attribute_3,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(u.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized
        FROM snap s
        LEFT JOIN item_hist u USING (item_id)
        LEFT JOIN matrix m ON m.matrix_id = u.item_matrix_id
        WHERE {where_source}
    """


def _manual_pin_merge(where_source: str, params: list) -> int:
    """Shared MERGE for manual pins: seeds full rows (incl. matrix/attributes)
    for every snapshot item matching the source filter; existing rows are just
    pinned. Returns the number of source rows."""
    repository.ensure_pi_tables()
    client = get_bq_client()
    source_query = _snapshot_source_query(where_source)
    merge_query = f"""
        MERGE `{repository.T_TRACKED}` T
        USING ({source_query}) S
        ON T.item_id = S.item_id
        WHEN MATCHED THEN UPDATE SET
            T.pinned = TRUE,
            T.item_matrix_id = S.item_matrix_id,
            T.matrix_description = S.matrix_description,
            T.attribute_1 = S.attribute_1,
            T.attribute_2 = S.attribute_2,
            T.attribute_3 = S.attribute_3,
            T.activated_at = IF(COALESCE(T.archived, FALSE),
                                CURRENT_TIMESTAMP(),
                                COALESCE(T.activated_at, CURRENT_TIMESTAMP())),
            T.archived = FALSE,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            item_matrix_id, matrix_description, attribute_1, attribute_2,
            attribute_3, archived, activated_at, updated_at
        ) VALUES (
            S.item_id, S.sku, S.system_sku, S.upc_normalized, S.brand, S.title,
            'manual', TRUE, FALSE, NULL, S.trailing_revenue_90d,
            S.current_retail, S.current_cost, NULL, S.is_map, NULL,
            S.item_matrix_id, S.matrix_description, S.attribute_1,
            S.attribute_2, S.attribute_3, FALSE, CURRENT_TIMESTAMP(),
            CURRENT_TIMESTAMP()
        )
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    result = client.query(merge_query, job_config=job_config).result()
    repository.invalidate_pi_caches()
    return getattr(result, "num_dml_affected_rows", 0) or 0


def add_manual_tracked_product(item_id: str) -> dict:
    """Pins a single item (from item search) into pi_tracked_products."""
    affected = _manual_pin_merge(
        "s.item_id = @item_id",
        [bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id))],
    )
    if not affected:
        raise ValueError(f"Item {item_id} not found in the master snapshot")
    return {"item_id": str(item_id)}


def add_manual_tracked_products_for_matrix(item_matrix_id: str) -> int:
    """Pins every non-archived variant of a matrix ("pin all variants")."""
    affected = _manual_pin_merge(
        "u.item_matrix_id = @matrix_id",
        [bigquery.ScalarQueryParameter("matrix_id", "STRING", str(item_matrix_id))],
    )
    if not affected:
        raise ValueError(f"No snapshot items found for matrix {item_matrix_id}")
    return affected


def expand_tracked_matrices() -> int:
    """Self-syncs the actively-subscribed matrices (pi_tracked_matrices) into
    pi_tracked_products. Runs every refresh AFTER the primary seed: variants newly
    added to a subscribed matrix are INSERTed (source='matrix_sub'); variants removed
    from a still-subscribed matrix are archived.

    Ownership is partitioned by `source`: this MERGE only ever archives its own
    'matrix_sub' rows, so it never fights the tag / top-revenue seed (which archive
    only their own sources). A row that is also tag- or manually-tracked keeps that
    source and is governed by that signal; the MATCHED branch here only refreshes
    matrix/attribute fields and un-archives, so a subscribed variant stays live.
    No-op when nothing is subscribed. Returns rows affected."""
    matrix_ids = repository.get_active_matrix_ids()
    if not matrix_ids:
        return 0
    repository.ensure_pi_tables()
    client = get_bq_client()
    params = [bigquery.ArrayQueryParameter("matrix_ids", "STRING", matrix_ids)]
    source_query = _snapshot_source_query("u.item_matrix_id IN UNNEST(@matrix_ids)")
    merge_query = f"""
        MERGE `{repository.T_TRACKED}` T
        USING ({source_query}) S
        ON T.item_id = S.item_id
        WHEN MATCHED THEN UPDATE SET
            T.item_matrix_id = S.item_matrix_id,
            T.matrix_description = S.matrix_description,
            T.attribute_1 = S.attribute_1,
            T.attribute_2 = S.attribute_2,
            T.attribute_3 = S.attribute_3,
            T.activated_at = IF(COALESCE(T.archived, FALSE),
                                CURRENT_TIMESTAMP(),
                                COALESCE(T.activated_at, CURRENT_TIMESTAMP())),
            T.archived = FALSE,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            item_id, sku, system_sku, upc_normalized, brand, title, source,
            pinned, excluded, revenue_rank, trailing_revenue_90d,
            current_retail, current_cost, min_price_override, is_map, map_price,
            item_matrix_id, matrix_description, attribute_1, attribute_2,
            attribute_3, archived, activated_at, updated_at
        ) VALUES (
            S.item_id, S.sku, S.system_sku, S.upc_normalized, S.brand, S.title,
            'matrix_sub', FALSE, FALSE, NULL, S.trailing_revenue_90d,
            S.current_retail, S.current_cost, NULL, S.is_map, NULL,
            S.item_matrix_id, S.matrix_description, S.attribute_1,
            S.attribute_2, S.attribute_3, FALSE, CURRENT_TIMESTAMP(),
            CURRENT_TIMESTAMP()
        )
        -- Variant removed from a still-subscribed matrix: archive it, but only if
        -- WE own it (source='matrix_sub', unpinned). Scoped to subscribed matrices
        -- so standalone items and other matrices are never touched.
        WHEN NOT MATCHED BY SOURCE
            AND COALESCE(T.source, '') = 'matrix_sub'
            AND COALESCE(T.pinned, FALSE) = FALSE
            AND COALESCE(T.archived, FALSE) = FALSE
            AND T.item_matrix_id IN UNNEST(@matrix_ids)
        THEN UPDATE SET T.archived = TRUE, T.updated_at = CURRENT_TIMESTAMP()
    """
    result = client.query(
        merge_query, job_config=bigquery.QueryJobConfig(query_parameters=params)
    ).result()
    repository.invalidate_pi_caches()
    return getattr(result, "num_dml_affected_rows", 0) or 0
