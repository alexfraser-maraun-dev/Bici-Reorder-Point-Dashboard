"""API routes for the Price Intelligence page. Mounted by main.py only when
PRICE_INTEL_ENABLED is on, so none of this (or its imports) exists in a disabled
deployment."""
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
    checks["schedule"] = (
        f"{config.SCHEDULE_HOUR_LOCAL:02d}:{config.SCHEDULE_MINUTE_LOCAL:02d} "
        f"{config.SCHEDULE_TIMEZONE}" if config.SCHEDULE_ENABLED else "disabled"
    )
    return {"status": "ok", "checks": checks}


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
        "last_run": runs[0] if runs else None,
        "scrape_status": scrape_runner.get_status(),
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


@router.post("/urls")
def create_tracked_url(payload: Dict[str, Any]):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http(s)://")
    return {"status": "success", "url": repository.upsert_tracked_url(payload)}


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
    background_tasks.add_task(seeding.refresh_tracked_products)
    return {"status": "started"}


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


@router.put("/tracked/{item_id}")
def update_tracked(item_id: str, payload: Dict[str, Any]):
    repository.update_tracked_product(item_id, payload)
    return {"status": "success"}


@router.get("/items/search")
def search_items(q: str):
    if not q or len(q.strip()) < 2:
        return []
    return repository.search_snapshot_items(q.strip())


# --- scraping ------------------------------------------------------------------

@router.post("/scrape")
def trigger_scrape(x_scrape_token: Optional[str] = Header(default=None)):
    if config.REQUIRE_SCRAPE_TOKEN and x_scrape_token != config.SCRAPE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid scrape token")
    run_id = scrape_runner.start_scrape(trigger="manual")
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
def list_changes(days: int = 14, acknowledged: Optional[bool] = None, limit: int = 200):
    return repository.get_change_events(days=days, acknowledged=acknowledged, limit=limit)


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
        raise HTTPException(status_code=502, detail=f"Lightspeed update failed: {e}")


@router.get("/push-log")
def push_log():
    return repository.get_price_push_logs()
