"""BigQuery persistence for the Price Intelligence feature.

All tables are additive (`pi_` prefix) inside APP_DATASET. Observation/event/run
rows go through batch load jobs, never streaming inserts, so later DML (ack
UPDATEs, retention deletes) is never blocked by a streaming buffer. The only
streaming write is the append-only price-push audit log, mirroring log_writeback.
"""
import contextlib
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from google.cloud import bigquery
from google.api_core.exceptions import NotFound

from app.services.bigquery_sync import get_bq_client, APP_DATASET, LS_DATASET
from . import config

T_COMPETITORS = f"{APP_DATASET}.pi_competitors"
T_TRACKED = f"{APP_DATASET}.pi_tracked_products"
T_TRACKED_MATRICES = f"{APP_DATASET}.pi_tracked_matrices"
# Prebuilt one-row-per-item search index (materialized by seeding.refresh_item_search_index
# via CREATE OR REPLACE, so it never accumulates). Backs the fast path of
# search_snapshot_items; absent until the first build, which falls back to the live query.
T_ITEM_SEARCH = f"{APP_DATASET}.pi_item_search"
T_URLS = f"{APP_DATASET}.pi_tracked_urls"
T_OBSERVATIONS = f"{APP_DATASET}.pi_price_observations"
T_EVENTS = f"{APP_DATASET}.pi_change_events"
T_DIGESTS = f"{APP_DATASET}.pi_digests"
T_PUSH_LOG = f"{APP_DATASET}.pi_price_push_log"
T_RUNS = f"{APP_DATASET}.pi_scrape_runs"
T_LINKS = f"{APP_DATASET}.pi_product_links"
T_OUR_PRICE_HISTORY = f"{APP_DATASET}.pi_our_price_history"
T_SETTINGS = f"{APP_DATASET}.pi_settings"

# Comparison math treats competitor prices as CAD (all competitors are Canadian
# storefronts). A listing that self-reports another currency must not enter
# market mins / undercut / MAP / price-index math — a USD price would read ~35%
# cheaper than reality. NULL/blank means the site didn't report one → CAD.
def sql_cad_only(alias: str = "") -> str:
    col = f"{alias}.currency" if alias else "currency"
    return f"COALESCE(NULLIF(UPPER(TRIM({col})), ''), 'CAD') = 'CAD'"


# Join target for pi_tracked_products that collapses accidental duplicate
# item_id rows (two concurrent manual-pin MERGEs can both hit NOT MATCHED and
# insert). Joining the raw table instead fans out every joined row — URLs get
# scraped twice, the Match queue shows phantom links, counts inflate.
SQL_TRACKED_DEDUPED = """(
    SELECT * FROM `{table}`
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY item_id
        ORDER BY COALESCE(pinned, FALSE) DESC, updated_at DESC) = 1
)"""

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
            # Runtime overrides for the admin console (settings.py). One row per
            # setting key; absence of a row means "use the env-var default".
            f"""CREATE TABLE IF NOT EXISTS `{T_SETTINGS}` (
                setting_key STRING NOT NULL,
                value_json STRING,
                updated_at TIMESTAMP,
                updated_by STRING
            )""",
            # Persistent matrix subscriptions: one row per LS item_matrix_id the
            # user has chosen to track as a unit. Durable, unlike a one-shot
            # pin-matrix — the seed step (seeding.expand_tracked_matrices) re-
            # expands active rows every run so new variants auto-join and removed
            # ones archive. active=FALSE means unsubscribed (its variants are
            # archived). See pi_tracked_products.source='matrix_sub'.
            f"""CREATE TABLE IF NOT EXISTS `{T_TRACKED_MATRICES}` (
                item_matrix_id STRING NOT NULL,
                matrix_description STRING,
                brand STRING,
                source STRING,
                active BOOL,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )""",
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
        _ensure_columns(client, T_URLS, {
            "competitor_sku": "STRING",
            "competitor_variant_id": "STRING",
            "competitor_gtin": "STRING",
            "variant_options_json": "STRING",
        })
        _ensure_columns(client, T_OBSERVATIONS, {
            "extraction_method": "STRING",
            "price_scope": "STRING",
            "variant_id": "STRING",
            "variant_options_json": "STRING",
            "price_low": "FLOAT64",
            "price_high": "FLOAT64",
        })
        _ensure_columns(client, T_LINKS, {
            "variant_id": "STRING",
            "variant_options_json": "STRING",
        })
        _ensure_columns(client, T_RUNS, {"stats_json": "STRING"})
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
_caches = OrderedDict()
_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 500
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        entry = _caches.get(key)
        if not entry:
            return None
        if (time.time() - entry[0]) >= _CACHE_TTL_SECONDS:
            _caches.pop(key, None)
            return None
        _caches.move_to_end(key)
        return entry[1]


def _cache_set(key, value):
    with _cache_lock:
        _caches[key] = (time.time(), value)
        _caches.move_to_end(key)
        while len(_caches) > _CACHE_MAX_ENTRIES:
            _caches.popitem(last=False)


def invalidate_pi_caches():
    with _cache_lock:
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


@contextlib.contextmanager
def _staging_table(table_id: str, rows: list):
    """Loads rows into a uniquely named staging table and yields its id.

    The name must be unique per call: two concurrent writers staging into a
    shared `<table>_temp` WRITE_TRUNCATE each other's rows between the load
    and the MERGE, silently losing one write. The temp load uses the target
    table's schema — autodetect would type ISO timestamp strings as STRING
    and the MERGE insert would then fail against TIMESTAMP columns. The
    staging table is deleted on exit; the 1h expiration backstops a process
    dying mid-MERGE."""
    client = get_bq_client()
    temp_table_id = f"{table_id}_tmp_{uuid.uuid4().hex[:12]}"
    target_schema = client.get_table(table_id).schema
    row_keys = set(rows[0].keys())
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        schema=[f for f in target_schema if f.name in row_keys],
    )
    client.load_table_from_json(rows, temp_table_id, job_config=job_config).result()
    try:
        temp = client.get_table(temp_table_id)
        temp.expires = datetime.now(timezone.utc) + timedelta(hours=1)
        client.update_table(temp, ["expires"])
    except Exception:
        pass  # expiration is a leak backstop only; the finally-delete is primary
    try:
        yield temp_table_id
    finally:
        client.delete_table(temp_table_id, not_found_ok=True)


def _merge_upsert(table_id: str, rows: list, key: str, update_cols: list, insert_cols: list):
    """Generic staging-table MERGE (mirrors upsert_managed_skus)."""
    ensure_pi_tables()
    _json_ready(rows)
    client = get_bq_client()
    set_clause = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
    cols = ", ".join(insert_cols)
    vals = ", ".join(f"S.{c}" for c in insert_cols)
    with _staging_table(table_id, rows) as temp_table_id:
        client.query(f"""
            MERGE `{table_id}` T
            USING `{temp_table_id}` S
            ON T.{key} = S.{key}
            WHEN MATCHED THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})
        """).result()


# ---------------------------------------------------------------------------
# Admin settings (runtime overrides; see settings.py for the registry)
# ---------------------------------------------------------------------------

