"""BigQuery persistence for the Price Intelligence feature.

All tables are additive (`pi_` prefix) inside APP_DATASET. Observation/event/run
rows go through batch load jobs, never streaming inserts, so later DML (ack
UPDATEs, retention deletes) is never blocked by a streaming buffer. The only
streaming write is the append-only price-push audit log, mirroring log_writeback.
"""
import threading
import time
import uuid
from datetime import date, datetime, timezone

from google.cloud import bigquery

from app.services.bigquery_sync import get_bq_client, APP_DATASET, LS_DATASET

T_COMPETITORS = f"{APP_DATASET}.pi_competitors"
T_TRACKED = f"{APP_DATASET}.pi_tracked_products"
T_URLS = f"{APP_DATASET}.pi_tracked_urls"
T_OBSERVATIONS = f"{APP_DATASET}.pi_price_observations"
T_EVENTS = f"{APP_DATASET}.pi_change_events"
T_DIGESTS = f"{APP_DATASET}.pi_digests"
T_PUSH_LOG = f"{APP_DATASET}.pi_price_push_log"
T_RUNS = f"{APP_DATASET}.pi_scrape_runs"
T_LINKS = f"{APP_DATASET}.pi_product_links"
T_OUR_PRICE_HISTORY = f"{APP_DATASET}.pi_our_price_history"

_tables_ensured = False
_ensure_lock = threading.Lock()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_pi_tables():
    """Creates all price-intel tables if absent. Runs once per process."""
    global _tables_ensured
    if _tables_ensured:
        return
    with _ensure_lock:
        if _tables_ensured:
            return
        client = get_bq_client()
        statements = [
            f"""CREATE TABLE IF NOT EXISTS `{T_COMPETITORS}` (
                competitor_id STRING NOT NULL,
                name STRING NOT NULL,
                base_url STRING NOT NULL,
                connector_type STRING,
                enabled BOOL,
                notes STRING,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                last_scraped_at TIMESTAMP,
                last_scrape_status STRING
            )""",
            f"""CREATE TABLE IF NOT EXISTS `{T_TRACKED}` (
                item_id STRING NOT NULL,
                sku STRING,
                system_sku STRING,
                upc_normalized STRING,
                brand STRING,
                title STRING,
                source STRING,
                pinned BOOL,
                excluded BOOL,
                revenue_rank INT64,
                trailing_revenue_90d FLOAT64,
                current_retail FLOAT64,
                current_cost FLOAT64,
                min_price_override FLOAT64,
                is_map BOOL,
                map_price FLOAT64,
                updated_at TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS `{T_URLS}` (
                url_id STRING NOT NULL,
                url STRING NOT NULL,
                competitor_id STRING,
                item_id STRING,
                label STRING,
                enabled BOOL,
                created_by STRING,
                created_at TIMESTAMP,
                last_scraped_at TIMESTAMP,
                last_status STRING
            )""",
            f"""CREATE TABLE IF NOT EXISTS `{T_OBSERVATIONS}` (
                observed_at TIMESTAMP NOT NULL,
                run_id STRING NOT NULL,
                source STRING,
                diff_key STRING,
                competitor_id STRING,
                url STRING,
                competitor_title STRING,
                competitor_sku STRING,
                gtin STRING,
                match_item_id STRING,
                match_method STRING,
                match_confidence FLOAT64,
                price FLOAT64,
                compare_at_price FLOAT64,
                currency STRING,
                in_stock BOOL
            )
            PARTITION BY DATE(observed_at)
            CLUSTER BY competitor_id, match_item_id""",
            f"""CREATE TABLE IF NOT EXISTS `{T_EVENTS}` (
                event_id STRING NOT NULL,
                occurred_at TIMESTAMP NOT NULL,
                run_id STRING,
                event_type STRING,
                competitor_id STRING,
                competitor_name STRING,
                item_id STRING,
                item_title STRING,
                url STRING,
                old_price FLOAT64,
                new_price FLOAT64,
                pct_change FLOAT64,
                acknowledged BOOL,
                acknowledged_at TIMESTAMP,
                notified BOOL,
                notified_at TIMESTAMP
            )
            PARTITION BY DATE(occurred_at)""",
            f"""CREATE TABLE IF NOT EXISTS `{T_DIGESTS}` (
                digest_id STRING,
                created_at TIMESTAMP,
                run_id STRING,
                model STRING,
                input_tokens INT64,
                output_tokens INT64,
                digest_md STRING,
                stats_json STRING
            )""",
            f"""CREATE TABLE IF NOT EXISTS `{T_PUSH_LOG}` (
                pushed_at TIMESTAMP,
                item_id STRING,
                sku STRING,
                old_price FLOAT64,
                new_price FLOAT64,
                floor_price FLOAT64,
                guard_result STRING,
                actor STRING,
                status STRING,
                error STRING,
                run_context STRING
            )""",
            f"""CREATE TABLE IF NOT EXISTS `{T_RUNS}` (
                run_id STRING,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                trigger STRING,
                status STRING,
                competitors_done INT64,
                urls_done INT64,
                observations_count INT64,
                changes_count INT64,
                error STRING
            )""",
            # Persistent competitor-listing <-> item links: the self-building
            # "pre-matched URL catalog". match_key identifies one scraped
            # listing (see matcher.build_match_key); one row per match_key.
            # A 'rejected' row is a tombstone — its key is never re-proposed.
            f"""CREATE TABLE IF NOT EXISTS `{T_LINKS}` (
                link_id STRING NOT NULL,
                item_id STRING,
                competitor_id STRING,
                match_key STRING NOT NULL,
                competitor_url STRING,
                competitor_sku STRING,
                competitor_title STRING,
                gtin STRING,
                level STRING,
                status STRING,
                source STRING,
                confidence FLOAT64,
                fuzzy_score FLOAT64,
                llm_verdict STRING,
                llm_reason STRING,
                our_price FLOAT64,
                their_price FLOAT64,
                decided_by STRING,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )""",
            # Forward-only history of OUR retail price, one row per item only when
            # the price changes (change-points). Powers the price-history chart's
            # "our price" line. Competitor history lives in pi_price_observations.
            f"""CREATE TABLE IF NOT EXISTS `{T_OUR_PRICE_HISTORY}` (
                item_id STRING NOT NULL,
                observed_at TIMESTAMP NOT NULL,
                price FLOAT64
            )
            PARTITION BY DATE(observed_at)
            CLUSTER BY item_id""",
        ]
        for stmt in statements:
            client.query(stmt).result()
        _ensure_columns(client, T_TRACKED, {
            "item_matrix_id": "STRING",
            "matrix_description": "STRING",
            "attribute_1": "STRING",
            "attribute_2": "STRING",
            "attribute_3": "STRING",
            # archived = no longer tag-tracked; row (and all its observations,
            # links, events) is retained and reactivates if the tag returns.
            "archived": "BOOL",
            "activated_at": "TIMESTAMP",
        })
        _ensure_columns(client, T_EVENTS, {"item_brand": "STRING"})
        _tables_ensured = True


def _ensure_columns(client, table_id: str, columns: dict):
    """Additive schema migration: ADD COLUMN IF NOT EXISTS per column (metadata-only
    for nullable columns). Drops the cached schema so load_rows sees new columns."""
    for name, col_type in columns.items():
        client.query(
            f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS {name} {col_type}"
        ).result()
    _schema_cache.pop(table_id, None)


