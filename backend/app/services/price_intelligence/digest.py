"""Post-scrape LLM market digest (feature e).

Runs once per scrape (or on manual regenerate), never per page request; the
rendered markdown is stored in pi_digests and served from there. Failures here
are always swallowed by the caller — scrape data is never lost because the
digest didn't generate. Cost at nightly volume is fractions of a cent per run.
"""
import json
import uuid

from google.cloud import bigquery

from app.services.bigquery_sync import get_bq_client
from . import config, repository

_anthropic_client = None

SYSTEM_PROMPT = (
    "You are a pricing analyst for Bici, a bicycle retailer in British Columbia. "
    "Given last night's competitor price data as JSON, write a concise markdown "
    "digest with these sections: (1) a one-line market position summary, "
    "(2) notable competitor moves, (3) 3-5 suggested actions with item names and "
    "concrete price points, (4) anything needing attention (failed scrapes, "
    "low-confidence matches, MAP violations by competitors). Under 350 words. "
    "Prices are CAD. Do not invent data — only reference what is in the JSON."
)


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def build_digest_stats(run_id: str) -> dict:
    """Compact stats over the run's observations — a few KB of JSON, not raw rows."""
    client = get_bq_client()
    params = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]

    def rows(query):
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        return [dict(r) for r in client.query(query, job_config=job_config).result()]

    position = rows(f"""
        WITH matched AS (
            SELECT o.match_item_id,
                   MIN(IF(o.in_stock, o.price, NULL)) AS market_min,
                   ANY_VALUE(t.current_retail) AS our_price
            FROM `{repository.T_OBSERVATIONS}` o
            JOIN `{repository.T_TRACKED}` t ON t.item_id = o.match_item_id
            WHERE o.run_id = @run_id AND o.price IS NOT NULL
            GROUP BY o.match_item_id
        )
        SELECT
            COUNT(*) AS matched_items,
            ROUND(AVG(SAFE_DIVIDE(our_price, market_min)), 3) AS price_index_vs_market_min,
            COUNTIF(our_price < market_min - 0.01) AS cheaper,
            COUNTIF(ABS(our_price - market_min) <= 0.01) AS parity,
            COUNTIF(our_price > market_min + 0.01) AS pricier
        FROM matched WHERE market_min IS NOT NULL AND our_price IS NOT NULL
    """)

    changes = rows(f"""
        SELECT event_type, item_title AS item, competitor_name AS competitor,
               old_price, new_price, pct_change
        FROM `{repository.T_EVENTS}`
        WHERE run_id = @run_id
          AND event_type IN ('price_drop', 'price_increase', 'map_violation',
                             'undercut', 'back_in_stock', 'out_of_stock')
        ORDER BY ABS(COALESCE(pct_change, 0)) DESC
        LIMIT 15
    """)

    gaps = rows(f"""
        WITH matched AS (
            SELECT o.match_item_id,
                   MIN(IF(o.in_stock, o.price, NULL)) AS market_min,
                   ANY_VALUE(t.current_retail) AS our_price,
                   ANY_VALUE(t.title) AS title
            FROM `{repository.T_OBSERVATIONS}` o
            JOIN `{repository.T_TRACKED}` t ON t.item_id = o.match_item_id
            WHERE o.run_id = @run_id AND o.price IS NOT NULL
            GROUP BY o.match_item_id
        )
        SELECT title AS item, our_price, market_min,
               ROUND(our_price - market_min, 2) AS gap
        FROM matched
        WHERE market_min IS NOT NULL AND our_price > market_min + 0.01
        ORDER BY gap DESC
        LIMIT 10
    """)

    run = rows(f"SELECT * FROM `{repository.T_RUNS}` WHERE run_id = @run_id LIMIT 1")

    def clean(rs):
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
                for r in rs]

    return {
        "run_id": run_id,
        "position": clean(position)[0] if position else {},
        "notable_changes": clean(changes),
        "biggest_gaps_above_market": clean(gaps),
        "scrape_health": clean(run)[0] if run else {},
    }


def generate_digest(run_id: str) -> dict:
    if not config.ANTHROPIC_API_KEY:
        print("pi: ANTHROPIC_API_KEY not set; skipping digest")
        return None
    stats = build_digest_stats(run_id)
    client = _get_anthropic_client()
    message = client.messages.create(
        model=config.DIGEST_MODEL,
        max_tokens=config.DIGEST_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(stats, default=str)}],
    )
    digest_md = "".join(block.text for block in message.content if block.type == "text")
    row = {
        "digest_id": str(uuid.uuid4()),
        "created_at": repository.utcnow_iso(),
        "run_id": run_id,
        "model": config.DIGEST_MODEL,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "digest_md": digest_md,
        "stats_json": json.dumps(stats, default=str),
    }
    repository.save_digest(row)
    return row