def get_settings_overrides() -> dict:
    """All stored setting overrides, {key: parsed JSON value}. Cached with the
    standard TTL; writes below invalidate immediately, so the in-process
    scheduler and scrape threads pick up console changes on their next read."""
    ensure_pi_tables()
    cached = _cache_get("pi_settings")
    if cached is not None:
        return cached
    out = {}
    for r in _rows(f"SELECT setting_key, value_json FROM `{T_SETTINGS}`"):
        try:
            out[r["setting_key"]] = json.loads(r["value_json"])
        except (TypeError, ValueError):
            pass  # unreadable row: behave as if the override doesn't exist
    _cache_set("pi_settings", out)
    return out


def upsert_setting(key: str, value, updated_by: str = "Dashboard"):
    row = {
        "setting_key": key,
        "value_json": json.dumps(value),
        "updated_at": utcnow_iso(),
        "updated_by": updated_by,
    }
    _merge_upsert(
        T_SETTINGS, [row], "setting_key",
        update_cols=["value_json", "updated_at", "updated_by"],
        insert_cols=list(row.keys()),
    )
    with _cache_lock:
        _caches.pop("pi_settings", None)


def delete_setting(key: str):
    """Clears an override so the setting reverts to its env-var default."""
    ensure_pi_tables()
    get_bq_client().query(
        f"DELETE FROM `{T_SETTINGS}` WHERE setting_key = @key",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("key", "STRING", key),
        ]),
    ).result()
    with _cache_lock:
        _caches.pop("pi_settings", None)


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
        # Drop the cached competitor list so scrape-health reads see this
        # status now, not after the TTL / end-of-run invalidation.
        with _cache_lock:
            _caches.pop("competitors", None)
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
        LEFT JOIN {SQL_TRACKED_DEDUPED.format(table=T_TRACKED)} t
          ON t.item_id = u.item_id
        ORDER BY u.created_at DESC
    """)
    if not include_disabled:
        rows = [r for r in rows if r.get("enabled")]
    return rows


def update_tracked_url(url_id: str, fields: dict):
    """Updates the mutable fields of a tracked URL (item_id / label / competitor_id)."""
    ensure_pi_tables()
    allowed = {"item_id", "label", "competitor_id", "competitor_sku",
               "competitor_variant_id", "competitor_gtin", "variant_options_json"}
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
        "competitor_sku": data.get("competitor_sku"),
        "competitor_variant_id": data.get("competitor_variant_id"),
        "competitor_gtin": data.get("competitor_gtin"),
        "variant_options_json": data.get("variant_options_json"),
        "enabled": bool(data.get("enabled", True)),
        "created_by": data.get("created_by", "Dashboard"),
        "created_at": data.get("created_at") or now,
        "last_scraped_at": None,
        "last_status": None,
    }
    _merge_upsert(
        T_URLS, [row], "url_id",
        update_cols=["url", "competitor_id", "item_id", "label", "enabled",
                     "competitor_sku", "competitor_variant_id", "competitor_gtin",
                     "variant_options_json"],
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


def dedupe_tracked_products() -> int:
    """Collapse accidental duplicate item_id rows in pi_tracked_products, keeping the
    best row per item (pinned first, then live over archived, then most-recently
    updated). Duplicates arise when two manual-pin MERGEs run concurrently and both
    hit NOT MATCHED before either commits (BigQuery MERGE isn't serializable against
    concurrent DML). Reads already dedupe defensively; this heals the physical table so
    counts and downstream MERGEs stay correct. A COUNT guard keeps the clean case free —
    the table is only rewritten when duplicates actually exist. Returns rows removed."""
    ensure_pi_tables()
    client = get_bq_client()
    rows = list(client.query(
        f"SELECT COUNT(*) - COUNT(DISTINCT item_id) AS extra FROM `{T_TRACKED}`"
    ).result())
    extra = int(rows[0]["extra"] or 0) if rows else 0
    if extra <= 0:
        return 0
    # Single-statement atomic rewrite (table is small and DML-written, no streaming
    # buffer). Keep exactly one row per item_id by the preference ordering above.
    client.query(f"""
        CREATE OR REPLACE TABLE `{T_TRACKED}` AS
        SELECT * EXCEPT(_dedupe_rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY item_id
                ORDER BY COALESCE(pinned, FALSE) DESC,
                         COALESCE(archived, FALSE) ASC,
                         updated_at DESC
            ) AS _dedupe_rn
            FROM `{T_TRACKED}`
        ) WHERE _dedupe_rn = 1
    """).result()
    invalidate_pi_caches()
    print(f"pi: removed {extra} duplicate tracked-product row(s)")
    return extra


def get_tracked_products_with_market(days: int = 7):
    """Tracked products joined with market min/median from the latest observation
    per (competitor, product) over the trailing window.

    Only exact variant/product observations participate. Range observations remain
    available for display and diagnostics but cannot influence decision KPIs."""
    ensure_pi_tables()
    cache_key = f"tracked_market_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    rows = _rows(f"""
        WITH latest AS (
            SELECT o.*
            FROM `{T_OBSERVATIONS}` o
            WHERE observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND match_item_id IS NOT NULL AND price IS NOT NULL
              AND {sql_cad_only("o")}
              AND (COALESCE(price_scope, 'variant') = 'variant'
                   OR (price_scope = 'product' AND NOT EXISTS (
                       SELECT 1 FROM `{T_TRACKED}` mt
                       WHERE mt.item_id = o.match_item_id AND mt.item_matrix_id IS NOT NULL)))
            QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
        ),
        store_rep AS (
            SELECT * FROM latest
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY match_item_id, COALESCE(competitor_id, CONCAT('url:', url))
                ORDER BY IF(COALESCE(in_stock, FALSE), 1, 0) DESC, observed_at DESC, price ASC
            ) = 1
        ),
        market AS (
            SELECT
                match_item_id AS item_id,
                MIN(IF(in_stock, price, NULL)) AS market_min_in_stock,
                MIN(price) AS market_min,
                APPROX_QUANTILES(price, 2)[SAFE_OFFSET(1)] AS market_median,
                COUNT(DISTINCT COALESCE(competitor_id, CONCAT('url:', url))) AS competitor_count,
                ARRAY_AGG(DISTINCT competitor_id IGNORE NULLS) AS competitor_ids,
                MAX(observed_at) AS last_observed_at
            FROM store_rep
            GROUP BY match_item_id
        )
        SELECT t.*, m.market_min_in_stock, m.market_min, m.market_median,
               m.competitor_count, m.competitor_ids, m.last_observed_at
        FROM `{T_TRACKED}` t
        LEFT JOIN market m ON m.item_id = t.item_id
        WHERE COALESCE(t.archived, FALSE) = FALSE
        -- Collapse any accidental duplicate item_id rows (two concurrent manual-pin
        -- MERGEs can both hit NOT MATCHED and insert) to exactly one row per item —
        -- otherwise the UI keys rows by item_id and renders phantom, multiplying rows.
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY t.item_id
            ORDER BY COALESCE(t.pinned, FALSE) DESC, t.updated_at DESC) = 1
        ORDER BY t.revenue_rank
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    _cache_set(cache_key, rows)
    return rows