# ---------------------------------------------------------------------------
# Small TTL caches (same shape as the bigquery_sync admin caches).
# ---------------------------------------------------------------------------
_caches = {}
_CACHE_TTL_SECONDS = 300


def _cache_get(key):
    entry = _caches.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _cache_set(key, value):
    _caches[key] = (time.time(), value)


def invalidate_pi_caches():
    _caches.clear()


def _rows(query: str, params=None):
    job_config = bigquery.QueryJobConfig(query_parameters=params or [])
    return [dict(r) for r in get_bq_client().query(query, job_config=job_config).result()]


_schema_cache = {}


def _table_schema(table_id: str):
    if table_id not in _schema_cache:
        _schema_cache[table_id] = get_bq_client().get_table(table_id).schema
    return _schema_cache[table_id]


def _json_ready(rows: list):
    """load_table_from_json can't serialize datetime/date — rows built from BQ
    query results (e.g. a competitor row fed back into upsert_competitor) carry
    them, so coerce to ISO strings in place before any load job."""
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (datetime, date)):
                row[key] = value.isoformat()


def load_rows(table_id: str, rows: list):
    """Batch-appends rows via a load job (immediately DML-safe, no streaming buffer).

    Passes the table's real schema instead of letting load_table_from_json
    autodetect: a batch whose competitor_sku / match_item_id values are all
    numeric would otherwise be typed INTEGER and rejected against the STRING
    columns. STRING-typed values are also coerced so a JSON-LD sku/gtin that
    arrives as a number still loads cleanly.
    """
    if not rows:
        return
    ensure_pi_tables()
    _json_ready(rows)
    schema = _table_schema(table_id)
    string_fields = {f.name for f in schema if f.field_type == "STRING"}
    for row in rows:
        for key in string_fields:
            value = row.get(key)
            if value is not None and not isinstance(value, str):
                row[key] = str(value)
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", schema=schema)
    get_bq_client().load_table_from_json(rows, table_id, job_config=job_config).result()


def _merge_upsert(table_id: str, rows: list, key: str, update_cols: list, insert_cols: list):
    """Generic temp-table MERGE (mirrors upsert_managed_skus). The temp load uses
    the target table's schema — autodetect would type ISO timestamp strings as
    STRING and the MERGE insert would then fail against TIMESTAMP columns."""
    ensure_pi_tables()
    _json_ready(rows)
    client = get_bq_client()
    temp_table_id = f"{table_id}_temp"
    target_schema = client.get_table(table_id).schema
    row_keys = set(rows[0].keys())
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=[f for f in target_schema if f.name in row_keys],
    )
    client.load_table_from_json(rows, temp_table_id, job_config=job_config).result()
    set_clause = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
    cols = ", ".join(insert_cols)
    vals = ", ".join(f"S.{c}" for c in insert_cols)
    client.query(f"""
        MERGE `{table_id}` T
        USING `{temp_table_id}` S
        ON T.{key} = S.{key}
        WHEN MATCHED THEN UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})
    """).result()


# ---------------------------------------------------------------------------
# Competitors (feature f)
# ---------------------------------------------------------------------------

def get_competitors(include_disabled: bool = True):
    ensure_pi_tables()
    cached = _cache_get("competitors") if include_disabled else None
    if cached is not None:
        return cached
    rows = _rows(f"SELECT * FROM `{T_COMPETITORS}` ORDER BY name")
    if not include_disabled:
        rows = [r for r in rows if r.get("enabled")]
    else:
        _cache_set("competitors", rows)
    return rows


def upsert_competitor(data: dict) -> dict:
    now = utcnow_iso()
    row = {
        "competitor_id": data.get("competitor_id") or str(uuid.uuid4()),
        "name": data["name"],
        "base_url": data["base_url"].rstrip("/"),
        "connector_type": data.get("connector_type"),
        "enabled": bool(data.get("enabled", True)),
        "notes": data.get("notes"),
        "created_at": data.get("created_at") or now,
        "updated_at": now,
        "last_scraped_at": data.get("last_scraped_at"),
        "last_scrape_status": data.get("last_scrape_status"),
    }
    _merge_upsert(
        T_COMPETITORS, [row], "competitor_id",
        update_cols=["name", "base_url", "connector_type", "enabled", "notes", "updated_at"],
        insert_cols=list(row.keys()),
    )
    invalidate_pi_caches()
    return row


def set_competitor_enabled(competitor_id: str, enabled: bool):
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_COMPETITORS}` SET enabled = @enabled, updated_at = CURRENT_TIMESTAMP() "
        "WHERE competitor_id = @cid",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("enabled", "BOOL", enabled),
            bigquery.ScalarQueryParameter("cid", "STRING", competitor_id),
        ]),
    ).result()
    invalidate_pi_caches()


def mark_competitor_scraped(competitor_id: str, status: str):
    try:
        get_bq_client().query(
            f"UPDATE `{T_COMPETITORS}` SET last_scraped_at = CURRENT_TIMESTAMP(), "
            "last_scrape_status = @status WHERE competitor_id = @cid",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("status", "STRING", status),
                bigquery.ScalarQueryParameter("cid", "STRING", competitor_id),
            ]),
        ).result()
    except Exception as e:
        print(f"pi: failed to mark competitor scraped: {e}")


# ---------------------------------------------------------------------------
# Tracked URLs (feature a)
# ---------------------------------------------------------------------------

def get_tracked_urls(include_disabled: bool = True):
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT u.*, t.title AS item_title, t.brand AS item_brand,
               t.upc_normalized AS item_upc, t.system_sku AS item_system_sku
        FROM `{T_URLS}` u
        LEFT JOIN `{T_TRACKED}` t ON t.item_id = u.item_id
        ORDER BY u.created_at DESC
    """)
    if not include_disabled:
        rows = [r for r in rows if r.get("enabled")]
    return rows


def update_tracked_url(url_id: str, fields: dict):
    """Updates the mutable fields of a tracked URL (item_id / label / competitor_id)."""
    ensure_pi_tables()
    allowed = {"item_id", "label", "competitor_id"}
    sets, params = [], [bigquery.ScalarQueryParameter("uid", "STRING", str(url_id))]
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k} = @{k}")
        params.append(bigquery.ScalarQueryParameter(k, "STRING", None if v is None else str(v)))
    if not sets:
        return
    get_bq_client().query(
        f"UPDATE `{T_URLS}` SET {', '.join(sets)} WHERE url_id = @uid",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    invalidate_pi_caches()


def upsert_tracked_url(data: dict) -> dict:
    now = utcnow_iso()
    row = {
        "url_id": data.get("url_id") or str(uuid.uuid4()),
        "url": data["url"],
        "competitor_id": data.get("competitor_id"),
        "item_id": str(data["item_id"]) if data.get("item_id") else None,
        "label": data.get("label"),
        "enabled": bool(data.get("enabled", True)),
        "created_by": data.get("created_by", "Dashboard"),
        "created_at": data.get("created_at") or now,
        "last_scraped_at": None,
        "last_status": None,
    }
    _merge_upsert(
        T_URLS, [row], "url_id",
        update_cols=["url", "competitor_id", "item_id", "label", "enabled"],
        insert_cols=list(row.keys()),
    )
    invalidate_pi_caches()
    return row


def set_tracked_url_enabled(url_id: str, enabled: bool):
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_URLS}` SET enabled = @enabled WHERE url_id = @uid",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("enabled", "BOOL", enabled),
            bigquery.ScalarQueryParameter("uid", "STRING", url_id),
        ]),
    ).result()
    invalidate_pi_caches()


