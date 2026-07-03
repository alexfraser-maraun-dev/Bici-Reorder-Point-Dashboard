"""BigQuery persistence for the Price Intelligence feature.

All tables are additive (`pi_` prefix) inside APP_DATASET. Observation/event/run
rows go through batch load jobs, never streaming inserts, so later DML (ack
UPDATEs, retention deletes) is never blocked by a streaming buffer. The only
streaming write is the append-only price-push audit log, mirroring log_writeback.
"""
import threading
import time
import uuid
from datetime import datetime, timezone

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
        ]
        for stmt in statements:
            client.query(stmt).result()
        _tables_ensured = True


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
    rows = _rows(f"SELECT * FROM `{T_URLS}` ORDER BY created_at DESC")
    if not include_disabled:
        rows = [r for r in rows if r.get("enabled")]
    return rows


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

def get_tracked_products(include_excluded: bool = True):
    ensure_pi_tables()
    rows = _rows(f"SELECT * FROM `{T_TRACKED}` ORDER BY revenue_rank")
    if not include_excluded:
        rows = [r for r in rows if not r.get("excluded")]
    return rows


def get_tracked_products_with_market(days: int = 7):
    """Tracked products joined with market min/median from the latest observation
    per (competitor, product) over the trailing window."""
    ensure_pi_tables()
    cache_key = f"tracked_market_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    rows = _rows(f"""
        WITH latest AS (
            SELECT * FROM `{T_OBSERVATIONS}`
            WHERE observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND match_item_id IS NOT NULL AND price IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
        ),
        market AS (
            SELECT
                match_item_id,
                MIN(IF(in_stock, price, NULL)) AS market_min_in_stock,
                MIN(price) AS market_min,
                APPROX_QUANTILES(price, 2)[SAFE_OFFSET(1)] AS market_median,
                COUNT(DISTINCT competitor_id) AS competitor_count,
                MAX(observed_at) AS last_observed_at
            FROM latest
            GROUP BY match_item_id
        )
        SELECT t.*, m.market_min_in_stock, m.market_min, m.market_median,
               m.competitor_count, m.last_observed_at
        FROM `{T_TRACKED}` t
        LEFT JOIN market m ON m.match_item_id = t.item_id
        ORDER BY t.revenue_rank
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    _cache_set(cache_key, rows)
    return rows


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


def search_snapshot_items(q: str, limit: int = 20):
    """Item search (for pinning) against the latest master snapshot."""
    like = f"%{q.lower()}%"
    return _rows(f"""
        WITH latest AS (
            SELECT MAX(snapshot_date_local) AS d FROM `{LS_DATASET}.v_master_snapshot_latest`
        )
        SELECT
            CAST(item_id AS STRING) AS item_id,
            ANY_VALUE(COALESCE(product_display_name, item_description)) AS title,
            ANY_VALUE(brand_name) AS brand,
            ANY_VALUE(manufacturer_sku) AS manufacturer_sku,
            MAX(item_current_price) AS current_retail
        FROM `{LS_DATASET}.v_master_snapshot_latest` s CROSS JOIN latest
        WHERE s.snapshot_date_local = latest.d
          AND COALESCE(item_archived, FALSE) = FALSE
          AND (
            LOWER(COALESCE(product_display_name, item_description)) LIKE @like
            OR LOWER(COALESCE(manufacturer_sku, '')) LIKE @like
            OR CAST(item_id AS STRING) = @exact
          )
        GROUP BY item_id
        ORDER BY MAX(sales_revenue_l90d) DESC
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


# ---------------------------------------------------------------------------
# Change events (feature a) — batch loaded; ack is a plain UPDATE.
# ---------------------------------------------------------------------------

def get_change_events(days: int = 14, acknowledged=None, limit: int = 200):
    ensure_pi_tables()
    where = "WHERE occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
    if acknowledged is not None:
        where += f" AND COALESCE(acknowledged, FALSE) = {'TRUE' if acknowledged else 'FALSE'}"
    return _rows(f"""
        SELECT * FROM `{T_EVENTS}` {where}
        ORDER BY occurred_at DESC LIMIT {int(limit)}
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])


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


# ---------------------------------------------------------------------------
# Digests, push log, runs
# ---------------------------------------------------------------------------

def save_digest(row: dict):
    load_rows(T_DIGESTS, [row])


def get_latest_digest():
    ensure_pi_tables()
    rows = _rows(f"SELECT * FROM `{T_DIGESTS}` ORDER BY created_at DESC LIMIT 1")
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
