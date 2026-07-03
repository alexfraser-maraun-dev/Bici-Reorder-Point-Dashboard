"""Scrape run lifecycle + the in-process nightly scheduler.

A run is triggered by POST /scrape or the scheduler, acquires a non-blocking lock
(409 if one is already going), and does all work on a daemon thread so the HTTP
request returns immediately (the 120s gunicorn timeout never applies). Live
progress is kept in an in-memory status dict the UI polls; the durable record is
one pi_scrape_runs row written at the end.

Memory discipline (512MB budget shared with the API): competitors are scraped
strictly sequentially, catalogs stream through generators, and observation
buffers flush to BigQuery per competitor (capped at FLUSH_ROWS), so peak state is
one page of products plus the small diff/match dicts.
"""
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config, repository
from .connectors import PageScraper, build_connector, detect_connector_type
from .matcher import MatchIndex

_scrape_lock = threading.Lock()
_status_lock = threading.Lock()
_status = {"status": "idle"}

PRICE_EPSILON = 0.005


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _set_status(**fields):
    with _status_lock:
        _status.update(fields)


def start_scrape(trigger: str = "manual"):
    """Starts a run on a daemon thread. Returns run_id, or None if one is running."""
    if not _scrape_lock.acquire(blocking=False):
        return None
    run_id = str(uuid.uuid4())
    with _status_lock:
        _status.clear()
        _status.update({
            "status": "running", "run_id": run_id, "trigger": trigger,
            "phase": "starting", "started_at": repository.utcnow_iso(),
            "competitors_done": 0, "competitors_total": 0,
            "urls_done": 0, "urls_total": 0,
            "observations": 0, "changes": 0, "errors": [],
        })
    threading.Thread(target=_run, args=(run_id, trigger), daemon=True).start()
    return run_id


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _build_events(prev_map, obs, competitor_name, item_lookup):
    """Diffs one observation against the previous one for its diff_key and returns
    change-event rows. First sightings are logged pre-acknowledged so a new
    competitor doesn't flood the badge; real price/stock changes arrive unread."""
    events = []
    key = obs["diff_key"]
    prev = prev_map.get(key)
    item = item_lookup.get(str(obs.get("match_item_id"))) if obs.get("match_item_id") else None
    base = {
        "run_id": obs["run_id"],
        "occurred_at": obs["observed_at"],
        "competitor_id": obs.get("competitor_id"),
        "competitor_name": competitor_name,
        "item_id": obs.get("match_item_id"),
        "item_title": (item or {}).get("title") or obs.get("competitor_title"),
        "url": obs.get("url"),
        "acknowledged_at": None,
        "notified": False,
        "notified_at": None,
    }

    def event(event_type, old_price, new_price, acknowledged=False):
        pct = None
        if old_price and new_price is not None and old_price > 0:
            pct = round((new_price - old_price) / old_price * 100, 2)
        events.append({
            **base,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "old_price": old_price,
            "new_price": new_price,
            "pct_change": pct,
            "acknowledged": acknowledged,
        })

    new_price = obs.get("price")
    old_price = prev.get("price") if prev else None

    if prev is None:
        event("first_observation" if obs["source"] == "url" else "new_match",
              None, new_price, acknowledged=True)
    else:
        if new_price is not None and old_price is not None:
            if new_price < old_price - PRICE_EPSILON:
                event("price_drop", old_price, new_price)
            elif new_price > old_price + PRICE_EPSILON:
                event("price_increase", old_price, new_price)
        prev_stock, new_stock = prev.get("in_stock"), obs.get("in_stock")
        if prev_stock is not None and new_stock is not None and prev_stock != new_stock:
            event("back_in_stock" if new_stock else "out_of_stock", old_price, new_price)

    # MAP intel: flag a competitor advertising below our MAP price, on the
    # transition only (first sighting below, or a move from >= MAP to < MAP).
    if item and item.get("is_map") and item.get("map_price") and new_price is not None:
        map_price = float(item["map_price"])
        was_below = old_price is not None and old_price < map_price - PRICE_EPSILON
        is_below = new_price < map_price - PRICE_EPSILON
        if is_below and not was_below:
            event("map_violation", old_price, new_price)

    # Update the in-memory prev map so re-observations within one run don't
    # duplicate events.
    prev_map[key] = {"price": new_price, "in_stock": obs.get("in_stock")}
    return events


