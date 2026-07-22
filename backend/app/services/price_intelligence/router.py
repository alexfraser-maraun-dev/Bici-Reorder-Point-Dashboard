"""API routes for the Price Intelligence page. Mounted by main.py only when
PRICE_INTEL_ENABLED is on, so none of this (or its imports) exists in a disabled
deployment."""
import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from . import config, pricing, repository, scrape_runner

router = APIRouter(prefix="/api/price-intel", tags=["price-intel"])


@router.get("/health")
def health():
    checks = {"enabled": True, "anthropic_key_present": bool(config.ANTHROPIC_API_KEY)}
    try:
        repository.ensure_pi_tables()
        checks["bq_tables"] = "ok"
    except Exception as e:
        checks["bq_tables"] = f"error: {e}"
    from . import settings as pi_settings
    checks["schedule"] = (
        f"{pi_settings.get('schedule_hour'):02d}:{pi_settings.get('schedule_minute'):02d} "
        f"{pi_settings.get('schedule_timezone')}"
        if pi_settings.get("schedule_enabled") else "disabled"
    )
    return {"status": "ok", "checks": checks}


# --- admin console settings --------------------------------------------------

@router.get("/settings")
def get_settings():
    """Effective settings + env defaults for the Admin tab. Secret values
    (Slack webhooks) are masked."""
    from . import settings as pi_settings
    return {"settings": pi_settings.describe()}


@router.put("/settings")
def update_settings(payload: Dict[str, Any]):
    """Partial update: {"changes": {key: value | null}}. null clears the
    override so the setting reverts to its env-var default."""
    from . import settings as pi_settings
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=400, detail="changes is required")
    try:
        pi_settings.update(changes, actor=payload.get("actor") or "Dashboard")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "success", "settings": pi_settings.describe()}


@router.get("/summary")
def get_summary():
    """KPIs for the overview cards + the nav badge count."""
    tracked = repository.get_tracked_products_with_market()
    active = [t for t in tracked if not t.get("excluded")]
    compared = [
        t for t in active
        if t.get("market_min_in_stock") is not None and t.get("current_retail")
    ]
    cheaper = sum(1 for t in compared if t["current_retail"] < t["market_min_in_stock"] - 0.01)
    pricier = sum(1 for t in compared if t["current_retail"] > t["market_min_in_stock"] + 0.01)
    index_vals = [
        t["current_retail"] / t["market_min_in_stock"]
        for t in compared if t["market_min_in_stock"]
    ]
    runs = repository.get_scrape_runs(limit=1)
    return {
        "tracked_count": len(active),
        "compared_count": len(compared),
        "cheaper": cheaper,
        "parity": len(compared) - cheaper - pricier,
        "pricier": pricier,
        "price_index_vs_market_min": round(sum(index_vals) / len(index_vals), 3) if index_vals else None,
        "unacknowledged_changes": repository.count_unacknowledged_events(),
        "map_tracked_count": sum(1 for t in active if t.get("is_map")),
        "upc_tracked_count": sum(1 for t in active if t.get("upc_normalized")),
        "pending_links": repository.count_pending_links(),
        "last_run": runs[0] if runs else None,
        "scrape_status": scrape_runner.get_status(),
        "scrape_health": repository.get_scrape_health(),
    }


# --- competitors (feature f) -------------------------------------------------

@router.get("/competitors")
def list_competitors():
    return repository.get_competitors()