def get_item_competitor_prices(item_id: str, days: int = 45):
    """One representative price per competitor for an item — the per-store breakdown.
    Exact child observations never propagate to matrix siblings. Range observations
    are returned separately by observation APIs, not this KPI-oriented breakdown."""
    ensure_pi_tables()
    rows = _rows(f"""
        WITH latest AS (
            SELECT o.* FROM `{T_OBSERVATIONS}` o
            WHERE o.match_item_id = @item_id
              AND o.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND o.price IS NOT NULL
              AND {sql_cad_only("o")}
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND NOT EXISTS (
                       SELECT 1 FROM `{T_TRACKED}` mt
                       WHERE mt.item_id = o.match_item_id AND mt.item_matrix_id IS NOT NULL)))
            QUALIFY ROW_NUMBER() OVER (PARTITION BY o.diff_key ORDER BY o.observed_at DESC) = 1
        )
        SELECT l.competitor_id, c.name AS competitor_name, l.source, l.url,
               l.competitor_title, l.price, l.compare_at_price, l.in_stock,
               l.observed_at, l.match_method, l.match_confidence,
               l.price_scope, l.price_low, l.price_high, l.extraction_method,
               l.variant_id, l.variant_options_json
        FROM latest l
        LEFT JOIN `{T_COMPETITORS}` c ON c.competitor_id = l.competitor_id
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])
    from urllib.parse import urlparse
    # group latest-per-listing rows by store, pick the best representative
    stores: dict = {}
    for r in rows:
        key = r.get("competitor_id") or f"url:{r.get('url')}"
        stores.setdefault(key, []).append(r)

    out = []
    for group in stores.values():
        best = sorted(group, key=lambda r: (
            1 if r.get("in_stock") else 0,
            r.get("observed_at") or "",
            -(r.get("price") or 0.0),
        ), reverse=True)[0]
        if not best.get("competitor_name") and best.get("url"):
            best["competitor_name"] = urlparse(best["url"]).netloc
        out.append(best)
    out.sort(key=lambda r: r.get("price") or 0.0)
    return out


# ---------------------------------------------------------------------------
# Matrix subscriptions (persistent "track this matrix" registry)
# ---------------------------------------------------------------------------

def get_tracked_matrices(active_only: bool = False):
    """All matrix subscriptions, newest first. active_only filters to live ones."""
    ensure_pi_tables()
    rows = _rows(f"SELECT * FROM `{T_TRACKED_MATRICES}` ORDER BY updated_at DESC")
    if active_only:
        rows = [r for r in rows if r.get("active")]
    return rows


def get_active_matrix_ids() -> list:
    """item_matrix_ids the user is actively subscribed to (drives the seed sync)."""
    return [str(r["item_matrix_id"]) for r in get_tracked_matrices(active_only=True)]


def upsert_tracked_matrix(matrix_id: str, matrix_description=None, brand=None) -> dict:
    """Subscribe (or re-activate) a matrix. Idempotent on item_matrix_id."""
    now = utcnow_iso()
    row = {
        "item_matrix_id": str(matrix_id),
        "matrix_description": matrix_description,
        "brand": brand,
        "source": "manual",
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    _merge_upsert(
        T_TRACKED_MATRICES, [row], "item_matrix_id",
        update_cols=["matrix_description", "brand", "active", "updated_at"],
        insert_cols=list(row.keys()),
    )
    invalidate_pi_caches()
    return row


def set_tracked_matrix_active(matrix_id: str, active: bool):
    """Toggle a subscription. Deactivating leaves the row for history; the caller
    (router) archives its source='matrix_sub' variants."""
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_TRACKED_MATRICES}` SET active = @active, "
        "updated_at = CURRENT_TIMESTAMP() WHERE item_matrix_id = @mid",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("active", "BOOL", bool(active)),
            bigquery.ScalarQueryParameter("mid", "STRING", str(matrix_id)),
        ]),
    ).result()
    invalidate_pi_caches()


def archive_matrix_sub_variants(matrix_id: str):
    """Archive the auto-added (source='matrix_sub'), unpinned variants of a matrix
    on unsubscribe. Pinned variants and tag-owned rows are left untouched — their
    own tracking signal governs them."""
    ensure_pi_tables()
    get_bq_client().query(
        f"UPDATE `{T_TRACKED}` SET archived = TRUE, updated_at = CURRENT_TIMESTAMP() "
        "WHERE item_matrix_id = @mid AND source = 'matrix_sub' "
        "AND COALESCE(pinned, FALSE) = FALSE AND COALESCE(archived, FALSE) = FALSE",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("mid", "STRING", str(matrix_id)),
        ]),
    ).result()
    invalidate_pi_caches()


def get_tracked_matrices_with_market(days: int = 7):
    """Matrix-grain rollup: one row per tracked matrix with variant counts and the
    market summary aggregated from its variants' *own* exact observations. No cross-
    propagation — a variant with no observations contributes nothing, and no price is
    ever borrowed from a sibling (inherits the price_scope guard of the per-variant
    query). `subscribed` marks matrices with a live pi_tracked_matrices row."""
    ensure_pi_tables()
    cache_key = f"tracked_matrices_market_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    rows = _rows(f"""
        WITH latest AS (
            SELECT o.*
            FROM `{T_OBSERVATIONS}` o
            WHERE observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND match_item_id IS NOT NULL AND price IS NOT NULL
              AND {sql_cad_only("o")}
              AND (COALESCE(price_scope, 'variant') = 'variant'
                   OR (price_scope = 'product' AND NOT EXISTS (
                       SELECT 1 FROM `{T_TRACKED}` mt
                       WHERE mt.item_id = o.match_item_id AND mt.item_matrix_id IS NOT NULL)))
            QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
        ),
        store_rep AS (
            SELECT * FROM latest
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY match_item_id, COALESCE(competitor_id, CONCAT('url:', url))
                ORDER BY IF(COALESCE(in_stock, FALSE), 1, 0) DESC, observed_at DESC, price ASC
            ) = 1
        ),
        mv AS (
            SELECT sr.*, t.item_matrix_id AS mid
            FROM store_rep sr
            JOIN `{T_TRACKED}` t ON t.item_id = sr.match_item_id
            WHERE t.item_matrix_id IS NOT NULL AND COALESCE(t.archived, FALSE) = FALSE
        ),
        mat AS (
            SELECT mid,
                MIN(IF(in_stock, price, NULL)) AS matrix_market_min_in_stock,
                MIN(price) AS matrix_market_min,
                COUNT(DISTINCT COALESCE(competitor_id, CONCAT('url:', url))) AS competitor_count,
                ARRAY_AGG(DISTINCT competitor_id IGNORE NULLS) AS competitor_ids,
                COUNT(DISTINCT match_item_id) AS variants_with_market,
                MAX(observed_at) AS last_observed_at
            FROM mv GROUP BY mid
        ),
        variants AS (
            SELECT item_matrix_id AS mid,
                ANY_VALUE(matrix_description) AS matrix_description,
                ANY_VALUE(brand) AS brand,
                -- DISTINCT so accidental duplicate item_id rows don't inflate the count.
                COUNT(DISTINCT item_id) AS variants_total,
                MIN(current_retail) AS current_retail_min,
                MAX(current_retail) AS current_retail_max,
                MIN(revenue_rank) AS revenue_rank
            FROM `{T_TRACKED}`
            WHERE item_matrix_id IS NOT NULL AND COALESCE(archived, FALSE) = FALSE
            GROUP BY item_matrix_id
        )
        SELECT v.mid AS item_matrix_id, v.matrix_description, v.brand,
               v.variants_total,
               COALESCE(mat.variants_with_market, 0) AS variants_with_market,
               mat.matrix_market_min_in_stock, mat.matrix_market_min,
               v.current_retail_min, v.current_retail_max,
               COALESCE(mat.competitor_count, 0) AS competitor_count,
               mat.competitor_ids, mat.last_observed_at, v.revenue_rank,
               EXISTS(SELECT 1 FROM `{T_TRACKED_MATRICES}` sub
                      WHERE sub.item_matrix_id = v.mid
                        AND COALESCE(sub.active, FALSE)) AS subscribed
        FROM variants v
        LEFT JOIN mat ON mat.mid = v.mid
        ORDER BY v.revenue_rank
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    _cache_set(cache_key, rows)
    return rows