def mark_url_scraped(url_id: str, status: str):
    try:
        get_bq_client().query(
            f"UPDATE `{T_URLS}` SET last_scraped_at = CURRENT_TIMESTAMP(), last_status = @status "
            "WHERE url_id = @uid",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("status", "STRING", status),
                bigquery.ScalarQueryParameter("uid", "STRING", url_id),
            ]),
        ).result()
    except Exception as e:
        print(f"pi: failed to mark url scraped: {e}")


# ---------------------------------------------------------------------------
# Tracked products (feature b)
# ---------------------------------------------------------------------------

def get_tracked_products(include_excluded: bool = True, include_archived: bool = False):
    ensure_pi_tables()
    rows = _rows(f"SELECT * FROM `{T_TRACKED}` ORDER BY revenue_rank")
    if not include_archived:
        rows = [r for r in rows if not r.get("archived")]
    if not include_excluded:
        rows = [r for r in rows if not r.get("excluded")]
    return rows


def get_tracked_products_with_market(days: int = 7):
    """Tracked products joined with market min/median from the latest observation
    per (competitor, product) over the trailing window.

    Market data aggregates at the model/matrix grain when the item belongs to a
    matrix (sizes share MSRP, so a 56cm listing prices every tracked size), else
    per item. competitor_count COALESCEs NULL competitor_id (tracked-URL rows)
    to the URL so URL-only tracking still counts as a store."""
    ensure_pi_tables()
    cache_key = f"tracked_market_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    rows = _rows(f"""
        WITH latest AS (
            SELECT o.*,
                   -- digit signature of the listing's size (last '-' segment only,
                   -- so the model number e.g. '5000' doesn't pollute the size match)
                   (SELECT STRING_AGG(d, ',' ORDER BY d) FROM UNNEST(REGEXP_EXTRACT_ALL(
                       LOWER(ARRAY_REVERSE(SPLIT(COALESCE(o.competitor_title, ''), ' - '))[SAFE_OFFSET(0)]),
                       r'[0-9]+')) d) AS comp_size_sig
            FROM `{T_OBSERVATIONS}` o
            WHERE observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND match_item_id IS NOT NULL AND price IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
        ),
        grouped AS (
            SELECT l.*, COALESCE(t.item_matrix_id, t.item_id) AS group_key
            FROM latest l
            JOIN `{T_TRACKED}` t ON t.item_id = l.match_item_id
        ),
        -- one representative per (matrix, store): freshest in-stock, then cheapest,
        -- so a stale listing can't undercut the current price (mirrors the breakdown).
        store_rep AS (
            SELECT * FROM grouped
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY group_key, COALESCE(competitor_id, CONCAT('url:', url))
                ORDER BY IF(COALESCE(in_stock, FALSE), 1, 0) DESC, observed_at DESC, price ASC
            ) = 1
        ),
        -- fallback: pool the whole matrix (sizes share MSRP for bikes)
        matrix_market AS (
            SELECT
                group_key,
                MIN(IF(in_stock, price, NULL)) AS market_min_in_stock,
                MIN(price) AS market_min,
                APPROX_QUANTILES(price, 2)[SAFE_OFFSET(1)] AS market_median,
                COUNT(DISTINCT COALESCE(competitor_id, CONCAT('url:', url))) AS competitor_count,
                ARRAY_AGG(DISTINCT competitor_id IGNORE NULLS) AS competitor_ids,
                MAX(observed_at) AS last_observed_at
            FROM store_rep
            GROUP BY group_key
        ),
        tracked_active AS (
            SELECT *, COALESCE(item_matrix_id, item_id) AS group_key,
                   (SELECT STRING_AGG(d, ',' ORDER BY d) FROM UNNEST(REGEXP_EXTRACT_ALL(
                       LOWER(CONCAT(COALESCE(attribute_1,''),' ',COALESCE(attribute_2,''),' ',
                       COALESCE(attribute_3,''))), r'[0-9]+')) d) AS item_size_sig
            FROM `{T_TRACKED}` WHERE COALESCE(archived, FALSE) = FALSE
        ),
        -- preferred: competitor listings whose size signature matches this item's,
        -- so market data stays size-accurate for matrices priced per size (tires);
        -- reduced to the freshest representative per (item, store).
        item_rep AS (
            SELECT t.item_id, g.competitor_id, g.url, g.in_stock, g.price, g.observed_at
            FROM tracked_active t
            JOIN grouped g
              ON g.group_key = t.group_key AND g.comp_size_sig = t.item_size_sig
            WHERE t.item_size_sig IS NOT NULL AND t.item_size_sig != ''
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY t.item_id, COALESCE(g.competitor_id, CONCAT('url:', g.url))
                ORDER BY IF(COALESCE(g.in_stock, FALSE), 1, 0) DESC, g.observed_at DESC, g.price ASC
            ) = 1
        ),
        item_market AS (
            SELECT
                item_id,
                MIN(IF(in_stock, price, NULL)) AS market_min_in_stock,
                MIN(price) AS market_min,
                APPROX_QUANTILES(price, 2)[SAFE_OFFSET(1)] AS market_median,
                COUNT(DISTINCT COALESCE(competitor_id, CONCAT('url:', url))) AS competitor_count,
                ARRAY_AGG(DISTINCT competitor_id IGNORE NULLS) AS competitor_ids,
                MAX(observed_at) AS last_observed_at
            FROM item_rep
            GROUP BY item_id
        )
        SELECT t.* EXCEPT(group_key, item_size_sig),
               COALESCE(im.market_min_in_stock, mm.market_min_in_stock) AS market_min_in_stock,
               COALESCE(im.market_min, mm.market_min) AS market_min,
               COALESCE(im.market_median, mm.market_median) AS market_median,
               COALESCE(im.competitor_count, mm.competitor_count) AS competitor_count,
               COALESCE(im.competitor_ids, mm.competitor_ids) AS competitor_ids,
               COALESCE(im.last_observed_at, mm.last_observed_at) AS last_observed_at
        FROM tracked_active t
        LEFT JOIN item_market im ON im.item_id = t.item_id
        LEFT JOIN matrix_market mm ON mm.group_key = t.group_key
        ORDER BY t.revenue_rank
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    _cache_set(cache_key, rows)
    return rows