@router.post("/competitors")
def create_or_update_competitor(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    if not payload.get("name") or not payload.get("base_url"):
        raise HTTPException(status_code=400, detail="name and base_url are required")
    row = repository.upsert_competitor(payload)

    # Autodetect the connector tier in the background so the POST stays fast.
    def _detect(competitor: dict):
        try:
            from .connectors import detect_connector_type
            competitor["connector_type"] = detect_connector_type(competitor["base_url"])
            repository.upsert_competitor(competitor)
        except Exception as e:
            print(f"pi: connector detection failed for {competitor['base_url']}: {e}")

    if not payload.get("connector_type"):
        background_tasks.add_task(_detect, dict(row))
    return {"status": "success", "competitor": row}


@router.delete("/competitors/{competitor_id}")
def disable_competitor(competitor_id: str):
    repository.set_competitor_enabled(competitor_id, False)
    return {"status": "success"}


# --- tracked URLs (feature a) --------------------------------------------------

@router.get("/urls")
def list_tracked_urls():
    return repository.get_tracked_urls()


def _variant_identity(payload: dict, item: dict) -> dict:
    options = payload.get("variant_options")
    if options is None and payload.get("variant_options_json"):
        try:
            options = json.loads(payload["variant_options_json"])
        except (TypeError, ValueError):
            options = []
    if options is None:
        options = [item.get(k) for k in ("attribute_1", "attribute_2", "attribute_3")
                   if item.get(k)]
    return {
        "sku": payload.get("competitor_sku") or item.get("sku"),
        "gtin": payload.get("competitor_gtin") or item.get("upc_normalized"),
        "variant_id": payload.get("competitor_variant_id"),
        "variant_options": options,
    }


def _inspect_linked_url(url: str, item_id: str, payload: dict) -> dict:
    """Resolve a manual URL before saving it; 409 carries selectable candidates."""
    items = {str(r["item_id"]): r for r in repository.get_tracked_products(include_archived=True)}
    item = items.get(str(item_id))
    if item is None:
        matches = repository.search_snapshot_items(str(item_id), limit=2)
        item = next((r for r in matches if str(r.get("item_id")) == str(item_id)), {})
    identity = _variant_identity(payload, item or {})
    from .connectors import PageScraper, resolve_listing
    scraper = PageScraper()
    result = resolve_listing(scraper.listings(url), identity)
    selected = result.get("listing")
    # A matrix item cannot safely accept a lone parent/product price unless an
    # identity signal actually matched it.
    unsafe_parent = bool((item or {}).get("item_matrix_id") and
                         result.get("matched_by") == "single_listing" and
                         selected and selected.get("price_scope") == "product")
    if result["status"] == "ambiguous" or unsafe_parent:
        candidates = result.get("candidates") or ([selected] if selected else [])
        raise HTTPException(status_code=409, detail={
            "code": "variant_selection_required",
            "message": "Select the competitor variant that matches this item.",
            "candidates": [{k: row.get(k) for k in (
                "title", "sku", "gtin", "variant_id", "variant_options", "price",
                "compare_at_price", "currency", "in_stock", "price_scope",
                "extraction_method")}
                for row in candidates],
        })
    if result["status"] == "not_found" or selected is None:
        raise HTTPException(status_code=422, detail="No price listing was found at this URL")
    # An identity match makes the result variant-grain even if its markup called
    # the enclosing object a Product rather than a variant.
    if result.get("matched_by") in ("sku", "gtin", "variant_id", "variant_options"):
        selected["price_scope"] = "variant"
    return selected


def _link_url_to_item(url: str, item_id: str, competitor_id=None, label=None,
                      url_id=None, selected_listing=None):
    """Records a confirmed manual_url link and tombstones any conflicting
    auto-matches for the same item at the same store — the human-provided URL is
    the truth. Items not already actively tracked get pinned so their market
    data displays; items tracked via the 'track' tag are left tag-governed
    (removing the tag still archives them). Finishes by fetching the page once
    so the price / market min / store count update immediately instead of after
    the next nightly run."""
    from urllib.parse import urlparse
    from . import seeding
    active_ids = {str(r["item_id"]) for r in repository.get_tracked_products()}
    if str(item_id) not in active_ids:
        try:
            seeding.add_manual_tracked_product(str(item_id))
        except ValueError:
            pass  # not in the snapshot (archived in LS) — the URL still tracks it
    # Make this URL the sole match at its store: reject other link rows AND sweep
    # bare fuzzy 'title match' listings (no link row) so nothing stale lingers.
    repository.sweep_domain_for_item(str(item_id), url)
    now = repository.utcnow_iso()
    link_identity = ((selected_listing or {}).get("variant_id") or
                     (selected_listing or {}).get("sku") or
                     (selected_listing or {}).get("gtin") or str(item_id))
    repository.upsert_human_link({
        "link_id": str(uuid.uuid4()),
        "item_id": str(item_id),
        "competitor_id": competitor_id,
        "match_key": f"url:{url}:{link_identity}",
        "competitor_url": url,
        "competitor_sku": (selected_listing or {}).get("sku"),
        "competitor_title": (selected_listing or {}).get("title") or label,
        "gtin": (selected_listing or {}).get("gtin"),
        "variant_id": (selected_listing or {}).get("variant_id"),
        "variant_options_json": json.dumps((selected_listing or {}).get("variant_options") or []),
        "level": "variant",
        "status": "confirmed",
        "source": "manual_url",
        "confidence": 1.0,
        "fuzzy_score": None,
        "llm_verdict": None,
        "llm_reason": None,
        "our_price": None,
        "their_price": None,
        "decided_by": "Dashboard",
        "created_at": now,
        "updated_at": now,
    })

    # Immediate first observation (one polite request) so the dashboard's
    # price / market min / Stores columns reflect the new match right away.
    try:
        from .connectors import PageScraper
        parsed = selected_listing or PageScraper().fetch(url)
        if parsed is not None and parsed.get("price") is not None:
            repository.load_rows(repository.T_OBSERVATIONS, [{
                "observed_at": repository.utcnow_iso(),
                "run_id": f"manual-link-{uuid.uuid4()}",
                "source": "url",
                # same diff key the nightly tracked-URL phase uses, so change
                # detection diffs against this baseline instead of re-alerting
                "diff_key": (f"url:{url}:" + str(parsed.get("variant_id") or
                             parsed.get("sku") or parsed.get("gtin") or "unresolved")),
                "competitor_id": competitor_id,
                "url": url,
                "competitor_title": parsed.get("title"),
                "competitor_sku": parsed.get("sku"),
                "gtin": parsed.get("gtin"),
                "match_item_id": str(item_id),
                "match_method": "manual_url",
                "match_confidence": 1.0,
                "price": parsed.get("price"),
                "compare_at_price": parsed.get("compare_at_price"),
                "currency": parsed.get("currency"),
                "in_stock": parsed.get("in_stock"),
                "extraction_method": parsed.get("extraction_method"),
                "price_scope": parsed.get("price_scope") or "variant",
                "variant_id": parsed.get("variant_id"),
                "variant_options_json": json.dumps(parsed.get("variant_options") or []),
                "price_low": parsed.get("price_low"),
                "price_high": parsed.get("price_high"),
            }])
            if url_id:
                repository.mark_url_scraped(url_id, "success")
            repository.invalidate_pi_caches()
        elif url_id:
            repository.mark_url_scraped(url_id, "no_price")
    except Exception as e:
        print(f"pi: immediate fetch of manual URL failed (next run retries): {e}")


@router.post("/urls")
def create_tracked_url(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http(s)://")
    if not payload.get("competitor_id"):
        # Infer the competitor from the URL's domain when possible.
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        for c in repository.get_competitors(include_disabled=False):
            if domain and domain in c["base_url"].lower():
                payload["competitor_id"] = c["competitor_id"]
                break
    selected = None
    if payload.get("item_id"):
        selected = _inspect_linked_url(url, str(payload["item_id"]), payload)
        payload.update({
            "competitor_sku": selected.get("sku"),
            "competitor_variant_id": selected.get("variant_id"),
            "competitor_gtin": selected.get("gtin"),
            "variant_options_json": json.dumps(selected.get("variant_options") or []),
        })
    # Same URL registered again (e.g. correcting a match from another tab):
    # update the existing row instead of creating a duplicate to scrape twice.
    existing = next(
        (u for u in repository.get_tracked_urls()
         if u["url"] == url and u.get("enabled")
         and (str(u.get("item_id") or "") == str(payload.get("item_id") or ""))),
        None,
    )
    if existing:
        payload["url_id"] = existing["url_id"]
        payload.setdefault("label", existing.get("label"))
        payload.setdefault("competitor_id", existing.get("competitor_id"))
    row = repository.upsert_tracked_url(payload)
    if payload.get("item_id"):
        background_tasks.add_task(
            _link_url_to_item, url, str(payload["item_id"]),
            payload.get("competitor_id"), payload.get("label"),
            row["url_id"], selected,
        )
    return {"status": "success", "url": row}


@router.put("/urls/{url_id}")
def update_tracked_url(url_id: str, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """Links a tracked URL to an item (or updates label/competitor)."""
    rows = [u for u in repository.get_tracked_urls() if u.get("url_id") == url_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Tracked URL not found")
    selected = None
    if payload.get("item_id"):
        selected = _inspect_linked_url(rows[0]["url"], str(payload["item_id"]), payload)
        payload.update({
            "competitor_sku": selected.get("sku"),
            "competitor_variant_id": selected.get("variant_id"),
            "competitor_gtin": selected.get("gtin"),
            "variant_options_json": json.dumps(selected.get("variant_options") or []),
        })
    repository.update_tracked_url(url_id, payload)
    if payload.get("item_id"):
        background_tasks.add_task(
            _link_url_to_item, rows[0]["url"], str(payload["item_id"]),
            payload.get("competitor_id") or rows[0].get("competitor_id"),
            payload.get("label") or rows[0].get("label"),
            url_id, selected,
        )
    return {"status": "success"}


@router.delete("/urls/{url_id}")
def disable_tracked_url(url_id: str):
    repository.set_tracked_url_enabled(url_id, False)
    return {"status": "success"}


# --- tracked products (feature b) ---------------------------------------------

@router.get("/tracked")
def list_tracked_products():
    return repository.get_tracked_products_with_market()


@router.post("/tracked/seed")
def reseed_tracked_products(background_tasks: BackgroundTasks):
    from . import seeding
    if not seeding.queue_refresh(trigger="manual"):
        return {"status": "already_running", "mode": config.SEED_MODE}
    background_tasks.add_task(
        seeding.refresh_tracked_products, trigger="manual",
    )
    return {"status": "started", "mode": config.SEED_MODE}


@router.get("/tracked/seed/status")
def reseed_tracked_products_status():
    from . import seeding
    return seeding.get_refresh_status()


@router.post("/tracked/pin")
def pin_item(payload: Dict[str, Any]):
    item_id = payload.get("item_id")
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id is required")
    from . import seeding
    try:
        seeding.add_manual_tracked_product(str(item_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success"}


def _subscribe_matrix(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Record a persistent matrix subscription and do the first expansion now.
    Shared by /tracked/track-matrix and the legacy /tracked/pin-matrix so both
    paths give the matrix self-syncing (not one-shot) semantics.

    Tracking a matrix is NOT pinning: variants land as source='matrix_sub' (unpinned)
    via the same expand_tracked_matrices the nightly run uses, so the set stays in sync
    and untracking can archive them. (Pinning is a separate, explicit per-item action.)"""
    matrix_id = payload.get("item_matrix_id")
    if not matrix_id:
        raise HTTPException(status_code=400, detail="item_matrix_id is required")
    matrix_id = str(matrix_id)
    from . import seeding
    try:
        variants = seeding.count_matrix_snapshot_variants(matrix_id)
    except Exception:
        variants = 0
    # Search can offer a matrix the *live* snapshot no longer has (the search index
    # refreshes nightly, so it can lag archival/new items by up to a day). Don't create
    # an empty subscription or throw a scary 404 — report it plainly so the UI can
    # explain and suggest a Re-seed.
    if not variants:
        return {
            "status": "empty", "item_matrix_id": matrix_id, "variants": 0,
            "warning": ("No active items for this matrix in the latest snapshot. If you "
                        "just changed it in Lightspeed, Re-seed the list and try again."),
        }
    # Persist the subscription first, then expand it through the shared self-sync so the
    # variants are inserted exactly as the nightly run would (source='matrix_sub').
    repository.upsert_tracked_matrix(
        matrix_id,
        matrix_description=payload.get("matrix_description"),
        brand=payload.get("brand"),
    )
    try:
        seeding.expand_tracked_matrices()
    except Exception as e:
        # Subscription is persisted; the nightly sync will still expand it. Report the
        # count we validated rather than failing the whole subscribe.
        print(f"pi: initial matrix expansion failed (nightly sync will retry): {e}")
    return {"status": "success", "variants": variants, "item_matrix_id": matrix_id}


@router.get("/tracked/matrices")
def list_tracked_matrices(days: int = 7):
    """Matrix-grain rollup for the tracked table (one row per tracked matrix)."""
    return repository.get_tracked_matrices_with_market(days=days)


@router.get("/tracked/matrices/{matrix_id}/coverage")
def matrix_coverage(matrix_id: str, days: int = 45):
    """Per-competitor variant coverage / undercut for one matrix."""
    return repository.get_matrix_coverage(matrix_id, days=days)


@router.post("/tracked/track-matrix")
def track_matrix(payload: Dict[str, Any]):
    """Subscribe a matrix as a unit: houses every current variant now and keeps the
    set in sync each run (seeding.expand_tracked_matrices)."""
    return _subscribe_matrix(payload)


@router.delete("/tracked/track-matrix/{matrix_id}")
def untrack_matrix(matrix_id: str):
    """Unsubscribe a matrix: deactivate the subscription and archive the variants it
    auto-added (source='matrix_sub', unpinned). Pinned/tag-owned variants stay."""
    repository.set_tracked_matrix_active(str(matrix_id), False)
    repository.archive_matrix_sub_variants(str(matrix_id))
    return {"status": "success"}


@router.post("/tracked/pin-matrix")
def pin_matrix(payload: Dict[str, Any]):
    """Legacy alias for track-matrix ("pin all variants" in the search picker)."""
    return _subscribe_matrix(payload)


@router.put("/tracked/{item_id}")
def update_tracked(item_id: str, payload: Dict[str, Any]):
    repository.update_tracked_product(item_id, payload)
    return {"status": "success"}


@router.get("/tracked/{item_id}/competitors")
def item_competitor_prices(item_id: str, days: int = 45):
    """Per-store latest-price breakdown behind the aggregated market columns."""
    return repository.get_item_competitor_prices(item_id, days=days)


@router.post("/tracked/{item_id}/reject-competitor")
def reject_competitor(item_id: str, payload: Dict[str, Any]):
    """Reject one competitor listing for this item (escape hatch for a stuck match).
    Tombstones the listing so it won't re-match, and clears its observations."""
    return repository.reject_competitor_listing(
        item_id, payload.get("competitor_id"), payload.get("url"),
        decided_by=payload.get("actor") or "Dashboard",
    )


@router.get("/tracked/{item_id}/price-history")
def item_price_history(item_id: str, days: int = 120):
    """Change-point-compressed price history (our line + one per competitor) for the
    expandable chart. Compressed server-side so the payload stays small."""
    return repository.get_item_price_history(item_id, days=days)


@router.get("/items/search")
def search_items(q: str):
    if not q or len(q.strip()) < 2:
        return []
    return repository.search_snapshot_items(q.strip())


@router.get("/items/search-index/status")
def item_search_index_status():
    return repository.item_search_index_status()


# --- product links / match review ------------------------------------------------

@router.get("/links")
def list_links(status: Optional[str] = None, item_id: Optional[str] = None,
               limit: int = 500):
    # The pending queue is a to-do list: links frozen by their item's archival
    # don't belong in it. Confirmed/rejected views keep full history.
    return repository.get_product_links(
        status=status, item_id=item_id,
        active_items_only=(status == "pending"), limit=limit,
    )


@router.post("/competitors/backfill-url-links")
def backfill_url_links(apply: bool = False):
    """Associate NULL-competitor URL rows (tracked URLs, their observations, manual
    links) with the registered competitor sharing their domain. Dry-run unless apply=true."""
    return repository.backfill_url_competitor_ids(apply=apply)


@router.post("/links/cleanup")
def cleanup_links(apply: bool = False):
    """One-off hygiene: reject color/size-mismatched links and enforce one
    confirmed link per (item, store). Dry-run unless apply=true."""
    return repository.cleanup_mismatched_links(apply=apply)


@router.post("/links/{link_id}/decision")
def decide_link(link_id: str, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    status = payload.get("status")
    if status not in ("confirmed", "rejected"):
        raise HTTPException(status_code=400, detail="status must be confirmed or rejected")
    actor = payload.get("actor") or "Dashboard"
    if status == "confirmed":
        # Guarded: enforces attribute match + one confirmed link per (item, store).
        # replace=true rejects the existing match at that store and takes over.
        result = repository.confirm_link(link_id, decided_by=actor, replace=bool(payload.get("replace")))
        # Fetch a first price immediately so the match shows in Tracked Products
        # within seconds instead of waiting for the next scrape.
        if result.get("status") == "confirmed":
            background_tasks.add_task(repository.fetch_and_record_link, link_id)
        return result
    repository.decide_link(link_id, status, decided_by=actor)
    return {"status": "rejected"}


# --- scraping ------------------------------------------------------------------

@router.post("/scrape")
def trigger_scrape(full: bool = False, x_scrape_token: Optional[str] = Header(default=None)):
    """full=true forces a whole-catalog crawl; otherwise the run is targeted
    (confirmed-link URLs only) unless new items still need catalog discovery."""
    if config.REQUIRE_SCRAPE_TOKEN and x_scrape_token != config.SCRAPE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid scrape token")
    run_id = scrape_runner.start_scrape(trigger="manual", full=full)
    if run_id is None:
        raise HTTPException(status_code=409, detail="A scrape run is already in progress")
    return {"status": "started", "run_id": run_id}


@router.get("/scrape/status")
def scrape_status():
    return scrape_runner.get_status()


@router.get("/runs")
def list_runs():
    return repository.get_scrape_runs()


# --- change feed (feature a) ----------------------------------------------------

@router.get("/changes")
def list_changes(days: int = 14, acknowledged: Optional[bool] = None,
                 competitor_id: Optional[str] = None, event_type: Optional[str] = None,
                 min_pct: Optional[float] = None, brand: Optional[str] = None,
                 limit: int = 200):
    event_types = [t.strip() for t in event_type.split(",") if t.strip()] if event_type else None
    return repository.get_change_events(
        days=days, acknowledged=acknowledged, competitor_id=competitor_id,
        event_types=event_types, min_abs_pct=min_pct, brand=brand, limit=limit,
    )


@router.post("/changes/ack")
def acknowledge_changes(payload: Dict[str, Any]):
    ids = payload.get("event_ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="event_ids is required")
    repository.acknowledge_events(ids)
    return {"status": "success", "acknowledged": len(ids)}


# --- price history (feature d) ---------------------------------------------------

@router.get("/observations")
def item_observations(item_id: str, days: int = 120):
    return repository.get_item_observations(item_id, days=days)


# --- LLM digest (feature e) -------------------------------------------------------

@router.get("/digest/latest")
def latest_digest():
    return repository.get_latest_digest() or {}


@router.post("/digest/generate")
def regenerate_digest(background_tasks: BackgroundTasks):
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY is not configured")
    runs = repository.get_scrape_runs(limit=1)
    if not runs:
        raise HTTPException(status_code=400, detail="No scrape runs yet")
    from . import digest

    def _generate(run_id: str):
        try:
            digest.generate_digest(run_id)
        except Exception as e:
            print(f"pi: manual digest generation failed: {e}")

    background_tasks.add_task(_generate, runs[0]["run_id"])
    return {"status": "started", "run_id": runs[0]["run_id"]}


# --- Slack notifications -----------------------------------------------------------

@router.post("/notify/test")
def notify_test():
    """Send a sample digest + MAP/undercut pings to the configured Slack webhooks
    so you can confirm setup before a real scrape runs."""
    from . import notify
    result = notify.send_test()
    if not result.get("sent"):
        raise HTTPException(status_code=400, detail=result.get("reason", "Slack not configured"))
    return result


# --- price push (feature c) --------------------------------------------------------

@router.post("/push-price/preview")
def preview_price_push(payload: Dict[str, Any]):
    item_id, new_price = payload.get("item_id"), payload.get("new_price")
    if not item_id or new_price is None:
        raise HTTPException(status_code=400, detail="item_id and new_price are required")
    try:
        return pricing.build_preview(str(item_id), float(new_price))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/push-price")
def execute_price_push(payload: Dict[str, Any]):
    item_id, new_price = payload.get("item_id"), payload.get("new_price")
    if not item_id or new_price is None:
        raise HTTPException(status_code=400, detail="item_id and new_price are required")
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="confirm: true is required")
    try:
        return pricing.push_price(
            str(item_id), float(new_price),
            actor=payload.get("actor") or "Dashboard",
            override_floor=bool(payload.get("override_floor")),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail="Lightspeed update failed")


@router.post("/push-price/matrix/preview")
def preview_matrix_price_push(payload: Dict[str, Any]):
    """Per-variant guard preview for a whole-matrix push. The variant list is read
    live from Lightspeed so the client can show (and later echo back) exactly what
    would be repriced."""
    item_id, new_price = payload.get("item_id"), payload.get("new_price")
    if not item_id or new_price is None:
        raise HTTPException(status_code=400, detail="item_id and new_price are required")
    try:
        return pricing.build_matrix_preview(str(item_id), float(new_price))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load the matrix from Lightspeed")


@router.post("/push-price/matrix")
def execute_matrix_price_push(payload: Dict[str, Any]):
    item_id, new_price = payload.get("item_id"), payload.get("new_price")
    expected = payload.get("expected_item_ids")
    if not item_id or new_price is None:
        raise HTTPException(status_code=400, detail="item_id and new_price are required")
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="confirm: true is required")
    if not isinstance(expected, list) or not expected:
        raise HTTPException(
            status_code=400,
            detail="expected_item_ids (the previewed variant list) is required")
    try:
        return pricing.push_matrix_price(
            str(item_id), float(new_price),
            actor=payload.get("actor") or "Dashboard",
            override_floor=bool(payload.get("override_floor")),
            expected_item_ids=expected,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Lightspeed update failed")


@router.get("/push-log")
def push_log():
    return repository.get_price_push_logs()