def get_matrix_coverage(matrix_id: str, days: int = 45):
    """Per-competitor coverage for one matrix: how many of our variants each
    competitor carries (has an exact observation for) and how many undercut our
    retail. Read-only over existing observations; same price_scope guard as the
    per-variant breakdown so a parent/product price never counts as coverage."""
    ensure_pi_tables()
    rows = _rows(f"""
        WITH latest AS (
            SELECT o.* FROM `{T_OBSERVATIONS}` o
            JOIN `{T_TRACKED}` t ON t.item_id = o.match_item_id
            WHERE t.item_matrix_id = @mid
              AND o.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
              AND o.price IS NOT NULL
              AND {sql_cad_only("o")}
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND NOT EXISTS (
                       SELECT 1 FROM `{T_TRACKED}` mt
                       WHERE mt.item_id = o.match_item_id AND mt.item_matrix_id IS NOT NULL)))
            QUALIFY ROW_NUMBER() OVER (PARTITION BY o.diff_key ORDER BY o.observed_at DESC) = 1
        ),
        store_rep AS (
            SELECT l.*, t.current_retail
            FROM latest l JOIN `{T_TRACKED}` t ON t.item_id = l.match_item_id
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY l.match_item_id, COALESCE(l.competitor_id, CONCAT('url:', l.url))
                ORDER BY IF(COALESCE(l.in_stock, FALSE), 1, 0) DESC, l.observed_at DESC, l.price ASC
            ) = 1
        )
        SELECT
            COALESCE(sr.competitor_id, CONCAT('url:', sr.url)) AS competitor_key,
            ANY_VALUE(sr.competitor_id) AS competitor_id,
            ANY_VALUE(c.name) AS competitor_name,
            ANY_VALUE(sr.url) AS url,
            COUNT(DISTINCT sr.match_item_id) AS variants_carried,
            COUNT(DISTINCT IF(sr.current_retail IS NOT NULL
                              AND sr.price < sr.current_retail - 0.005,
                              sr.match_item_id, NULL)) AS variants_undercut,
            MIN(sr.price) AS price_min, MAX(sr.price) AS price_max,
            MAX(sr.observed_at) AS last_observed_at
        FROM store_rep sr
        LEFT JOIN `{T_COMPETITORS}` c ON c.competitor_id = sr.competitor_id
        GROUP BY competitor_key
        ORDER BY variants_carried DESC
    """, params=[
        bigquery.ScalarQueryParameter("mid", "STRING", str(matrix_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])
    from urllib.parse import urlparse
    for r in rows:
        if not r.get("competitor_name") and r.get("url"):
            r["competitor_name"] = urlparse(r["url"]).netloc
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


# Columns the picker consumes (order matches the ItemSearchResult shape). `rev` and
# `search_text` exist in the base/index for ORDER BY / WHERE but aren't returned.
_ITEM_SEARCH_COLS = (
    "item_id, title, brand, manufacturer_sku, system_sku, current_retail, "
    "upc_normalized, item_matrix_id, matrix_description, "
    "attribute_1, attribute_2, attribute_3"
)


_TAG_UNSAFE_RE = re.compile(r"[^a-z0-9_-]")


def _search_tag_body() -> str:
    """The configured search tag, sanitized to a bare token safe to embed inside a
    BigQuery raw-string REGEXP_CONTAINS literal (defends against a hostile env value).
    Empty when the filter is disabled (PI_SEARCH_TAG blank)."""
    return _TAG_UNSAFE_RE.sub("", (config.SEARCH_TAG or "").strip().lower())


def _item_search_catalog_query() -> str:
    """One row per active, `add`-tagged catalog item before search-specific fields.

    Membership is filtered by the Lightspeed search tag (PI_SEARCH_TAG, default
    'add'). The tag sits on individual variants — matrix parents are not snapshot
    rows and often aren't tagged — so the filter is per variant AND a matrix stays
    fully searchable whenever any one active variant carries the tag (add_matrices).
    Blank PI_SEARCH_TAG disables the filter entirely (whole catalog).

    The materialized builder and emergency live fallback share only this source join.
    Their term-matching expressions intentionally stay independent so a builder-specific
    SQL regression cannot break the fallback too.
    """
    tag = _search_tag_body()
    if tag:
        has_tag_col = (
            "LOGICAL_OR(REGEXP_CONTAINS(LOWER(COALESCE(item_tags, '')), "
            f"r'(^|,)\\s*{tag}\\s*(,|$)')) AS has_search_tag"
        )
        add_matrices_cte = """,
        add_matrices AS (
            SELECT DISTINCT a.item_matrix_id
            FROM snap s
            JOIN attrs a USING (item_id)
            WHERE s.has_search_tag AND a.item_matrix_id IS NOT NULL
        )"""
        # Keep matrix membership as a join. BigQuery cannot de-correlate the
        # equivalent IN subquery when it appears under the OR below; because this
        # catalog feeds both the materialized index and live fallback, that shape
        # would break both search paths.
        tag_join = (
            "LEFT JOIN add_matrices am "
            "ON am.item_matrix_id = a.item_matrix_id"
        )
        tag_where = (
            "WHERE s.has_search_tag "
            "OR am.item_matrix_id IS NOT NULL"
        )
    else:
        has_tag_col = "FALSE AS has_search_tag"
        add_matrices_cte = ""
        tag_join = ""
        tag_where = ""
    return f"""
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
                MAX(COALESCE(sales_revenue_l90d, 0)) AS rev,
                ANY_VALUE(latest.d) AS source_snapshot_date,
                {has_tag_col}
            FROM `{LS_DATASET}.v_master_snapshot_latest` s CROSS JOIN latest
            WHERE s.snapshot_date_local = latest.d
              AND COALESCE(item_archived, FALSE) = FALSE
            GROUP BY item_id
        ){add_matrices_cte}
        SELECT
            s.item_id, s.title, s.brand, s.manufacturer_sku, s.system_sku,
            s.current_retail,
            NULLIF(LTRIM(REGEXP_REPLACE(COALESCE(a.raw_upc, ''), r'\\D', ''), '0'), '')
                AS upc_normalized,
            a.item_matrix_id, m.matrix_description,
            a.attribute_1, a.attribute_2, a.attribute_3,
            s.rev, s.source_snapshot_date
        FROM snap s
        LEFT JOIN attrs a USING (item_id)
        LEFT JOIN matrix m ON m.matrix_id = a.item_matrix_id
        {tag_join}
        {tag_where}
    """


def _item_search_base_query() -> str:
    """Catalog rows plus a BigQuery-compatible normalized search blob.

    ARRAY_TO_STRING is used instead of CONCAT_WS (which GoogleSQL does not
    implement). Search covers every identity buyers commonly paste into the picker.
    """
    return f"""
        SELECT c.*,
            LOWER(ARRAY_TO_STRING([
                COALESCE(c.title, ''),
                COALESCE(c.brand, ''),
                COALESCE(c.manufacturer_sku, ''),
                COALESCE(c.system_sku, ''),
                COALESCE(c.upc_normalized, ''),
                COALESCE(c.matrix_description, ''),
                COALESCE(c.attribute_1, ''),
                COALESCE(c.attribute_2, ''),
                COALESCE(c.attribute_3, '')
            ], ' ')) AS search_text
        FROM ({_item_search_catalog_query()}) c
    """


_item_search_rebuild_lock = threading.Lock()
_item_search_status_lock = threading.Lock()
_item_search_status = {"status": "idle"}


def _set_item_search_status(**fields):
    with _item_search_status_lock:
        _item_search_status.update(fields)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def item_search_index_status():
    """Current process rebuild state plus authoritative BigQuery table metadata."""
    with _item_search_status_lock:
        status = dict(_item_search_status)
    try:
        table = get_bq_client().get_table(T_ITEM_SEARCH)
    except NotFound:
        status.update({"exists": False, "built_at": None, "row_count": 0, "bytes": 0})
        if status.get("status") not in ("running", "failed"):
            status["status"] = "missing"
        return status
    except Exception as e:
        status["metadata_error"] = str(e)
        return status

    modified = table.modified
    status.update({
        "exists": True,
        "built_at": _iso(modified),
        "row_count": table.num_rows,
        "bytes": table.num_bytes,
    })
    if modified is not None:
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        status["stale"] = (datetime.now(timezone.utc) - modified).total_seconds() >= 20 * 3600
    if status.get("status") in ("idle", "missing"):
        status["status"] = "ready"
    return status


def _item_search_build_query() -> str:
    return f"""
        CREATE OR REPLACE TABLE `{T_ITEM_SEARCH}` AS
        SELECT *, CURRENT_TIMESTAMP() AS built_at
        FROM ({_item_search_base_query()})
    """


def refresh_item_search_index(trigger: str = "unknown"):
    """Rebuild pi_item_search: one row per non-archived catalog item with a prebuilt
    search_text blob. CREATE OR REPLACE is a wholesale, atomic swap — the table never
    accumulates, so it stays flat at ~catalog size regardless of rebuild count. Runs
    nightly with the scrape and on manual reseed (via seeding.refresh_tracked_products),
    never per search. A single-flight lock prevents duplicate 700+ MB rebuilds when
    startup, manual reseed, and the scheduler overlap."""
    if not _item_search_rebuild_lock.acquire(blocking=False):
        status = item_search_index_status()
        status["already_running"] = True
        return status

    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    _set_item_search_status(
        status="running", trigger=trigger, started_at=started_at.isoformat(),
        finished_at=None, error=None,
    )
    try:
        client = get_bq_client()
        job = client.query(_item_search_build_query())
        job.result()
        invalidate_pi_caches()
        table = client.get_table(T_ITEM_SEARCH)
        result = {
            "status": "success",
            "trigger": trigger,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "row_count": table.num_rows,
            "bytes": table.num_bytes,
            "bytes_processed": getattr(job, "total_bytes_processed", None),
            "slot_millis": getattr(job, "slot_millis", None),
            "error": None,
        }
        _set_item_search_status(**result)
        return item_search_index_status()
    except Exception as e:
        _set_item_search_status(
            status="failed", trigger=trigger,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=round(time.monotonic() - started, 3), error=str(e),
        )
        raise
    finally:
        _item_search_rebuild_lock.release()


def item_search_built_at():
    """UTC timestamp of the last index build, or None if the table is absent/empty.
    Drives the startup warm-up's staleness check."""
    try:
        return get_bq_client().get_table(T_ITEM_SEARCH).modified
    except NotFound:
        return None


def _item_search_live_query(limit: int = 40) -> str:
    return f"""
        SELECT {_ITEM_SEARCH_COLS}
        FROM ({_item_search_catalog_query()})
        WHERE STRPOS(LOWER(COALESCE(title, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(brand, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(manufacturer_sku, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(system_sku, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(upc_normalized, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(matrix_description, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(attribute_1, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(attribute_2, '')), @term) > 0
           OR STRPOS(LOWER(COALESCE(attribute_3, '')), @term) > 0
           OR item_id = @exact
        ORDER BY rev DESC, matrix_description, attribute_1
        LIMIT {int(limit)}
    """


def _item_search_fast_query(limit: int = 40) -> str:
    return f"""
        SELECT {_ITEM_SEARCH_COLS}
        FROM `{T_ITEM_SEARCH}`
        WHERE STRPOS(search_text, @term) > 0 OR item_id = @exact
        ORDER BY rev DESC, matrix_description, attribute_1
        LIMIT {int(limit)}
    """


def _search_snapshot_items_live(q: str, limit: int = 40):
    """Independent emergency search used while the materialized index is absent.

    This deliberately filters the raw catalog columns and never consumes the
    builder's search_text expression, preserving a real fallback boundary.
    """
    term = q.strip().lower()
    return _rows(_item_search_live_query(limit), params=[
        bigquery.ScalarQueryParameter("term", "STRING", term),
        bigquery.ScalarQueryParameter("exact", "STRING", q.strip()),
    ])


def search_snapshot_items(q: str, limit: int = 40):
    """Item search for the pin picker. Fast path: a single scan of the prebuilt
    pi_item_search index (refreshed nightly + on reseed). Falls back to the live
    snapshot join only until that index exists, so search never breaks. A short
    per-query TTL cache makes repeat searches (reopening the picker) free."""
    term = q.strip().lower()
    cache_key = f"item_search_{term}_{int(limit)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    params = [
        bigquery.ScalarQueryParameter("term", "STRING", term),
        bigquery.ScalarQueryParameter("exact", "STRING", q.strip()),
    ]
    try:
        rows = _rows(_item_search_fast_query(limit), params=params)
    except NotFound:
        # Index not built yet (first boot before warm-up/reseed) — serve the live
        # query so search works, slowly, until the index lands.
        rows = _search_snapshot_items_live(q, limit)
    _cache_set(cache_key, rows)
    return rows


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
          AND COALESCE(price_scope, 'variant') IN ('variant', 'product')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY diff_key ORDER BY observed_at DESC) = 1
    """, params=[bigquery.ScalarQueryParameter("days", "INT64", days)])
    return {r["diff_key"]: r for r in rows if r.get("diff_key")}


def get_item_observations(item_id: str, days: int = 120):
    return _rows(f"""
        SELECT observed_at, competitor_id, url, price, compare_at_price, in_stock,
               match_method, match_confidence, competitor_title, extraction_method,
               price_scope, price_low, price_high, variant_id, variant_options_json
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

    Competitor series are item-specific; child observations never bleed across a
    matrix. Ambiguous/range observations are excluded from the chart.

    Each series also includes one baseline point — its latest change before the
    window (lookback capped at 365 days) — so the client can carry the price
    forward from the window's left edge instead of starting the line blank."""
    ensure_pi_tables()
    ours = _rows(f"""
        SELECT observed_at, price FROM (
            SELECT observed_at, price,
                   LAG(price) OVER (ORDER BY observed_at) AS prev_price,
                   MAX(observed_at) OVER () AS last_at,
                   observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
                       AS in_window,
                   ROW_NUMBER() OVER (
                       PARTITION BY observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
                       ORDER BY observed_at DESC) AS rn
            FROM `{T_OUR_PRICE_HISTORY}`
            WHERE item_id = @item_id
              AND observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 365 DAY)
              AND price IS NOT NULL
        )
        WHERE (in_window AND (prev_price IS NULL OR ABS(price - prev_price) > 0.005
                              OR observed_at = last_at))
           OR (NOT in_window AND rn = 1)
        ORDER BY observed_at
    """, params=[
        bigquery.ScalarQueryParameter("item_id", "STRING", str(item_id)),
        bigquery.ScalarQueryParameter("days", "INT64", days),
    ])

    comp_rows = _rows(f"""
        WITH obs AS (
            SELECT
                COALESCE(o.competitor_id, CONCAT('url:', o.url)) AS series_key,
                o.competitor_id, o.url, o.run_id, o.observed_at, o.price, o.in_stock
            FROM `{T_OBSERVATIONS}` o
            WHERE o.match_item_id = @item_id
              AND o.observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 365 DAY)
              AND o.price IS NOT NULL
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND NOT EXISTS (
                       SELECT 1 FROM `{T_TRACKED}` mt
                       WHERE mt.item_id = o.match_item_id AND mt.item_matrix_id IS NOT NULL)))
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
                   MAX(observed_at) OVER (PARTITION BY series_key) AS last_at,
                   observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
                       AS in_window,
                   ROW_NUMBER() OVER (
                       PARTITION BY series_key,
                           observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
                       ORDER BY observed_at DESC) AS rn
            FROM per_run
        )
        SELECT c.series_key, c.competitor_id, comp.name AS competitor_name, c.url,
               c.observed_at, c.price, c.in_stock
        FROM changed c
        LEFT JOIN `{T_COMPETITORS}` comp ON comp.competitor_id = c.competitor_id
        WHERE (c.in_window AND (c.prev_price IS NULL OR ABS(c.price - c.prev_price) > 0.005
                                OR c.observed_at = c.last_at))
           OR (NOT c.in_window AND c.rn = 1)
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
        LEFT JOIN {SQL_TRACKED_DEDUPED.format(table=T_TRACKED)} t
          ON t.item_id = l.item_id
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
    """Reject one competitor listing for one item — the "Ban" escape hatch on the
    tracked-products breakdown. A listing can reach an item through three separate
    surfaces, and a durable ban has to close all of them or the next scrape re-adds
    it:
      1. a confirmed/pending link row (manual_url pin keyed url:{url}, or a
         gtin/attr/llm link keyed {cid}:{sku}) -> the targeted-link loop re-attaches
         it every run;
      2. a pi_tracked_urls pin -> the tracked-URL phase re-scrapes it every run;
      3. no link row at all (a fuzzy/catalog auto-match) -> the matcher re-matches
         it every run unless its match_key is tombstoned.
    We key steps 1 & 2 on (item, url) rather than competitor: a URL is store-unique,
    so this also works when the listing was pinned under the wrong competitor (the
    exact case the old SKU-key-only reject silently failed on — it rebuilt a
    {cid}:{sku} key that never matched a url:{url} pin, and never touched the
    tracked-URL row). Then nulls match_item_id on the listing's observations so it
    drops from the market/history immediately."""
    from .matcher import build_match_key
    ensure_pi_tables()
    iid = str(item_id)
    cid = str(competitor_id) if competitor_id else None
    url = url or ""
    client = get_bq_client()

    def _exec(sql, params):
        job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
        job.result()
        return job.num_dml_affected_rows or 0

    # Freshest observation for this listing (item + url), used to rebuild the
    # matcher's match_key. Not filtered on competitor — a URL belongs to one store,
    # so bans of a mislabeled-competitor listing must still find it.
    obs = _rows(f"""
        SELECT competitor_id, competitor_sku, url, competitor_title
        FROM `{T_OBSERVATIONS}`
        WHERE match_item_id = @iid AND COALESCE(url, '') = @url
        ORDER BY observed_at DESC LIMIT 1
    """, params=[
        bigquery.ScalarQueryParameter("iid", "STRING", iid),
        bigquery.ScalarQueryParameter("url", "STRING", url),
    ])
    if not obs:
        return {"status": "error", "reason": "no observation found for that listing"}
    o = obs[0]

    id_url_params = [
        bigquery.ScalarQueryParameter("iid", "STRING", iid),
        bigquery.ScalarQueryParameter("url", "STRING", url),
        bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
    ]

    # 1) Reject any confirmed/pending link at this URL (any match_key scheme).
    links_rejected = 0
    if url:
        links_rejected = _exec(
            f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
            "updated_at = CURRENT_TIMESTAMP() "
            "WHERE item_id = @iid AND COALESCE(competitor_url, '') = @url "
            "AND status IN ('confirmed', 'pending')", id_url_params)

    # 2) Disable any tracked-URL pin at this URL so the URL phase stops re-scraping.
    urls_disabled = 0
    if url:
        urls_disabled = _exec(
            f"UPDATE `{T_URLS}` SET enabled = FALSE "
            "WHERE item_id = @iid AND url = @url AND COALESCE(enabled, TRUE) = TRUE",
            [bigquery.ScalarQueryParameter("iid", "STRING", iid),
             bigquery.ScalarQueryParameter("url", "STRING", url)])

    # 3) Tombstone the matcher's match_key (the no-link fuzzy/catalog path). Rebuild
    #    exactly what the scraper writes (SKU-preferred) so rejected_keys blocks it.
    key_cid = o.get("competitor_id") or cid or ""
    match_key = build_match_key(key_cid, {
        "sku": o.get("competitor_sku"), "url": o.get("url"),
        "title": o.get("competitor_title"),
    })
    existing = _rows(f"SELECT status FROM `{T_LINKS}` WHERE match_key = @mk LIMIT 1",
                     params=[bigquery.ScalarQueryParameter("mk", "STRING", match_key)])
    if existing:
        _exec(f"UPDATE `{T_LINKS}` SET status = 'rejected', decided_by = @actor, "
              "updated_at = CURRENT_TIMESTAMP() WHERE match_key = @mk AND status != 'rejected'",
              [bigquery.ScalarQueryParameter("mk", "STRING", match_key),
               bigquery.ScalarQueryParameter("actor", "STRING", decided_by)])
    else:
        now = utcnow_iso()
        insert_product_links([{
            "link_id": str(uuid.uuid4()), "item_id": iid,
            "competitor_id": key_cid or cid, "match_key": match_key,
            "competitor_url": o.get("url"), "competitor_sku": o.get("competitor_sku"),
            "competitor_title": o.get("competitor_title"), "gtin": None,
            "level": "variant", "status": "rejected", "source": "human",
            "confidence": None, "fuzzy_score": None, "llm_verdict": None,
            "llm_reason": "rejected from tracked-products breakdown", "our_price": None,
            "their_price": None, "decided_by": decided_by,
            "created_at": now, "updated_at": now,
        }])

    # 4) Drop the listing from market/history now (all observations at this URL).
    obs_cleared = _exec(
        f"UPDATE `{T_OBSERVATIONS}` SET match_item_id = NULL "
        "WHERE match_item_id = @iid AND COALESCE(url, '') = @url",
        [bigquery.ScalarQueryParameter("iid", "STRING", iid),
         bigquery.ScalarQueryParameter("url", "STRING", url)])

    invalidate_pi_caches()
    return {"status": "rejected", "match_key": match_key,
            "links_rejected": links_rejected, "tracked_urls_disabled": urls_disabled,
            "observations_cleared": obs_cleared}


def insert_product_links(rows: list):
    """Insert-only MERGE on match_key: existing rows (including tombstones and
    human decisions) are never touched."""
    if not rows:
        return
    ensure_pi_tables()
    _json_ready(rows)
    client = get_bq_client()
    cols = ", ".join(rows[0].keys())
    vals = ", ".join(f"S.{c}" for c in rows[0].keys())
    with _staging_table(T_LINKS, rows) as temp_table_id:
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
                     "competitor_title", "gtin", "variant_id", "variant_options_json",
                     "level", "status", "source", "confidence",
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
    set_clause = ", ".join(f"T.{c} = S.{c}" for c in rows[0].keys() if c != "link_id")
    with _staging_table(T_LINKS, rows) as temp_table_id:
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


def _update_link_decisions(confirmed_ids: list, rejected_ids: list,
                           decided_by: str) -> None:
    """Apply any number of human link decisions with one BigQuery DML statement.

    The Match tab can select hundreds of rows. Issuing one UPDATE per row in
    parallel exceeds BigQuery's per-table outstanding-DML and mutation-rate
    limits, and concurrent updates can also fail serialization. Array parameters
    keep the whole decision set in a single atomic table mutation.
    """
    confirmed_ids = [str(link_id) for link_id in confirmed_ids]
    rejected_ids = [str(link_id) for link_id in rejected_ids]
    decided_ids = confirmed_ids + rejected_ids
    if not decided_ids:
        return
    get_bq_client().query(
        f"""
        UPDATE `{T_LINKS}` SET
            status = IF(link_id IN UNNEST(@confirmed_ids), 'confirmed', 'rejected'),
            decided_by = @actor,
            confidence = IF(link_id IN UNNEST(@confirmed_ids), 1.0, confidence),
            updated_at = CURRENT_TIMESTAMP()
        WHERE link_id IN UNNEST(@decided_ids)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("confirmed_ids", "STRING", confirmed_ids),
            bigquery.ArrayQueryParameter("decided_ids", "STRING", decided_ids),
            bigquery.ScalarQueryParameter("actor", "STRING", decided_by),
        ]),
    ).result()
    invalidate_pi_caches()