def get_item_competitor_prices(item_id: str, days: int = 45):
    """One representative price per competitor for an item — the per-store breakdown.
    Includes matrix-sibling matches, but keeps it size-accurate: for each store we
    prefer a listing whose size matches this item (falling back to size-unknown, then
    any), and among those the freshest in-stock, then cheapest — so a stale or wrong-
    size sibling can't undercut the real price. NULL competitor_id → URL host name."""
    ensure_pi_tables()
    rows = _rows(f"""
        WITH me AS (
            SELECT item_id, item_matrix_id, attribute_1, attribute_2, attribute_3
            FROM `{T_TRACKED}` WHERE item_id = @item_id
        ),
        variants AS (
            SELECT t.item_id FROM `{T_TRACKED}` t, me
            WHERE t.item_id = me.item_id
               OR (me.item_matrix_id IS NOT NULL AND t.item_matrix_id = me.item_matrix_id)
        ),
        latest AS (
            SELECT o.* FROM `{T_OBSERVATIONS}` o
            JOIN variants v ON v.item_id = o.match_item_id
            WHERE o.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND o.price IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY o.diff_key ORDER BY o.observed_at DESC) = 1
        )
        SELECT l.competitor_id, c.name AS competitor_name, l.source, l.url,
               l.competitor_title, l.price, l.compare_at_price, l.in_stock,
               l.observed_at, l.match_method, l.match_confidence,
               me.attribute_1 AS it_a1, me.attribute_2 AS it_a2, me.attribute_3 AS it_a3
        FROM latest l
        LEFT JOIN `{T_COMPETITORS}` c ON c.competitor_id = l.competitor_id
        CROSS JOIN me
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])
    from urllib.parse import urlparse
    from .matcher import size_matches_item

    # group latest-per-listing rows by store, pick the best representative
    stores: dict = {}
    for r in rows:
        r["_size"] = size_matches_item(
            r.get("competitor_title"), [r.get("it_a1"), r.get("it_a2"), r.get("it_a3")])
        key = r.get("competitor_id") or f"url:{r.get('url')}"
        stores.setdefault(key, []).append(r)

    out = []
    for group in stores.values():
        pool = ([r for r in group if r["_size"] is True]
                or [r for r in group if r["_size"] is None] or group)
        best = sorted(pool, key=lambda r: (
            1 if r.get("in_stock") else 0,
            r.get("observed_at") or "",
            -(r.get("price") or 0.0),
        ), reverse=True)[0]
        if not best.get("competitor_name") and best.get("url"):
            best["competitor_name"] = urlparse(best["url"]).netloc
        for k in ("_size", "it_a1", "it_a2", "it_a3"):
            best.pop(k, None)
        out.append(best)
    out.sort(key=lambda r: r.get("price") or 0.0)
    return out


def update_tracked_product(item_id: str, fields: dict):
    """Updates the manual-control fields (pinned/excluded/min_price_override/map_price)."""
    ensure_pi_tables()
    allowed = {"pinned", "excluded", "min_price_override", "map_price"}
    sets, params = [], [bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id))]
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("pinned", "excluded"):
            sets.append(f"{k} = @{k}")
            params.append(bigquery.ScalarQueryParameter(k, "BOOL", bool(v)))
        else:
            sets.append(f"{k} = @{k}")
            params.append(bigquery.ScalarQueryParameter(k, "FLOAT64", None if v is None else float(v)))
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP()")
    get_bq_client().query(
        f"UPDATE `{T_TRACKED}` SET {', '.join(sets)} WHERE item_id = @item_id",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    invalidate_pi_caches()


def update_tracked_current_retail(item_id: str, price: float):
    try:
        get_bq_client().query(
            f"UPDATE `{T_TRACKED}` SET current_retail = @price, updated_at = CURRENT_TIMESTAMP() "
            "WHERE item_id = @item_id",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("price", "FLOAT64", float(price)),
                bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
            ]),
        ).result()
    except Exception as e:
        print(f"pi: failed to update tracked retail: {e}")


def record_our_price_snapshot():
    """Append one row per active tracked item to pi_our_price_history, but only
    where current_retail differs (> $0.005) from that item's last recorded price —
    forward-only change-points powering the chart's "our price" line. The first run
    seeds every item's opening point; unchanged prices produce zero rows, so calling
    it every scrape is cheap. INSERT...SELECT (DML, not streaming) keeps the table
    small. Failures never abort a scrape."""
    ensure_pi_tables()
    try:
        get_bq_client().query(f"""
            INSERT INTO `{T_OUR_PRICE_HISTORY}` (item_id, observed_at, price)
            WITH last_recorded AS (
                SELECT item_id, price
                FROM `{T_OUR_PRICE_HISTORY}`
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY item_id ORDER BY observed_at DESC) = 1
            )
            SELECT t.item_id, CURRENT_TIMESTAMP(), t.current_retail
            FROM `{T_TRACKED}` t
            LEFT JOIN last_recorded h ON h.item_id = t.item_id
            WHERE t.current_retail IS NOT NULL
              AND COALESCE(t.archived, FALSE) = FALSE
              AND (h.price IS NULL OR ABS(h.price - t.current_retail) > 0.005)
        """).result()
    except Exception as e:
        print(f"pi: failed to record our price snapshot: {e}")


def search_snapshot_items(q: str, limit: int = 40):
    """Item search (for pinning) against the latest master snapshot, joined to
    item_history/item_matrix_history for matrix + variant attributes so the UI
    can tell 'Rapha Core Bib - M / Black' from its 19 siblings."""
    like = f"%{q.lower()}%"
    return _rows(f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d FROM `{LS_DATASET}.v_master_snapshot_latest`
        ),
        attrs AS (
            SELECT
                CAST(id AS STRING) AS item_id,
                CAST(NULLIF(item_matrix_id, 0) AS STRING) AS item_matrix_id,
                COALESCE(NULLIF(upc, ''), NULLIF(ean, '')) AS raw_upc,
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
                ANY_VALUE(manufacturer_sku) AS manufacturer_sku,
                ANY_VALUE(CAST(system_sku AS STRING)) AS system_sku,
                MAX(item_current_price) AS current_retail,
                MAX(COALESCE(sales_revenue_l90d, 0)) AS rev
            FROM `{LS_DATASET}.v_master_snapshot_latest` s CROSS JOIN latest
            WHERE s.snapshot_date_local = latest.d
              AND COALESCE(item_archived, FALSE) = FALSE
            GROUP BY item_id
        )
        SELECT
            s.item_id, s.title, s.brand, s.manufacturer_sku, s.system_sku,
            s.current_retail,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(a.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized,
            a.item_matrix_id, m.matrix_description,
            a.attribute_1, a.attribute_2, a.attribute_3
        FROM snap s
        LEFT JOIN attrs a USING (item_id)
        LEFT JOIN matrix m ON m.matrix_id = a.item_matrix_id
        WHERE LOWER(COALESCE(s.title, '')) LIKE @like
           OR LOWER(COALESCE(s.manufacturer_sku, '')) LIKE @like
           OR LOWER(COALESCE(m.matrix_description, '')) LIKE @like
           OR s.item_id = @exact
        ORDER BY s.rev DESC, m.matrix_description, a.attribute_1
        LIMIT {int(limit)}
    """, params=[
        bigquery.ScalarQueryParameter("like", "STRING", like),
        bigquery.ScalarQueryParameter("exact", "STRING", q.strip()),
    ])


# ---------------------------------------------------------------------------
# Observations / diffing
# ---------------------------------------------------------------------------

def get_latest_observation_map(days: int = 45):
    """Latest observation per diff_key, as {diff_key: {price, in_stock}}. Bounded
    to a trailing window so the dict stays small on the 512MB instance."""
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT diff_key, price, in_stock
        FROM `{T_OBSERVATIONS}`
        WHERE observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    return {r["diff_key"]: r for r in rows if r.get("diff_key")}


