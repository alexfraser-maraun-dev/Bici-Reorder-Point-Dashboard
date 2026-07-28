"""LLM verification of pending product-link candidates.

After each scrape run, pending proposals (pi_product_links rows with
status='pending', no verdict yet) are batched to a small model that decides
whether each competitor listing is the same product as our catalog item.
Verdicts write back to the links table:
  same_variant -> confirmed (variant, 0.95) when PI_AUTO_CONFIRM is on;
                  otherwise stays pending with the verdict as triage annotation
  same_model   -> re-anchored to the size-matched tracked variant; confirmed
                  (model, 0.85) only when PI_AUTO_CONFIRM is on AND the anchor
                  is unambiguous; otherwise pending for human review
  different    -> rejected (tombstone: the match_key is never re-proposed) —
                  auto-reject stays on in manual mode to keep the queue clean,
                  except gtin-sourced proposals (a barcode/LLM conflict goes
                  to a human, not a tombstone)
  uncertain    -> stays pending for human review

Mirrors digest.py: lazy client, ANTHROPIC_API_KEY guard, best-effort — the
caller swallows failures so scrape data is never lost.
"""
import json
import re

from . import config, matcher, repository, settings

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
    "TRIM RULE, apply before any other reasoning and without exception: if one "
    "title carries a trim/edition token the other lacks, the verdict is "
    "'different' — never 'same_model'. A base model and its uprated version are "
    "two products that merely share a name. 'Giro Eclipse Pro Spherical Helmet' "
    "vs 'Eclipse Spherical Helmet' is 'different'. 'Aeroad CF SLX' vs 'Aeroad "
    "CF SL' is 'different'. Sharing a colour and size does not make them the "
    "same product, and neither does a plausible price gap. "
    "If ours.model_already_matched_at_this_store is present, that store sells "
    "our model on that other page — a listing at a DIFFERENT url is then "
    "almost certainly a neighbouring model: prefer 'different' unless the "
    "titles match exactly. "
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