def decide_links_bulk(link_ids: list, status: str,
                      decided_by: str = "Dashboard") -> dict:
    """Guard and persist a Match-tab selection without concurrent per-row DML.

    Results remain aligned with the caller's de-duplicated input order. Confirm
    decisions preserve the same variant-conflict and one-link-per-item/store
    rules as ``confirm_link``; later selected candidates for an already-occupied
    item/store pair are skipped. Reject decisions need no per-row guard.
    """
    from .matcher import parse_variant_options, attributes_conflict

    ensure_pi_tables()
    ids = list(dict.fromkeys(str(link_id) for link_id in link_ids if link_id))
    if not ids:
        return {"results": [], "confirmed_link_ids": []}
    if len(ids) > 500:
        raise ValueError("A maximum of 500 link decisions can be applied at once")
    if status not in ("confirmed", "rejected"):
        raise ValueError("status must be confirmed or rejected")

    if status == "rejected":
        _update_link_decisions([], ids, decided_by)
        return {
            "results": [{"link_id": link_id, "status": "rejected"} for link_id in ids],
            "confirmed_link_ids": [],
        }

    selected_rows = _rows(f"""
        SELECT l.link_id, l.item_id, l.competitor_id, l.competitor_title, l.status,
               t.attribute_1 AS a1, t.attribute_2 AS a2, t.attribute_3 AS a3
        FROM `{T_LINKS}` l
        LEFT JOIN `{T_TRACKED}` t ON t.item_id = l.item_id
        WHERE l.link_id IN UNNEST(@ids)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY l.link_id ORDER BY t.updated_at DESC
        ) = 1
    """, params=[bigquery.ArrayQueryParameter("ids", "STRING", ids)])
    selected_by_id = {}
    for row in selected_rows:
        selected_by_id.setdefault(str(row["link_id"]), row)

    item_ids = list(dict.fromkeys(
        str(row["item_id"]) for row in selected_rows if row.get("item_id") is not None
    ))
    existing_rows = _rows(f"""
        SELECT link_id, item_id, COALESCE(competitor_id, '') AS competitor_id
        FROM `{T_LINKS}`
        WHERE status = 'confirmed' AND item_id IN UNNEST(@item_ids)
    """, params=[bigquery.ArrayQueryParameter("item_ids", "STRING", item_ids)]) \
        if item_ids else []
    occupied = {}
    for row in existing_rows:
        pair = (str(row["item_id"]), str(row.get("competitor_id") or ""))
        occupied.setdefault(pair, set()).add(str(row["link_id"]))

    confirmed_ids, rejected_ids, results = [], [], []
    for link_id in ids:
        row = selected_by_id.get(link_id)
        if row is None:
            results.append({"link_id": link_id, "status": "error",
                            "reason": "link not found"})
            continue
        pair = (str(row.get("item_id") or ""),
                str(row.get("competitor_id") or ""))
        if row.get("status") == "confirmed":
            occupied.setdefault(pair, set()).add(link_id)
            results.append({"link_id": link_id, "status": "confirmed",
                            "reason": "already confirmed"})
            continue
        if attributes_conflict(parse_variant_options(row.get("competitor_title")),
                               [row.get("a1"), row.get("a2"), row.get("a3")]):
            rejected_ids.append(link_id)
            results.append({"link_id": link_id, "status": "rejected",
                            "reason": "color/size mismatch with the item"})
            continue
        other_confirmed = occupied.get(pair, set()) - {link_id}
        if other_confirmed:
            results.append({
                "link_id": link_id, "status": "skipped", "can_replace": True,
                "reason": "item already has a confirmed link at this store",
            })
            continue
        confirmed_ids.append(link_id)
        occupied.setdefault(pair, set()).add(link_id)
        results.append({"link_id": link_id, "status": "confirmed", "replaced": 0})

    _update_link_decisions(confirmed_ids, rejected_ids, decided_by)
    return {"results": results, "confirmed_link_ids": confirmed_ids}