def get_item_observations(item_id: str, days: int = 120):
    return _rows(f"""
        SELECT observed_at, competitor_id, url, price, compare_at_price, in_stock,
               match_method, match_confidence, competitor_title
        FROM `{T_OBSERVATIONS}`
        WHERE match_item_id = @item_id
          AND observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ORDER BY observed_at
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])


def get_item_price_history(item_id: str, days: int = 120):
    """Change-point-compressed price history for the expandable chart: our price
    line + one line per competitor. Both series are reduced server-side (SQL LAG)
    to only the points where the price actually changed, so the payload stays tiny
    even over a long window (one item, lazy on expand). A price holds until the next
    change (the client draws it as a step line).

    Competitor series aggregate at the model/matrix grain (matrix siblings included,
    like get_item_competitor_prices); when a competitor has multiple listings at one
    snapshot we keep the in-stock, lowest price as the representative point."""
    ensure_pi_tables()
    ours = _rows(f"""
        SELECT observed_at, price FROM (
            SELECT observed_at, price,
                   LAG(price) OVER (ORDER BY observed_at) AS prev_price,
                   MAX(observed_at) OVER () AS last_at
            FROM `{T_OUR_PRICE_HISTORY}`
            WHERE item_id = @item_id
              AND observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND price IS NOT NULL
        )
        WHERE prev_price IS NULL OR ABS(price - prev_price) > 0.005 OR observed_at = last_at
        ORDER BY observed_at
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])

    comp_rows = _rows(f"""
        WITH me AS (
            SELECT item_id, item_matrix_id FROM `{T_TRACKED}`
            WHERE item_id = @item_id
        ),
        variants AS (
            SELECT t.item_id FROM `{T_TRACKED}` t, me
            WHERE t.item_id = me.item_id
               OR (me.item_matrix_id IS NOT NULL AND t.item_matrix_id = me.item_matrix_id)
        ),
        obs AS (
            SELECT
                COALESCE(o.competitor_id, CONCAT('url:', o.url)) AS series_key,
                o.competitor_id, o.url, o.run_id, o.observed_at, o.price, o.in_stock
            FROM `{T_OBSERVATIONS}` o
            JOIN variants v ON v.item_id = o.match_item_id
            WHERE o.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND o.price IS NOT NULL
        ),
        per_run AS (
            -- One representative point per competitor per scrape: prefer an
            -- in-stock listing, then the lowest price. Grouping by run_id (not
            -- the microsecond-exact observed_at) collapses a store's multiple
            -- listings for the item into a single effective price.
            SELECT
                series_key,
                ANY_VALUE(competitor_id) AS competitor_id,
                ANY_VALUE(url) AS url,
                MIN(observed_at) AS observed_at,
                ARRAY_AGG(price ORDER BY IF(COALESCE(in_stock, FALSE), 0, 1), price
                          LIMIT 1)[OFFSET(0)] AS price,
                MAX(COALESCE(in_stock, FALSE)) AS in_stock
            FROM obs
            GROUP BY series_key, run_id
        ),
        changed AS (
            SELECT *,
                   LAG(price) OVER (PARTITION BY series_key ORDER BY observed_at) AS prev_price,
                   MAX(observed_at) OVER (PARTITION BY series_key) AS last_at
            FROM per_run
        )
        SELECT c.series_key, c.competitor_id, comp.name AS competitor_name, c.url,
               c.observed_at, c.price, c.in_stock
        FROM changed c
        LEFT JOIN `{T_COMPETITORS}` comp ON comp.competitor_id = c.competitor_id
        WHERE c.prev_price IS NULL OR ABS(c.price - c.prev_price) > 0.005
           OR c.observed_at = c.last_at
        ORDER BY c.series_key, c.observed_at
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])

    from urllib.parse import urlparse
    series: dict = {}
    for r in comp_rows:
        key = r["series_key"]
        s = series.get(key)
        if s is None:
            name = r.get("competitor_name")
            if not name and r.get("url"):
                name = urlparse(r["url"]).netloc
            s = {
                "competitor_id": r.get("competitor_id"),
                "competitor_name": name or "Unknown store",
                "points": [],
            }
            series[key] = s
        s["points"].append({
            "observed_at": r["observed_at"],
            "price": r["price"],
            "in_stock": r["in_stock"],
        })
    competitors = sorted(series.values(), key=lambda s: s["competitor_name"].lower())
    return {"ours": ours, "competitors": competitors}


# ---------------------------------------------------------------------------
# Change events (feature a) — batch loaded; ack is a plain UPDATE.
# ---------------------------------------------------------------------------

def get_change_events(days: int = 14, acknowledged=None, competitor_id=None,
                      event_types=None, min_abs_pct=None, brand=None, run_id=None,
                      limit: int = 200):
    ensure_pi_tables()
    where = "WHERE occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
    params = [bigquery.ScalarQueryParameter("days", "INT64", days)]
    if run_id:
        where += " AND run_id = @run_id"
        params.append(bigquery.ScalarQueryParameter("run_id", "STRING", str(run_id)))
    if acknowledged is not None:
        where += f" AND COALESCE(acknowledged, FALSE) = {'TRUE' if acknowledged else 'FALSE'}"
    if competitor_id:
        where += " AND competitor_id = @cid"
        params.append(bigquery.ScalarQueryParameter("cid", "STRING", str(competitor_id)))
    if event_types:
        where += " AND event_type IN UNNEST(@types)"
        params.append(bigquery.ArrayQueryParameter("types", "STRING", [str(t) for t in event_types]))
    if min_abs_pct is not None:
        where += " AND ABS(COALESCE(pct_change, 0)) >= @min_pct"
        params.append(bigquery.ScalarQueryParameter("min_pct", "FLOAT64", float(min_abs_pct)))
    if brand:
        where += " AND LOWER(COALESCE(item_brand, '')) = @brand"
        params.append(bigquery.ScalarQueryParameter("brand", "STRING", str(brand).lower()))
    return _rows(f"""
        SELECT * FROM `{T_EVENTS}` {where}
        ORDER BY occurred_at DESC LIMIT {int(limit)}
    """, params=params)


def count_unacknowledged_events() -> int:
    ensure_pi_tables()
    cached = _cache_get("unack_count")
    if cached is not None:
        return cached
    rows = _rows(f"""
        SELECT COUNT(*) AS n FROM `{T_EVENTS}`
        WHERE COALESCE(acknowledged, FALSE) = FALSE
          AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    """)
    n = rows[0]["n"] if rows else 0
    _cache_set("unack_count", n)
    return n


def acknowledge_events(event_ids: list):
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_EVENTS}` SET acknowledged = TRUE, acknowledged_at = CURRENT_TIMESTAMP() "
        "WHERE event_id IN UNNEST(@ids)",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("ids", "STRING", [str(e) for e in event_ids]),
        ]),
    ).result()
    invalidate_pi_caches()


def mark_events_notified(event_ids: list):
    """Stamp the reserved notified/notified_at columns after a Slack dispatch so
    no future re-notify path double-sends. No-op on empty input."""
    if not event_ids:
        return
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_EVENTS}` SET notified = TRUE, notified_at = CURRENT_TIMESTAMP() "
        "WHERE event_id IN UNNEST(@ids)",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("ids", "STRING", [str(e) for e in event_ids]),
        ]),
    ).result()


# ---------------------------------------------------------------------------
# Product links (confirmed competitor-listing <-> item matches + pending
# verification candidates). One row per match_key; write-precedence: rows a
# human decided (decided_by set, or source in human/manual_url) are never
# auto-overwritten.
# ---------------------------------------------------------------------------

def get_product_links(status=None, item_id=None, unverified_only=False,
                      active_items_only=False, limit: int = 1000):
    """Links joined with the item's description so the UI never has to fall back
    to a raw item_id (links can reference archived items the /tracked payload
    doesn't carry). active_items_only hides links whose item is archived —
    they're frozen, not dead: unarchiving the item brings them straight back."""
    ensure_pi_tables()
    where, params = [], []
    if status:
        where.append("l.status = @status")
        params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
    if item_id:
        where.append("l.item_id = @item_id")
        params.append(bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)))
    if unverified_only:
        where.append("l.llm_verdict IS NULL")
    if active_items_only:
        where.append("COALESCE(t.archived, FALSE) = FALSE")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return _rows(f"""
        SELECT l.*, t.title AS item_title, t.brand AS item_brand,
               t.matrix_description AS item_matrix_description,
               t.attribute_1 AS item_attribute_1,
               t.attribute_2 AS item_attribute_2,
               t.attribute_3 AS item_attribute_3,
               t.upc_normalized AS item_upc, t.system_sku AS item_system_sku
        FROM `{T_LINKS}` l
        LEFT JOIN `{T_TRACKED}` t ON t.item_id = l.item_id
        {clause}
        ORDER BY l.fuzzy_score DESC, l.created_at DESC
        LIMIT {int(limit)}
    """, params=params)


