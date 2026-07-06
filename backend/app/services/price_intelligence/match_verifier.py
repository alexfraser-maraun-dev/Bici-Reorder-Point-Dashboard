"""LLM verification of pending product-link candidates.

After each scrape run, near-miss fuzzy candidates (pi_product_links rows with
status='pending', no verdict yet) are batched to a small model that decides
whether each competitor listing is the same product as our catalog item.
Verdicts write back to the links table:
  same_variant -> confirmed (variant, 0.95)
  same_model   -> confirmed (model, 0.85) when the target variant is unambiguous
                  (single tracked variant of the matrix, or the extracted size
                  matches one variant's attributes); otherwise stays pending for
                  human review with the verdict attached
  different    -> rejected (tombstone: the match_key is never re-proposed)
  uncertain    -> stays pending for human review

Mirrors digest.py: lazy client, ANTHROPIC_API_KEY guard, best-effort — the
caller swallows failures so scrape data is never lost.
"""
import json
import re

from . import config, repository

_anthropic_client = None

SYSTEM_PROMPT = (
    "You verify product matches for a bicycle retailer's price-comparison tool. "
    "Given pairs of (ours: our catalog item, theirs: a competitor's listing), "
    "decide whether they refer to the same product. Verdicts: "
    "'same_variant' = same model AND same size/spec; "
    "'same_model' = same product model but a different or undeterminable "
    "size/color variant; "
    "'different' = not the same product — a different model year counts as "
    "'different' unless the titles clearly indicate the same year. "
    "Edition/technology suffixes define DIFFERENT models, not variants: "
    "'Grand Prix 5000 TT TR' vs 'Grand Prix 5000' is 'different', as are "
    "S TR vs TR vs AS TR, tubeless vs clincher vs tubular, disc vs rim, "
    "Di2/AXS/eTap vs mechanical, and trim tiers (SL/Pro/Comp/Expert/Sport). "
    "When one title carries such a suffix and the other does not, that is "
    "'different' unless another field proves otherwise. "
    "'uncertain' = you cannot tell from the given fields. "
    "Extract the competitor's size token into competitor_size when present "
    "(e.g. '56', 'M', 'XL', '700 x 25c'), else null. Do not guess: prices "
    "differing wildly (>40%) is evidence against a match. Keep each reason "
    "under 20 words."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair_id": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["same_variant", "same_model", "different", "uncertain"],
                    },
                    "competitor_size": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["pair_id", "verdict", "competitor_size", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _build_pair(link: dict, item: dict) -> dict:
    return {
        "pair_id": link["link_id"],
        "ours": {
            "title": item.get("title"),
            "brand": item.get("brand"),
            "sku": item.get("sku"),
            "upc": item.get("upc_normalized"),
            "price": item.get("current_retail"),
            "matrix_description": item.get("matrix_description"),
            # All variant attributes — size can live in any slot (tires keep
            # color in attribute_1 and "700c x 28mm" in attribute_2).
            "variant_attributes": [
                a for a in (item.get("attribute_1"), item.get("attribute_2"),
                            item.get("attribute_3"))
                if a and str(a).strip()
            ],
        },
        "theirs": {
            "title": link.get("competitor_title"),
            "sku": link.get("competitor_sku"),
            "price": link.get("their_price"),
            "url": link.get("competitor_url"),
        },
    }


def _size_matches(size: str, attr: str) -> bool:
    """Competitor size tokens rarely equal our attribute strings verbatim
    ("700 x 25c" vs "700c x 25mm"), so also compare their digit sequences."""
    size, attr = size.strip().lower(), attr.strip().lower()
    if size == attr:
        return True
    size_digits = re.findall(r"\d+", size)
    return bool(size_digits) and size_digits == re.findall(r"\d+", attr)


def _resolve_model_anchor(item: dict, competitor_size, tracked_by_matrix: dict):
    """For a same_model verdict, picks the tracked variant the link should attach
    to. Returns (item_id, resolved) — resolved False means ambiguous (stays
    pending for a human to pick the variant).

    A competitor variant pairs with exactly ONE of our variants, so a matrix
    item only auto-confirms when the extracted size matches exactly one tracked
    variant — even a lone tracked variant has a specific size, and anchoring an
    unverified size to it is how one item ends up carrying every competitor
    variant of the model."""
    matrix_id = item.get("item_matrix_id")
    variants = tracked_by_matrix.get(matrix_id, []) if matrix_id else []
    if not variants:
        # Non-matrix item: there is no other variant it could be.
        return str(item["item_id"]), True
    if competitor_size:
        size = str(competitor_size)
        hits = [
            v for v in variants
            if any(
                _size_matches(size, str(v.get(a) or ""))
                for a in ("attribute_1", "attribute_2", "attribute_3")
                if v.get(a)
            )
        ]
        if len(hits) == 1:
            return str(hits[0]["item_id"]), True
    # No unambiguous size resolution: keep the fuzzy anchor but require human
    # confirmation.
    return str(item["item_id"]), False


def verify_candidates(max_pairs: int = None) -> dict:
    if not config.ANTHROPIC_API_KEY:
        print("pi: ANTHROPIC_API_KEY not set; skipping match verification")
        return {"skipped": "no api key"}
    max_pairs = max_pairs or config.MATCH_MAX_PAIRS_PER_RUN
    # active_items_only: links to archived items are frozen, not queued — they'd
    # otherwise occupy the fetch window every run while being unverifiable.
    links = repository.get_product_links(
        status="pending", unverified_only=True, active_items_only=True,
        limit=max_pairs
    )
    if not links:
        return {"pairs": 0}

    tracked = repository.get_tracked_products(include_excluded=True)
    by_id = {str(t["item_id"]): t for t in tracked}
    tracked_by_matrix = {}
    for t in tracked:
        if t.get("item_matrix_id"):
            tracked_by_matrix.setdefault(t["item_matrix_id"], []).append(t)

    # One confirmed link per (item, competitor): a competitor variant pairs
    # with exactly one of our variants, so once a pair is taken, further
    # would-be confirmations stay pending for human review.
    confirmed_pairs = {
        (str(l["item_id"]), l.get("competitor_id"))
        for l in repository.get_product_links(status="confirmed", limit=5000)
        if l.get("item_id")
    }

    def _claim_pair(item_id: str, competitor_id, update: dict) -> bool:
        pair = (str(item_id), competitor_id)
        if pair in confirmed_pairs:
            update["status"] = "pending"
            update["llm_reason"] = (
                f"{update.get('llm_reason') or ''} "
                "[item already has a confirmed link at this store]"
            ).strip()[:300]
            return False
        confirmed_pairs.add(pair)
        return True

    client = _get_anthropic_client()
    stats = {"pairs": 0, "confirmed": 0, "rejected": 0, "pending": 0, "errors": 0,
             "input_tokens": 0, "output_tokens": 0}
    updates = []
    now = repository.utcnow_iso()

    for start in range(0, len(links), config.MATCH_BATCH_SIZE):
        batch = [l for l in links[start:start + config.MATCH_BATCH_SIZE]
                 if str(l.get("item_id")) in by_id]
        if not batch:
            continue
        pairs = [_build_pair(l, by_id[str(l["item_id"])]) for l in batch]
        by_link_id = {l["link_id"]: l for l in batch}
        try:
            message = client.messages.create(
                model=config.MATCH_MODEL,
                max_tokens=config.MATCH_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
                messages=[{"role": "user", "content": json.dumps(pairs, default=str)}],
            )
            stats["input_tokens"] += message.usage.input_tokens
            stats["output_tokens"] += message.usage.output_tokens
            text = next(b.text for b in message.content if b.type == "text")
            results = json.loads(text)["results"]
        except Exception as e:
            print(f"pi: match verification batch failed: {e}")
            for link in batch:
                # Same key set as verdict updates (update_link_verdicts derives
                # the MERGE SET clause from the first row's keys).
                updates.append({
                    "link_id": link["link_id"],
                    "item_id": str(link["item_id"]),
                    "status": "pending",
                    "level": link.get("level"),
                    "confidence": link.get("confidence"),
                    "llm_verdict": "error",
                    "llm_reason": str(e)[:300],
                    "updated_at": now,
                })
                stats["errors"] += 1
            continue

        for result in results:
            link = by_link_id.get(result.get("pair_id"))
            if link is None:
                continue
            verdict = result.get("verdict")
            item = by_id.get(str(link["item_id"])) or {}
            update = {
                "link_id": link["link_id"],
                "item_id": str(link["item_id"]),
                "status": "pending",
                "level": link.get("level"),
                "confidence": link.get("confidence"),
                "llm_verdict": verdict,
                "llm_reason": (result.get("reason") or "")[:300],
                "updated_at": now,
            }
            if verdict == "same_variant":
                update.update(status="confirmed", level="variant", confidence=0.95)
                if _claim_pair(link["item_id"], link.get("competitor_id"), update):
                    stats["confirmed"] += 1
                else:
                    stats["pending"] += 1
            elif verdict == "same_model":
                anchor_id, resolved = _resolve_model_anchor(
                    item, result.get("competitor_size"), tracked_by_matrix
                )
                update.update(item_id=anchor_id, level="model")
                if resolved:
                    update.update(status="confirmed", confidence=0.85)
                    if _claim_pair(anchor_id, link.get("competitor_id"), update):
                        stats["confirmed"] += 1
                    else:
                        stats["pending"] += 1
                else:
                    stats["pending"] += 1
            elif verdict == "different":
                update.update(status="rejected")
                stats["rejected"] += 1
            else:
                stats["pending"] += 1
            updates.append(update)
            stats["pairs"] += 1

    repository.update_link_verdicts(updates)
    return stats
