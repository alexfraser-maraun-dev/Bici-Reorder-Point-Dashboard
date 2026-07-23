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
from . import config, repository, settings

_anthropic_client = None

_TARGET_PCT = int(config.DIGEST_UNDERCUT_TARGET_PCT)

DEFAULT_SYSTEM_PROMPT = (
    "You are a pricing analyst for Bici, a bicycle retailer in British Columbia. "
    "Given last night's competitor price data as JSON, write a concise markdown "
    "digest with these sections: (1) a one-line market position summary, "
    "(2) notable competitor moves, (3) 3-5 suggested actions with item names and "
    "concrete price points, (4) anything needing attention (failed scrapes, "
    "low-confidence matches, competitor MAP violations).\n\n"
    "Pricing policy — apply it to every recommendation:\n"
    "- When a COMPETITOR is advertising below MAP (map_violation events), never "
    "recommend that we drop our price to match — that would put us in violation "
    "too. The remedy is to flag the violation with the relevant vendor/brand, so "
    "surface those under 'needs attention' and frame the action as a vendor flag, "
    "not a price cut.\n"
    "- We price to win volume while protecting margin. Being cheaper than the "
    "market wins volume, but margin matters more than being lowest: do NOT "
    f"recommend pricing more than ~{_TARGET_PCT}% below the next-lowest in-stock "
    "competitor. Aim to sit just under the next-lowest price, not far under it.\n"
    "- Where we are already far below the market (see most_below_market), the "
    f"opportunity is to RAISE toward the ~{_TARGET_PCT}%-under target and recover "
    "margin — not to cut further.\n"
    "- Cost & margin: items in biggest_gaps_above_market and most_below_market "
    "carry our_cost, margin_pct (gross margin at our current price), and "
    "floor_price (the lowest price our guardrails allow — it already accounts for "
    "MAP, manual overrides, and our minimum margin over cost). NEVER recommend a "
    "price at or below floor_price, and state the resulting gross margin % in each "
    "price suggestion so the volume-vs-margin tradeoff is explicit.\n\n"
    "Under 350 words. Prices are CAD. Do not invent data — only reference what is "
    "in the JSON."
)


def system_prompt() -> str:
    """Effective digest prompt: the admin-console override when one is saved,
    otherwise the default above. A blank override also means default."""
    custom = (settings.get("digest_prompt") or "").strip()
    return custom or DEFAULT_SYSTEM_PROMPT


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
              AND {repository.sql_cad_only("o")}
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND t.item_matrix_id IS NULL))
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
                             'undercut')
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
              AND {repository.sql_cad_only("o")}
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND t.item_matrix_id IS NULL))
            GROUP BY o.match_item_id
        )
        SELECT match_item_id AS item_id, title AS item, our_price, market_min,
               ROUND(our_price - market_min, 2) AS gap
        FROM matched
        WHERE market_min IS NOT NULL AND our_price > market_min + 0.01
        ORDER BY gap DESC
        LIMIT 10
    """)

    # Items where we sit well UNDER the market min — margin left on the table.
    # Feeds the "raise toward the target, don't cut further" digest guidance.
    below = rows(f"""
        WITH matched AS (
            SELECT o.match_item_id,
                   MIN(IF(o.in_stock, o.price, NULL)) AS market_min,
                   ANY_VALUE(t.current_retail) AS our_price,
                   ANY_VALUE(t.title) AS title
            FROM `{repository.T_OBSERVATIONS}` o
            JOIN `{repository.T_TRACKED}` t ON t.item_id = o.match_item_id
            WHERE o.run_id = @run_id AND o.price IS NOT NULL
              AND {repository.sql_cad_only("o")}
              AND (COALESCE(o.price_scope, 'variant') = 'variant'
                   OR (o.price_scope = 'product' AND t.item_matrix_id IS NULL))
            GROUP BY o.match_item_id
        )
        SELECT match_item_id AS item_id, title AS item, our_price, market_min,
               ROUND(market_min - our_price, 2) AS below_by,
               ROUND((market_min - our_price) / market_min * 100, 1) AS below_by_pct
        FROM matched
        WHERE market_min IS NOT NULL AND our_price < market_min - 0.01
        ORDER BY below_by DESC
        LIMIT 10
    """)

    run = rows(f"SELECT * FROM `{repository.T_RUNS}` WHERE run_id = @run_id LIMIT 1")

    # Attach cost/margin/floor to the per-item lists that drive raise/cut actions,
    # reusing the same guardrail floor the price-push enforces so the digest never
    # suggests a price a push would reject.
    tracked_by_id = {str(t["item_id"]): t for t in repository.get_tracked_products(include_archived=True)}
    _enrich_margin(gaps, tracked_by_id)
    _enrich_margin(below, tracked_by_id)

    def clean(rs):
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
                for r in rs]

    return {
        "run_id": run_id,
        "position": clean(position)[0] if position else {},
        "notable_changes": clean(changes),
        "biggest_gaps_above_market": clean(gaps),
        "most_below_market": clean(below),
        "pricing_guardrails": {
            "margin_floor_pct": round(config.MARGIN_FLOOR_PCT * 100, 1),
            "undercut_target_pct": config.DIGEST_UNDERCUT_TARGET_PCT,
            "note": "floor_price already bakes in the margin floor / MAP / overrides; "
                    "never recommend below it. margin_pct is gross margin at our current price.",
        },
        "scrape_health": clean(run)[0] if run else {},
    }


def _enrich_margin(rows_list, tracked_by_id):
    """Adds our_cost, margin_pct (gross, at current retail), and floor_price
    (pricing.compute_floor — the authoritative push guardrail) to each row that
    resolves to a tracked item. Leaves fields absent when cost is unknown."""
    from . import pricing
    for r in rows_list:
        item = tracked_by_id.get(str(r.get("item_id")))
        if not item:
            continue
        cost = item.get("current_cost")
        our = r.get("our_price")
        if cost:
            r["our_cost"] = round(float(cost), 2)
            if our and float(our) > 0:
                r["margin_pct"] = round((float(our) - float(cost)) / float(our) * 100, 1)
        floor, floor_source = pricing.compute_floor(item)
        if floor is not None:
            r["floor_price"] = floor
            r["floor_source"] = floor_source


def generate_digest(run_id: str) -> dict:
    if not config.ANTHROPIC_API_KEY:
        print("pi: ANTHROPIC_API_KEY not set; skipping digest")
        return None
    stats = build_digest_stats(run_id)
    client = _get_anthropic_client()
    message = client.messages.create(
        model=config.DIGEST_MODEL,
        max_tokens=config.DIGEST_MAX_TOKENS,
        system=system_prompt(),
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