def get_link_match_keys() -> set:
    """All match_keys with any row (any status) — the candidate generator skips
    these so rejected links act as tombstones and the LLM is never re-asked."""
    ensure_pi_tables()
    rows = _rows(f"SELECT DISTINCT match_key FROM `{T_LINKS}`")
    return {r["match_key"] for r in rows if r.get("match_key")}


def get_rejected_match_keys() -> set:
    """Match keys a human rejected — the matcher skips these so a rejected listing
    (incl. a fuzzy/UPC auto-match) never returns on the next scrape."""
    ensure_pi_tables()
    rows = _rows(f"SELECT DISTINCT match_key FROM `{T_LINKS}` "
                 "WHERE status = 'rejected' AND match_key IS NOT NULL")
    return {r["match_key"] for r in rows if r.get("match_key")}


def backfill_url_competitor_ids(apply: bool = False) -> dict:
    """Associate URL-based rows that have a NULL competitor_id with the registered
    competitor that shares their domain. Fixes the case where a store was added both
    as a competitor AND as a tracked URL, so it stops appearing as two separate
    'stores' on an item. Dry-run by default. Only touches pi_ tables:
    pi_tracked_urls, pi_price_observations, pi_product_links (matched to
    pi_competitors by NET.REG_DOMAIN)."""
    ensure_pi_tables()

    def _count(table, urlcol):
        rows = _rows(f"""
            SELECT COUNT(*) AS n
            FROM `{table}` t JOIN `{T_COMPETITORS}` c
              ON NET.REG_DOMAIN(t.{urlcol}) = NET.REG_DOMAIN(c.base_url)
            WHERE t.competitor_id IS NULL AND t.{urlcol} IS NOT NULL
              AND NET.REG_DOMAIN(t.{urlcol}) IS NOT NULL
        """)
        return rows[0]["n"] if rows else 0

    counts = {
        "pi_tracked_urls": _count(T_URLS, "url"),
        "pi_price_observations": _count(T_OBSERVATIONS, "url"),
        "pi_product_links": _count(T_LINKS, "competitor_url"),
    }
    report = {"applied": apply, "would_update": counts}
    if not apply:
        return report

    client = get_bq_client()

    def _apply(table, urlcol, extra=""):
        client.query(f"""
            UPDATE `{table}` t SET competitor_id = c.competitor_id{extra}
            FROM `{T_COMPETITORS}` c
            WHERE t.competitor_id IS NULL AND t.{urlcol} IS NOT NULL
              AND NET.REG_DOMAIN(t.{urlcol}) = NET.REG_DOMAIN(c.base_url)
              AND NET.REG_DOMAIN(t.{urlcol}) IS NOT NULL
        """).result()

    _apply(T_URLS, "url")
    _apply(T_OBSERVATIONS, "url")
    _apply(T_LINKS, "competitor_url", extra=", updated_at = CURRENT_TIMESTAMP()")
    invalidate_pi_caches()
    return report


def reject_competitor_listing(item_id: str, competitor_id, url, decided_by: str = "Dashboard"):
    """Reject one competitor listing for one item — the manual escape hatch for a
    stuck match (e.g. a fuzzy title auto-match that has no reviewable link row).
    Reconstructs the listing's match_key from its latest observation (SKU-preferred,
    matching what the scraper writes), tombstones it, and nulls match_item_id on that
    listing's observations so it drops from the market/history immediately."""
    from .matcher import build_match_key
    ensure_pi_tables()
    cid = str(competitor_id) if competitor_id else None
    url = url or ""
    cid_clause = "competitor_id = @cid" if cid else "competitor_id IS NULL"
    params = [
        bigquery.ScalarQueryParameter("iid", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("url", "STRING", url),
    ]
    if cid:
        params.append(bigquery.ScalarQueryParameter("cid", "STRING", cid))
    obs = _rows(f"""
        SELECT competitor_id, competitor_sku, url, competitor_title
        FROM `{T_OBSERVATIONS}`
        WHERE match_item_id = @iid AND COALESCE(url, '') = @url AND {cid_clause}
        ORDER BY observed_at DESC LIMIT 1
    """, params=params)
    if not obs:
        return {"status": "error", "reason": "no observation found for that listing"}
    o = obs[0]
    key_cid = o.get("competitor_id") or cid or ""
    match_key = build_match_key(key_cid, {
        "sku": o.get("competitor_sku"), "url": o.get("url"),
        "title": o.get("competitor_title"),
    })
    client = get_bq_client()
    job = client.query(
        f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
        "updated_at = CURRENT_TIMESTAMP() WHERE match_key = @mk",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
            bigquery.ScalarQueryParameter("mk", "STRING", match_key),
        ]),
    )
    job.result()
    if not job.num_dml_affected_rows:
        now = utcnow_iso()
        insert_product_links([{
            "link_id": str(uuid.uuid4()), "item_id": str(item_id),
            "competitor_id": cid, "match_key": match_key,
            "competitor_url": o.get("url"), "competitor_sku": o.get("competitor_sku"),
            "competitor_title": o.get("competitor_title"), "gtin": None,
            "level": "variant", "status": "rejected", "source": "human",
            "confidence": None, "fuzzy_score": None, "llm_verdict": None,
            "llm_reason": "rejected from tracked-products breakdown", "our_price": None,
            "their_price": None, "decided_by": decided_by,
            "created_at": now, "updated_at": now,
        }])
    client.query(
        f"UPDATE `{T_OBSERVATIONS}` SET match_item_id = NULL "
        f"WHERE match_item_id = @iid AND COALESCE(url, '') = @url AND {cid_clause}",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()
    invalidate_pi_caches()
    return {"status": "rejected", "match_key": match_key}


def insert_product_links(rows: list):
    """Insert-only MERGE on match_key: existing rows (including tombstones and
    human decisions) are never touched."""
    if not rows:
        return
    ensure_pi_tables()
    _json_ready(rows)
    client = get_bq_client()
    temp_table_id = f"{T_LINKS}_temp"
    target_schema = client.get_table(T_LINKS).schema
    row_keys = set(rows[0].keys())
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=[f for f in target_schema if f.name in row_keys],
    )
    client.load_table_from_json(rows, temp_table_id, job_config=job_config).result()
    cols = ", ".join(rows[0].keys())
    vals = ", ".join(f"S.{c}" for c in rows[0].keys())
    client.query(f"""
        MERGE `{T_LINKS}` T
        USING (
            SELECT * FROM `{temp_table_id}`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY match_key ORDER BY fuzzy_score DESC) = 1
        ) S
        ON T.match_key = S.match_key
        WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})
    """).result()
    invalidate_pi_caches()