def confirm_link(link_id: str, decided_by: str = "Dashboard", replace: bool = False) -> dict:
    """Guarded single-row/override confirm. Bulk decisions mirror these rules in
    ``decide_links_bulk``. Enforces the invariant that one of our items links to
    exactly one competitor variant per store:
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


def fetch_and_record_link(link_id: str):
    """Immediately fetch a just-confirmed link's URL and record an observation, so
    a match confirmed in the Matching tab shows in Tracked Products within seconds
    instead of waiting for the next scrape. Mirrors the nightly targeted-link path
    (diff_key = link:{id}, source='link'), incl. Shopify variant resolution by SKU."""
    from .connectors import PageScraper
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT link_id, item_id, competitor_id, competitor_url, competitor_sku,
               competitor_title, gtin, variant_id, variant_options_json, confidence
        FROM `{T_LINKS}` WHERE link_id = @lid AND status = 'confirmed'
    """, params=[bigquery.ScalarQueryParameter("lid", "STRING", str(link_id))])
    if not rows:
        return
    l = rows[0]
    url, item_id = l.get("competitor_url"), l.get("item_id")
    if not url or not item_id:
        return
    try:
        parsed = PageScraper().fetch(
            url, sku=l.get("competitor_sku"), gtin=l.get("gtin"),
            variant_id=l.get("variant_id"),
            variant_options=json.loads(l.get("variant_options_json") or "[]"),
        )
        if parsed and parsed.get("price") is not None:
            if parsed.get("_matched_by") in ("sku", "gtin", "variant_id", "variant_options"):
                parsed["price_scope"] = "variant"
            load_rows(T_OBSERVATIONS, [{
                "observed_at": utcnow_iso(), "run_id": f"confirm-{uuid.uuid4()}",
                "source": "link", "diff_key": f"link:{l['link_id']}",
                "competitor_id": l.get("competitor_id"), "url": url,
                "competitor_title": parsed.get("title") or l.get("competitor_title"),
                "competitor_sku": parsed.get("sku") or l.get("competitor_sku"),
                "gtin": parsed.get("gtin") or l.get("gtin"),
                "match_item_id": str(item_id), "match_method": "link",
                "match_confidence": l.get("confidence") or 1.0,
                "price": parsed.get("price"), "compare_at_price": parsed.get("compare_at_price"),
                "currency": parsed.get("currency"), "in_stock": parsed.get("in_stock"),
                "extraction_method": parsed.get("extraction_method"),
                "price_scope": parsed.get("price_scope") or "variant",
                "variant_id": parsed.get("variant_id"),
                "variant_options_json": json.dumps(parsed.get("variant_options") or []),
                "price_low": parsed.get("price_low"), "price_high": parsed.get("price_high"),
            }])
            invalidate_pi_caches()
    except Exception as e:
        print(f"pi: immediate fetch of confirmed link {link_id} failed (next scrape retries): {e}")


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
        LEFT JOIN {SQL_TRACKED_DEDUPED.format(table=T_TRACKED)} t
          ON t.item_id = l.item_id
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