def _build_pair(link: dict, item: dict, known_page: str = None) -> dict:
    ours = {
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
    }
    # Only worth sending when it contradicts the proposal — the page this
    # listing is on is already the known one otherwise.
    if known_page and known_page != matcher.page_key(link.get("competitor_url")):
        ours["model_already_matched_at_this_store"] = known_page
    return {
        "pair_id": link["link_id"],
        "ours": ours,
        "theirs": {
            "title": link.get("competitor_title"),
            "sku": link.get("competitor_sku"),
            "variant_options": json.loads(link.get("variant_options_json") or "[]"),
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
    to. Returns (item_id, resolved, note) — resolved False means ambiguous (stays
    pending for a human to pick the variant), and note explains an unresolvable
    size so the queue row says why our variant is shown next to a different one.

    A competitor variant pairs with exactly ONE of our variants, so a matrix
    item only auto-confirms when the extracted size matches exactly one tracked
    variant — even a lone tracked variant has a specific size, and anchoring an
    unverified size to it is how one item ends up carrying every competitor
    variant of the model."""
    matrix_id = item.get("item_matrix_id")
    variants = tracked_by_matrix.get(matrix_id, []) if matrix_id else []
    if not variants:
        # Non-matrix item: there is no other variant it could be.
        return str(item["item_id"]), True, None
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
            return str(hits[0]["item_id"]), True, None
        if not hits:
            # Their size is one we don't stock in this matrix, so the item shown
            # is the matrix's anchor variant, not a claimed size match.
            return (str(item["item_id"]), False,
                    f"we track no '{size}' variant of this model")
    # No unambiguous size resolution: keep the fuzzy anchor but require human
    # confirmation.
    return str(item["item_id"]), False, None


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

    confirmed_links = repository.get_product_links(status="confirmed", limit=5000)
    # One confirmed link per (item, competitor): a competitor variant pairs
    # with exactly one of our variants, so once a pair is taken, further
    # would-be confirmations stay pending for human review.
    confirmed_pairs = {
        (str(l["item_id"]), l.get("competitor_id"))
        for l in confirmed_links if l.get("item_id")
    }
    # (model group, competitor) -> the page there we've already confirmed sells
    # it. Given to the model as evidence against a proposal on a different page.
    known_pages = {}
    for link in confirmed_links:
        owner = by_id.get(str(link.get("item_id") or ""))
        page = matcher.page_key(link.get("competitor_url"))
        if owner and page and link.get("competitor_id"):
            known_pages.setdefault(
                (matcher.model_group_key(owner), link["competitor_id"]), page)

    def _known_page(link: dict):
        owner = by_id.get(str(link.get("item_id") or ""))
        if not owner:
            return None
        return known_pages.get(
            (matcher.model_group_key(owner), link.get("competitor_id")))

    # Within one run, several candidates can claim the same (item, competitor).
    # The best attribute (color+size) match wins the slot; a lower-scoring one that
    # already claimed it is demoted back to pending. A pre-existing confirmed link
    # (prior run / human) always blocks — change it with reject-and-replace.
    claimed_in_run = {}  # pair -> (update_dict, score)

    def _claim_pair(item_id: str, competitor_id, update: dict, link: dict, item: dict) -> bool:
        pair = (str(item_id), competitor_id)
        if pair in confirmed_pairs:
            update["status"] = "pending"
            update["llm_reason"] = (
                f"{update.get('llm_reason') or ''} "
                "[item already has a confirmed link at this store]"
            ).strip()[:300]
            return False
        # Structured options when the connector recovered them; the title tail is
        # the fallback for listings that carry the variant only in their name.
        options = (json.loads(link.get("variant_options_json") or "[]")
                   or matcher.parse_variant_options(link.get("competitor_title")))
        score = matcher.attribute_match_score(
            options,
            [item.get("attribute_1"), item.get("attribute_2"), item.get("attribute_3")],
        )
        prev = claimed_in_run.get(pair)
        if prev is not None and score <= prev[1]:
            update["status"] = "pending"
            update["llm_reason"] = (
                f"{update.get('llm_reason') or ''} [a closer variant match won this store]"
            ).strip()[:300]
            return False
        if prev is not None:
            prev[0]["status"] = "pending"
            prev[0]["llm_reason"] = (
                f"{prev[0].get('llm_reason') or ''} [superseded by a closer variant match]"
            ).strip()[:300]
            stats["confirmed"] -= 1
            stats["pending"] += 1
        claimed_in_run[pair] = (update, score)
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
        pairs = [_build_pair(l, by_id[str(l["item_id"])], _known_page(l))
                 for l in batch]
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
                update.update(level="variant")
                if not settings.get("auto_confirm"):
                    # Manual-review mode: the verdict is triage annotation only —
                    # the human confirms from the Matching queue.
                    stats["pending"] += 1
                elif _claim_pair(link["item_id"], link.get("competitor_id"), update, link, item):
                    update.update(status="confirmed", confidence=0.95)
                    stats["confirmed"] += 1
                else:
                    stats["pending"] += 1
            elif verdict == "same_model":
                anchor_id, resolved, note = _resolve_model_anchor(
                    item, result.get("competitor_size"), tracked_by_matrix
                )
                # Re-anchoring to the size-matched variant is useful triage even
                # when the confirm itself waits for a human.
                update.update(item_id=anchor_id, level="model")
                if note:
                    update["llm_reason"] = (
                        f"{update['llm_reason']} [{note}]"
                    ).strip()[:300]
                if not (settings.get("auto_confirm") and resolved):
                    stats["pending"] += 1
                else:
                    update.update(status="confirmed", confidence=0.85)
                    anchor_item = by_id.get(str(anchor_id), item)
                    if _claim_pair(anchor_id, link.get("competitor_id"), update, link, anchor_item):
                        stats["confirmed"] += 1
                    else:
                        update.update(status="pending")
                        stats["pending"] += 1
            elif verdict == "different":
                if link.get("source") == "gtin":
                    # Barcode says same product, LLM says different — a real
                    # conflict worth human eyes, not an auto-tombstone.
                    stats["pending"] += 1
                else:
                    update.update(status="rejected")
                    stats["rejected"] += 1
            else:
                stats["pending"] += 1
            updates.append(update)
            stats["pairs"] += 1

    repository.update_link_verdicts(updates)
    return stats