def upsert_human_link(row: dict):
    """Full upsert for human-created/decided links — overrides any prior row."""
    _merge_upsert(
        T_LINKS, [row], "match_key",
        update_cols=["item_id", "competitor_id", "competitor_url", "competitor_sku",
                     "competitor_title", "level", "status", "source", "confidence",
                     "decided_by", "updated_at"],
        insert_cols=list(row.keys()),
    )
    invalidate_pi_caches()


def update_link_verdicts(rows: list):
    """Update-only MERGE on link_id, applying LLM verdicts. Skips rows a human
    has already decided."""
    if not rows:
        return
    ensure_pi_tables()
    client = get_bq_client()
    temp_table_id = f"{T_LINKS}_verdicts_temp"
    target_schema = client.get_table(T_LINKS).schema
    row_keys = set(rows[0].keys())
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=[f for f in target_schema if f.name in row_keys],
    )
    client.load_table_from_json(rows, temp_table_id, job_config=job_config).result()
    set_clause = ", ".join(f"T.{c} = S.{c}" for c in row_keys if c != "link_id")
    client.query(f"""
        MERGE `{T_LINKS}` T
        USING `{temp_table_id}` S
        ON T.link_id = S.link_id
        WHEN MATCHED AND T.decided_by IS NULL AND T.source NOT IN ('human', 'manual_url')
        THEN UPDATE SET {set_clause}
    """).result()
    invalidate_pi_caches()