def _scrape_status_bucket(status: str) -> str:
    """Collapse a competitor's last_scrape_status into a health bucket."""
    s = (status or "").lower()
    if not s:
        return "unknown"
    if s.startswith("failed") or "error" in s:
        return "failed"
    if s == "skipped_no_connector":
        return "skipped"
    if s == "success_no_products":
        return "empty"       # connector returned nothing (bad sitemap / blocked)
    if s == "success_no_matches":
        return "no_matches"  # products found, none in a tracked brand
    if s.startswith("success"):
        return "ok"
    return "unknown"


def get_scrape_health() -> dict:
    """Compact health overview of the most recent scrape for the UI: the last run
    row plus a per-competitor breakdown bucketed by last_scrape_status, and an
    overall rollup. Enabled competitors only (disabled ones aren't scraped)."""
    runs = get_scrape_runs(limit=1)
    run = runs[0] if runs else None
    competitors = [c for c in get_competitors() if c.get("enabled")]
    per, counts = [], {}
    for c in competitors:
        bucket = _scrape_status_bucket(c.get("last_scrape_status"))
        counts[bucket] = counts.get(bucket, 0) + 1
        per.append({
            "competitor_id": c.get("competitor_id"),
            "name": c.get("name"),
            "connector_type": c.get("connector_type"),
            "status": c.get("last_scrape_status"),
            "bucket": bucket,
            "last_scraped_at": c.get("last_scraped_at"),
        })
    per.sort(key=lambda p: ({"failed": 0, "empty": 1, "no_matches": 2,
                             "skipped": 3, "unknown": 4, "ok": 5}.get(p["bucket"], 6),
                            p["name"] or ""))
    run_status = (run or {}).get("status")
    if run_status == "running":
        overall = "running"
    elif counts.get("failed") or run_status == "failed":
        overall = "failed"
    elif run_status == "partial" or counts.get("empty"):
        overall = "degraded"
    elif run_status in ("success", "partial") or counts.get("ok"):
        overall = "healthy"
    else:
        overall = run_status or "never"
    return {
        "overall": overall,
        "last_run": run,
        "counts": {"total": len(per), "ok": counts.get("ok", 0),
                   "empty": counts.get("empty", 0), "no_matches": counts.get("no_matches", 0),
                   "failed": counts.get("failed", 0), "skipped": counts.get("skipped", 0)},
        "competitors": per,
    }


def has_scheduler_blocking_run_on(local_date_str: str, timezone_name: str) -> bool:
    """True if the scheduler should not fire on the given local date: any
    success/partial run (manual or scheduled) already covered the day, or a
    scheduled attempt already ran — including a *failed* one. Counting failed
    scheduled attempts matters: the scheduler's `due` window spans the rest of
    the day, so without it a systemic failure re-fires a full run every tick."""
    ensure_pi_tables()
    rows = _rows(f"""
        SELECT COUNT(*) AS n FROM `{T_RUNS}`
        WHERE (status IN ('success', 'partial') OR trigger = 'scheduled')
          AND FORMAT_DATE('%Y-%m-%d', DATE(started_at, @tz)) = @d
    """, params=[
        bigquery.ScalarQueryParameter("tz", "STRING", timezone_name),
        bigquery.ScalarQueryParameter("d", "STRING", local_date_str),
    ])
    return bool(rows and rows[0]["n"] > 0)