def _run(run_id: str, trigger: str):
    started_at = _now_iso()
    counters = {"competitors_done": 0, "urls_done": 0, "observations": 0, "changes": 0}
    errors = []
    try:
        _set_status(phase="preparing")
        repository.ensure_pi_tables()

        # Keep the comparison list current before matching against it.
        try:
            from . import seeding
            seeding.refresh_tracked_products()
        except Exception as e:
            errors.append(f"seeding: {e}")
            print(f"pi: seeding failed (continuing with existing list): {e}")

        index = MatchIndex.load()
        item_lookup = index.items
        prev_map = repository.get_latest_observation_map()
        brand_tokens = sorted({(r.get("brand") or "") for r in item_lookup.values()} - {""})

        competitors = [c for c in repository.get_competitors() if c.get("enabled")]
        urls = repository.get_tracked_urls(include_disabled=False)
        _set_status(competitors_total=len(competitors), urls_total=len(urls))

        # --- catalog scrape, one competitor at a time -----------------------
        for competitor in competitors:
            cid = competitor["competitor_id"]
            _set_status(phase=f"scraping {competitor['name']}")
            comp_status = "success"
            try:
                if not competitor.get("connector_type") or competitor["connector_type"] == "unknown":
                    detected = detect_connector_type(competitor["base_url"])
                    competitor["connector_type"] = detected
                    repository.upsert_competitor(competitor)
                connector = build_connector(competitor, brand_tokens=brand_tokens)
                if connector is None:
                    repository.mark_competitor_scraped(cid, "skipped_no_connector")
                    counters["competitors_done"] += 1
                    _set_status(competitors_done=counters["competitors_done"])
                    continue

                obs_buffer, event_buffer = [], []
                for product in connector.iter_products():
                    item_id, method, confidence = index.match(product)
                    observed_at = _now_iso()
                    diff_key = (
                        f"cat:{cid}:"
                        f"{product.get('gtin') or product.get('sku') or product.get('url') or product.get('title')}"
                    )
                    obs = {
                        "observed_at": observed_at,
                        "run_id": run_id,
                        "source": "catalog",
                        "diff_key": diff_key,
                        "competitor_id": cid,
                        "url": product.get("url"),
                        "competitor_title": product.get("title"),
                        "competitor_sku": product.get("sku"),
                        "gtin": product.get("gtin"),
                        "match_item_id": item_id,
                        "match_method": method,
                        "match_confidence": confidence,
                        "price": product.get("price"),
                        "compare_at_price": product.get("compare_at_price"),
                        "currency": product.get("currency"),
                        "in_stock": product.get("in_stock"),
                    }
                    # Only persist rows that matched (or carry a GTIN we may match
                    # later) — storing entire foreign catalogs nightly is noise.
                    if item_id is None and not product.get("gtin"):
                        continue
                    obs_buffer.append(obs)
                    if item_id is not None:
                        event_buffer.extend(
                            _build_events(prev_map, obs, competitor["name"], item_lookup)
                        )
                    if len(obs_buffer) >= config.FLUSH_ROWS:
                        repository.load_rows(repository.T_OBSERVATIONS, obs_buffer)
                        counters["observations"] += len(obs_buffer)
                        obs_buffer = []
                repository.load_rows(repository.T_OBSERVATIONS, obs_buffer)
                counters["observations"] += len(obs_buffer)
                repository.load_rows(repository.T_EVENTS, event_buffer)
                counters["changes"] += sum(1 for e in event_buffer if not e["acknowledged"])
            except Exception as e:
                comp_status = f"failed: {e}"
                errors.append(f"{competitor['name']}: {e}")
                print(f"pi: competitor {competitor['name']} failed: {e}")
            repository.mark_competitor_scraped(cid, comp_status[:200])
            counters["competitors_done"] += 1
            _set_status(competitors_done=counters["competitors_done"],
                        observations=counters["observations"], changes=counters["changes"])

        # --- tracked URLs (feature a) ---------------------------------------
        _set_status(phase="scraping tracked URLs")
        scraper = PageScraper()
        obs_buffer, event_buffer = [], []
        competitor_names = {c["competitor_id"]: c["name"] for c in repository.get_competitors()}
        for url_row in urls:
            url = url_row["url"]
            try:
                parsed = scraper.fetch(url)
                if parsed is None or parsed.get("price") is None:
                    repository.mark_url_scraped(url_row["url_id"], "no_price")
                else:
                    item_id = str(url_row["item_id"]) if url_row.get("item_id") else None
                    method, confidence = ("manual_url", 1.0) if item_id else (None, 0.0)
                    if not item_id:
                        item_id, method, confidence = index.match(parsed)
                    obs = {
                        "observed_at": _now_iso(),
                        "run_id": run_id,
                        "source": "url",
                        "diff_key": f"url:{url}",
                        "competitor_id": url_row.get("competitor_id"),
                        "url": url,
                        "competitor_title": parsed.get("title"),
                        "competitor_sku": parsed.get("sku"),
                        "gtin": parsed.get("gtin"),
                        "match_item_id": item_id,
                        "match_method": method,
                        "match_confidence": confidence,
                        "price": parsed.get("price"),
                        "compare_at_price": parsed.get("compare_at_price"),
                        "currency": parsed.get("currency"),
                        "in_stock": parsed.get("in_stock"),
                    }
                    obs_buffer.append(obs)
                    name = competitor_names.get(url_row.get("competitor_id")) or urlparse_domain(url)
                    event_buffer.extend(_build_events(prev_map, obs, name, item_lookup))
                    repository.mark_url_scraped(url_row["url_id"], "success")
            except Exception as e:
                errors.append(f"url {url}: {e}")
                repository.mark_url_scraped(url_row["url_id"], f"failed: {e}"[:200])
            counters["urls_done"] += 1
            _set_status(urls_done=counters["urls_done"])
        repository.load_rows(repository.T_OBSERVATIONS, obs_buffer)
        counters["observations"] += len(obs_buffer)
        repository.load_rows(repository.T_EVENTS, event_buffer)
        counters["changes"] += sum(1 for e in event_buffer if not e["acknowledged"])
        _set_status(observations=counters["observations"], changes=counters["changes"])

        repository.invalidate_pi_caches()

        # --- LLM digest: best-effort, never fails the run --------------------
        _set_status(phase="generating digest")
        try:
            from . import digest
            digest.generate_digest(run_id)
        except Exception as e:
            errors.append(f"digest: {e}")
            print(f"pi: digest generation failed: {e}")

        status = "partial" if errors else "success"
    except Exception as e:
        errors.append(str(e))
        status = "failed"
        print(f"pi: scrape run {run_id} failed: {e}")
    finally:
        finished_at = _now_iso()
        try:
            repository.save_scrape_run({
                "run_id": run_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "trigger": trigger,
                "status": status,
                "competitors_done": counters["competitors_done"],
                "urls_done": counters["urls_done"],
                "observations_count": counters["observations"],
                "changes_count": counters["changes"],
                "error": "; ".join(errors)[:1000] if errors else None,
            })
        except Exception as e:
            print(f"pi: failed to save run record: {e}")
        _set_status(status=status, phase="done", finished_at=finished_at,
                    errors=errors[:20])
        _scrape_lock.release()


def urlparse_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


# ---------------------------------------------------------------------------
# Nightly scheduler (in-process; Render starter tier never idles the process out)
# ---------------------------------------------------------------------------
_scheduler_started = False


def start_scheduler():
    """Starts the nightly-scrape daemon thread once per process."""
    global _scheduler_started
    if _scheduler_started or not config.SCHEDULE_ENABLED:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def _scheduler_loop():
    tz = ZoneInfo(config.SCHEDULE_TIMEZONE)
    while True:
        try:
            now = datetime.now(tz)
            due = (now.hour, now.minute) >= (config.SCHEDULE_HOUR_LOCAL, config.SCHEDULE_MINUTE_LOCAL)
            if due and get_status().get("status") != "running":
                today = now.strftime("%Y-%m-%d")
                # BQ-backed guard so restarts/redeploys can't double-run a night.
                if not repository.has_successful_run_on(today, config.SCHEDULE_TIMEZONE):
                    print(f"pi: scheduler firing nightly scrape for {today}")
                    start_scrape(trigger="scheduled")
        except Exception as e:
            print(f"pi: scheduler tick failed: {e}")
        time.sleep(60)