def decide_link(link_id: str, status: str, decided_by: str = "Dashboard"):
    """Human confirm/reject from the review UI — permanent (never auto-overwritten)."""
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_LINKS}` SET status = @status, decided_by = @actor, "
        "confidence = IF(@status = 'confirmed', 1.0, confidence), "
        "updated_at = CURRENT_TIMESTAMP() WHERE link_id = @lid",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
            bigquery.ScalarQueryParameter("lid", "STRING", str(link_id)),
        ]),
    ).result()
    invalidate_pi_caches()


def confirm_link(link_id: str, decided_by: str = "Dashboard", replace: bool = False) -> dict:
    """Guarded confirm — the single choke point for both single-row and bulk
    confirms. Enforces the invariant that one of our items links to exactly one
    competitor variant per store:
      - a competitor variant whose color/size conflicts with the item is rejected
        (it's the wrong variant, not ours);
      - if the item already has a confirmed link at that store, the confirm is
        skipped unless replace=True, in which case the existing confirmed link(s)
        at that store are rejected first and this one takes over (the override
        path from the Matching tab).
    Returns {status: confirmed|rejected|skipped|error, reason, can_replace?, replaced?}."""
    from .matcher import parse_variant_options, attributes_conflict
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT l.item_id, l.competitor_id, l.competitor_title,
               t.attribute_1 AS a1, t.attribute_2 AS a2, t.attribute_3 AS a3
        FROM `{T_LINKS}` l LEFT JOIN `{T_TRACKED}` t ON t.item_id = l.item_id
        WHERE l.link_id = @lid
    """, params=[bigquery.ScalarQueryParameter("lid", "STRING", str(link_id))])
    if not rows:
        return {"status": "error", "reason": "link not found"}
    l = rows[0]
    if attributes_conflict(parse_variant_options(l.get("competitor_title")),
                           [l.get("a1"), l.get("a2"), l.get("a3")]):
        decide_link(link_id, "rejected", decided_by=decided_by)
        return {"status": "rejected", "reason": "color/size mismatch with the item"}
    dupes = _rows(f"""
        SELECT link_id FROM `{T_LINKS}`
        WHERE item_id = @iid AND COALESCE(competitor_id, '') = @cid
          AND status = 'confirmed' AND link_id != @lid
    """, params=[
        bigquery.ScalarQueryParameter("iid", "STRING", str(l["item_id"])),
        bigquery.ScalarQueryParameter("cid", "STRING", str(l.get("competitor_id") or "")),
        bigquery.ScalarQueryParameter("lid", "STRING", str(link_id)),
    ])
    if dupes:
        if not replace:
            return {"status": "skipped", "can_replace": True,
                    "reason": "item already has a confirmed link at this store"}
        get_bq_client().query(
            f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
            "updated_at = CURRENT_TIMESTAMP() WHERE link_id IN UNNEST(@ids)",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
                bigquery.ArrayQueryParameter("ids", "STRING", [d["link_id"] for d in dupes]),
            ]),
        ).result()
    decide_link(link_id, "confirmed", decided_by=decided_by)
    return {"status": "confirmed", "replaced": len(dupes)}


def cleanup_mismatched_links(apply: bool = False) -> dict:
    """One-off hygiene sweep (dry-run by default). Rejects links whose competitor
    color/size conflicts with the item's attributes (both confirmed and pending),
    then enforces one confirmed link per (item_id, competitor) by keeping the best
    survivor and rejecting the rest. When applying, also nulls match_item_id on the
    observations those wrong CONFIRMED links attributed, so market columns + the
    price-history chart correct immediately. Returns a report."""
    from collections import defaultdict
    from .matcher import parse_variant_options, attributes_conflict
    ensure_pi_tables()
    confirmed = get_product_links(status="confirmed", limit=5000)
    pending = get_product_links(status="pending", limit=5000)

    def attrs(l):
        return [l.get("item_attribute_1"), l.get("item_attribute_2"), l.get("item_attribute_3")]

    attr_reject = [
        l for l in confirmed + pending
        if attributes_conflict(parse_variant_options(l.get("competitor_title")), attrs(l))
    ]
    attr_ids = {l["link_id"] for l in attr_reject}

    surviving = [l for l in confirmed if l["link_id"] not in attr_ids]
    by_pair = defaultdict(list)
    for l in surviving:
        by_pair[(l.get("item_id"), l.get("competitor_id"))].append(l)

    def rank(l):
        return (l.get("llm_verdict") == "same_variant",
                l.get("source") in ("gtin", "manual_url", "human", "attr"),
                l.get("confidence") or 0, l.get("fuzzy_score") or 0)

    dupe_reject = []
    for links in by_pair.values():
        if len(links) > 1:
            keep = sorted(links, key=rank, reverse=True)[0]
            dupe_reject += [l for l in links if l["link_id"] != keep["link_id"]]

    reject_ids = attr_ids | {l["link_id"] for l in dupe_reject}
    obs_targets = [l for l in confirmed
                   if l["link_id"] in reject_ids and l.get("competitor_url")]

    report = {
        "applied": apply,
        "attr_reject_confirmed": sum(1 for l in attr_reject if l["status"] == "confirmed"),
        "attr_reject_pending": sum(1 for l in attr_reject if l["status"] == "pending"),
        "dupe_reject_confirmed": len(dupe_reject),
        "total_links_rejected": len(reject_ids),
        "observation_listings_reattributed": len(obs_targets),
        "samples": [
            {"status": l["status"], "item_id": l.get("item_id"), "attrs": attrs(l),
             "competitor_title": l.get("competitor_title")}
            for l in (attr_reject + dupe_reject)[:12]
        ],
    }
    if not apply:
        return report

    client = get_bq_client()
    if reject_ids:
        client.query(
            f"UPDATE `{T_LINKS}` SET status='rejected', decided_by='cleanup', "
            "updated_at=CURRENT_TIMESTAMP() WHERE link_id IN UNNEST(@ids)",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("ids", "STRING", list(reject_ids))]),
        ).result()
    if obs_targets:
        keys = [f"{l['item_id']}|{l.get('competitor_id') or ''}|{l['competitor_url']}"
                for l in obs_targets]
        client.query(
            f"UPDATE `{T_OBSERVATIONS}` SET match_item_id = NULL "
            "WHERE match_item_id IS NOT NULL AND CONCAT(CAST(match_item_id AS STRING), '|', "
            "COALESCE(competitor_id,''), '|', COALESCE(url,'')) IN UNNEST(@keys)",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("keys", "STRING", keys)]),
        ).result()
    invalidate_pi_caches()
    return report


def sweep_domain_for_item(item_id: str, url: str, decided_by: str = "Dashboard") -> dict:
    """Make a pinned manual URL the SOLE match for an item at a store. Beyond
    tombstoning conflicting link rows (any source), this also handles fuzzy
    'title match' listings that have NO link row: it reconstructs their match_key
    so the matcher won't re-match them, and nulls their observations so they drop
    from the market/history. The incoming url:{url} link is left intact."""
    from .matcher import build_match_key
    ensure_pi_tables()
    keep_key = f"url:{url}"
    client = get_bq_client()
    dom_params = [
        bigquery.ScalarQueryParameter("iid", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("url", "STRING", url),
    ]
    # 1) reject every OTHER link row for this item at this registrable domain
    client.query(
        f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
        "updated_at = CURRENT_TIMESTAMP() "
        "WHERE item_id = @iid AND status IN ('confirmed', 'pending') "
        "AND competitor_url IS NOT NULL "
        "AND NET.REG_DOMAIN(competitor_url) = NET.REG_DOMAIN(@url) "
        "AND match_key != @keep",
        job_config=bigquery.QueryJobConfig(query_parameters=dom_params + [
            bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
            bigquery.ScalarQueryParameter("keep", "STRING", keep_key),
        ]),
    ).result()
    # 2) tombstone fuzzy listings (attributed to the item at the domain, no link row)
    listings = _rows(f"""
        SELECT competitor_id, url AS o_url, competitor_sku, competitor_title
        FROM `{T_OBSERVATIONS}`
        WHERE match_item_id = @iid AND url IS NOT NULL
          AND NET.REG_DOMAIN(url) = NET.REG_DOMAIN(@url) AND url != @url
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY COALESCE(competitor_id, ''), url ORDER BY observed_at DESC) = 1
    """, params=dom_params)
    existing = get_link_match_keys()
    now = utcnow_iso()
    tombstones = []
    for r in listings:
        cid = r.get("competitor_id") or ""
        mk = build_match_key(cid, {"sku": r.get("competitor_sku"), "url": r.get("o_url"),
                                   "title": r.get("competitor_title")})
        if mk in existing or mk == keep_key:
            continue
        existing.add(mk)
        tombstones.append({
            "link_id": str(uuid.uuid4()), "item_id": str(item_id),
            "competitor_id": r.get("competitor_id"), "match_key": mk,
            "competitor_url": r.get("o_url"), "competitor_sku": r.get("competitor_sku"),
            "competitor_title": r.get("competitor_title"), "gtin": None,
            "level": "variant", "status": "rejected", "source": "human",
            "confidence": None, "fuzzy_score": None, "llm_verdict": None,
            "llm_reason": "superseded by a pinned URL at this store", "our_price": None,
            "their_price": None, "decided_by": decided_by,
            "created_at": now, "updated_at": now,
        })
    if tombstones:
        insert_product_links(tombstones)
    # 3) null the observations of every OTHER listing at the domain
    client.query(
        f"UPDATE `{T_OBSERVATIONS}` SET match_item_id = NULL "
        "WHERE match_item_id = @iid AND url IS NOT NULL "
        "AND NET.REG_DOMAIN(url) = NET.REG_DOMAIN(@url) AND url != @url",
        job_config=bigquery.QueryJobConfig(query_parameters=dom_params),
    ).result()
    invalidate_pi_caches()
    return {"listings_swept": len(listings), "tombstoned": len(tombstones)}


def reject_conflicting_links(item_id: str, domain: str, decided_by: str = "Dashboard"):
    """When a human pins the true URL for an item at a store, tombstone any
    auto-created (gtin/llm) links for the same item at the same domain so the
    wrong match can't resurface."""
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
        "updated_at = CURRENT_TIMESTAMP() "
        "WHERE item_id = @item_id AND source IN ('gtin', 'llm', 'attr') "
        "AND status IN ('confirmed', 'pending') "
        "AND STRPOS(COALESCE(competitor_url, ''), @domain) > 0",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
            bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
            bigquery.ScalarQueryParameter("domain", "STRING", domain),
        ]),
    ).result()
    invalidate_pi_caches()


def count_pending_links() -> int:
    ensure_pi_tables()
    cached = _cache_get("pending_links")
    if cached is not None:
        return cached
    # Mirrors the pending review queue: links frozen by item archival don't count.
    rows = _rows(f"""
        SELECT COUNT(*) AS n
        FROM `{T_LINKS}` l
        LEFT JOIN `{T_TRACKED}` t ON t.item_id = l.item_id
        WHERE l.status = 'pending' AND COALESCE(t.archived, FALSE) = FALSE
    """)
    n = rows[0]["n"] if rows else 0
    _cache_set("pending_links", n)
    return n


# ---------------------------------------------------------------------------
# Digests, push log, runs
# ---------------------------------------------------------------------------

def save_digest(row: dict):
    load_rows(T_DIGESTS, [row])


def get_latest_digest():
    ensure_pi_tables()
    rows = _rows(f"SELECT * FROM `{T_DIGESTS}` ORDER BY created_at DESC LIMIT 1")
    return rows[0] if rows else None


def get_digest_for_run(run_id: str):
    ensure_pi_tables()
    rows = _rows(
        f"SELECT * FROM `{T_DIGESTS}` WHERE run_id = @run_id "
        "ORDER BY created_at DESC LIMIT 1",
        params=[bigquery.ScalarQueryParameter("run_id", "STRING", str(run_id))],
    )
    return rows[0] if rows else None


def log_price_push(row: dict):
    """Streams the price-push audit record (append-only, mirrors log_writeback)."""
    try:
        ensure_pi_tables()
        errors = get_bq_client().insert_rows_json(T_PUSH_LOG, [row])
        if errors:
            print(f"pi: push log errors: {errors}")
    except Exception as e:
        print(f"pi: failed to log price push: {e}")


def get_price_push_logs(limit: int = 100):
    ensure_pi_tables()
    return _rows(f"SELECT * FROM `{T_PUSH_LOG}` ORDER BY pushed_at DESC LIMIT {int(limit)}")


def save_scrape_run(row: dict):
    load_rows(T_RUNS, [row])


def get_scrape_runs(limit: int = 30):
    ensure_pi_tables()
    return _rows(f"SELECT * FROM `{T_RUNS}` ORDER BY started_at DESC LIMIT {int(limit)}")


def has_successful_run_on(local_date_str: str, timezone_name: str) -> bool:
    """True if a success/partial run already started on the given local date —
    the scheduler's double-run guard across restarts."""
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT COUNT(*) AS n FROM `{T_RUNS}`
        WHERE status IN ('success', 'partial')
          AND FORMAT_DATE('%Y-%m-%d', DATE(started_at, @tz)) = @d
    """, params=[
        bigquery.ScalarQueryParameter("tz", "STRING", timezone_name),
        bigquery.ScalarQueryParameter("d", "STRING", local_date_str),
    ])
    return bool(rows and rows[0]["n"] > 0)
