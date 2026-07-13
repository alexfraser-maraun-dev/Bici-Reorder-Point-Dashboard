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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, repository
from .connectors import PageScraper, build_connector, detect_connector_type
from .matcher import MatchIndex, build_match_key, _identifying_sku

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


def start_scrape(trigger: str = "manual", full: bool = False):
    """Starts a run on a daemon thread. Returns run_id, or None if one is running.

    full=True forces a whole-catalog crawl; otherwise the run is targeted
    (confirmed-link URLs only) unless recently activated items still need
    catalog discovery."""
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
            "links_done": 0, "links_total": 0,
            "observations": 0, "changes": 0, "errors": [],
        })
    threading.Thread(target=_run, args=(run_id, trigger, full), daemon=True).start()
    return run_id


def _needs_catalog_discovery(item_lookup: dict, confirmed_links: list, urls: list) -> list:
    """Active items still inside the discovery window with no confirmed link and
    no linked tracked URL — the trigger for a full catalog crawl."""
    linked_items = {str(l["item_id"]) for l in confirmed_links if l.get("item_id")}
    linked_items |= {str(u["item_id"]) for u in urls if u.get("item_id")}
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.DISCOVERY_DAYS)
    needy = []
    for item_id, row in item_lookup.items():
        if item_id in linked_items:
            continue
        activated = row.get("activated_at")
        if activated is None:
            needy.append(item_id)  # legacy row without a timestamp: keep hunting
            continue
        if isinstance(activated, str):
            try:
                activated = datetime.fromisoformat(activated.replace("Z", "+00:00"))
            except ValueError:
                needy.append(item_id)
                continue
        if activated.tzinfo is None:
            activated = activated.replace(tzinfo=timezone.utc)
        if activated >= cutoff:
            needy.append(item_id)
    return needy


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _item_display_title(item: dict) -> str:
    """Our title plus variant attributes — matrix variants share the display
    name ("Continental Grand Prix 5000 Tire"), so events would otherwise be
    indistinguishable across sizes."""
    title = (item or {}).get("title")
    if not title:
        return None
    attrs = [
        str(a).strip()
        for a in (item.get("attribute_1"), item.get("attribute_2"), item.get("attribute_3"))
        if a and str(a).strip()
    ]
    return f"{title} ({' / '.join(attrs)})" if attrs else title


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
        "item_title": _item_display_title(item) or obs.get("competitor_title"),
        "item_brand": (item or {}).get("brand"),
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

    # MAP intel: flag a competitor advertising below our MAP floor, on the
    # transition only (first sighting below, or a move from >= floor to < floor).
    # MAP == our retail price for tagged items (map_price override wins if set).
    map_floor = (item.get("map_price") or item.get("current_retail")) if item and item.get("is_map") else None
    if map_floor and new_price is not None:
        map_floor = float(map_floor)
        was_below = old_price is not None and old_price < map_floor - PRICE_EPSILON
        is_below = new_price < map_floor - PRICE_EPSILON
        if is_below and not was_below:
            event("map_violation", old_price, new_price)

    # Undercut intel: a competitor crossed below OUR retail on an item we carry
    # — we've lost the price. Same transition-only guard as MAP so a competitor
    # who sits below us every night doesn't re-alert; a newly discovered listing
    # already below us alerts once. Skipped for MAP-tagged items, where the
    # map_violation above is the sharper signal for the same crossing.
    our_price = item.get("current_retail") if item and not item.get("is_map") else None
    if our_price and new_price is not None:
        our_price = float(our_price)
        was_below = old_price is not None and old_price < our_price - PRICE_EPSILON
        is_below = new_price < our_price - PRICE_EPSILON
        if is_below and not was_below:
            event("undercut", old_price, new_price)

    # Update the in-memory prev map so re-observations within one run don't
    # duplicate events.
    prev_map[key] = {"price": new_price, "in_stock": obs.get("in_stock")}
    return events


def _run(run_id: str, trigger: str, force_full: bool = False):
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

        # Record our own price change-points (after the refresh updates
        # current_retail) so the price-history chart has an "our price" line.
        repository.record_our_price_snapshot()

        index = MatchIndex.load()
        item_lookup = index.items
        prev_map = repository.get_latest_observation_map()
        brand_tokens = sorted({(r.get("brand") or "") for r in item_lookup.values()} - {""})

        # Candidate links for LLM/human verification. Collection is generous
        # (competitor catalogs surface thousands of same-brand near-misses);
        # the flush step keeps only the best-scoring few per item, up to the
        # per-run pair cap, so the LLM sees quality candidates first. Keys that
        # already have a row (incl. rejections) are never re-proposed.
        MAX_COLLECTED_CANDIDATES = 5000
        CANDIDATES_PER_ITEM = 5
        existing_link_keys = repository.get_link_match_keys()
        pending_links, gtin_links = [], []

        def _propose_link(match_key, candidate, competitor_id, product, item_id=None,
                          method=None, confidence=None):
            """Buffers a link row. Confirmed only for auto-confirmed matches
            (item_id set: gtin/attr_exact with PI_AUTO_CONFIRM on); everything
            else — including gtin/brand+SKU/fuzzy hits in manual-review mode —
            is a pending proposal for the Matching queue. Skips keys already
            decided/proposed and (item, competitor) pairs that already hold a
            confirmed link: a decided pair never gets further proposals."""
            if match_key in existing_link_keys:
                return
            method = method or (candidate or {}).get("method")
            confidence = confidence if confidence is not None \
                else (candidate or {}).get("confidence")
            target_id = str(item_id or candidate["item_id"])
            if (target_id, competitor_id) in confirmed_pairs:
                return
            existing_link_keys.add(match_key)
            is_confirmed = item_id is not None and method in ("gtin", "attr_exact")
            if not is_confirmed and len(pending_links) >= MAX_COLLECTED_CANDIDATES:
                return
            item = item_lookup.get(target_id) or {}
            row = {
                "link_id": str(uuid.uuid4()),
                "item_id": target_id,
                "competitor_id": competitor_id,
                "match_key": match_key,
                "competitor_url": product.get("url"),
                "competitor_sku": product.get("sku"),
                "competitor_title": product.get("title"),
                "gtin": product.get("gtin"),
                "level": "variant" if is_confirmed else (candidate or {}).get("level", "variant"),
                "status": "confirmed" if is_confirmed else "pending",
                "source": ("gtin" if method == "gtin"
                           else "attr" if method in ("attr", "attr_exact")
                           else "llm"),
                "confidence": confidence,
                "fuzzy_score": None if is_confirmed else (candidate or {}).get("fuzzy_score"),
                "llm_verdict": None,
                "llm_reason": None,
                "our_price": item.get("current_retail"),
                "their_price": product.get("price"),
                "decided_by": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            if is_confirmed:
                confirmed_pairs.add((target_id, competitor_id))
                gtin_links.append(row)
            else:
                pending_links.append(row)

        competitors = [c for c in repository.get_competitors() if c.get("enabled")]
        urls = repository.get_tracked_urls(include_disabled=False)
        _set_status(competitors_total=len(competitors), urls_total=len(urls))

        # --- full catalog crawl vs targeted (confirmed URLs only)? ----------
        confirmed_links = repository.get_product_links(status="confirmed", limit=5000)
        # Decided (item, competitor) pairs: _propose_link never proposes into
        # a pair that already holds a confirmed link.
        confirmed_pairs = {
            (str(l["item_id"]), l.get("competitor_id"))
            for l in confirmed_links if l.get("item_id")
        }
        needy = _needs_catalog_discovery(item_lookup, confirmed_links, urls)
        full_scan = force_full or bool(needy) or not (confirmed_links or urls)
        print(f"pi: run mode = {'full catalog' if full_scan else 'targeted'} "
              f"(forced={force_full}, items needing discovery={len(needy)}, "
              f"confirmed links={len(confirmed_links)})")

        # --- catalog scrape, one competitor at a time -----------------------
        for competitor in competitors if full_scan else []:
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
                obs_before = counters["observations"]
                products_seen = 0
                for product in connector.iter_products():
                    products_seen += 1
                    match_key = build_match_key(cid, product)
                    item_id, method, confidence, candidate = index.match(product, match_key)
                    observed_at = _now_iso()
                    # Identity for price diffing over time. Route the SKU through
                    # _identifying_sku so Shopify's blank-SKU placeholders
                    # ("Default Title", etc.) fall through to the unique URL —
                    # otherwise a store's whole SKU-less catalog collapses onto one
                    # diff_key and cross-contaminates prices (phantom moves).
                    diff_key = (
                        f"cat:{cid}:"
                        f"{product.get('gtin') or _identifying_sku(product.get('sku')) or product.get('url') or product.get('title')}"
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
                    if method == "gtin":
                        # Persist the exact match as a confirmed link so future
                        # runs match even if the barcode later disappears.
                        _propose_link(match_key, None, cid, product,
                                      item_id=item_id, method="gtin", confidence=1.0)
                    elif method == "attr_exact":
                        # Color+size matched exactly one tracked variant and
                        # PI_ATTR_AUTO_CONFIRM is on — persist as a confirmed link.
                        _propose_link(match_key, None, cid, product,
                                      item_id=item_id, method="attr_exact", confidence=confidence)
                    elif item_id is None and candidate is not None:
                        _propose_link(match_key, candidate, cid, product)
                    # Persist rows that matched, carry a GTIN we may match later,
                    # or belong to a tracked brand (so misses are diagnosable and
                    # verifiable) — storing entire foreign catalogs is noise.
                    if (item_id is None and not product.get("gtin")
                            and not index.has_brand(product.get("brand"))):
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
                # Distinguish a healthy crawl from silent failures: a connector that
                # yields nothing (bad sitemap / blocked) vs one that yields products
                # but persists none (no tracked-brand overlap) — both looked like
                # plain "success" before and hid, e.g., the Oak Bay 0-result crawl.
                obs_added = counters["observations"] - obs_before
                if products_seen == 0:
                    comp_status = "success_no_products"
                elif obs_added == 0:
                    comp_status = "success_no_matches"
            except Exception as e:
                comp_status = f"failed: {e}"
                errors.append(f"{competitor['name']}: {e}")
                print(f"pi: competitor {competitor['name']} failed: {e}")
            repository.mark_competitor_scraped(cid, comp_status[:200])
            counters["competitors_done"] += 1
            _set_status(competitors_done=counters["competitors_done"],
                        observations=counters["observations"], changes=counters["changes"])

        # --- SERP discovery: competitors with no crawlable catalog -----------
        # Runs whenever items need discovery (same trigger as the full crawl);
        # inserts links directly, so this run's verification pass sees them.
        serp_pending = 0
        if config.SERP_ENABLED and needy:
            _set_status(phase="SERP discovery")
            try:
                from . import serp_discovery
                serp_stats = serp_discovery.discover(
                    needy, competitors, index, existing_link_keys
                )
                print(f"pi: serp discovery: {serp_stats}")
                _set_status(serp=serp_stats)
                serp_pending = serp_stats.get("pending", 0)
                if serp_stats.get("aborted"):
                    errors.append(f"serp discovery aborted: {serp_stats['aborted']}")
            except Exception as e:
                errors.append(f"serp discovery: {e}")
                print(f"pi: serp discovery failed: {e}")

        # --- targeted mode: re-check each confirmed link's URL ---------------
        scraper = PageScraper()
        competitor_names = {c["competitor_id"]: c["name"] for c in repository.get_competitors()}
        if not full_scan:
            # Tracked-URL rows are scraped in their own phase below; skip links
            # that would fetch the same page twice.
            tracked_url_set = {u["url"] for u in urls}
            link_targets = [
                l for l in confirmed_links
                if l.get("competitor_url") and l.get("item_id")
                and str(l["item_id"]) in item_lookup
                and l["competitor_url"] not in tracked_url_set
            ]
            _set_status(phase="scraping confirmed links", links_total=len(link_targets))
            obs_buffer, event_buffer = [], []
            links_done, per_competitor = 0, {}
            for link in link_targets:
                try:
                    # Pass the known SKU so Shopify variant resolution works even for
                    # older links stored as the base (non-?variant) product URL.
                    parsed = scraper.fetch(link["competitor_url"], sku=link.get("competitor_sku"))
                    if parsed is not None and parsed.get("price") is not None:
                        obs = {
                            "observed_at": _now_iso(),
                            "run_id": run_id,
                            "source": "link",
                            "diff_key": f"link:{link['link_id']}",
                            "competitor_id": link.get("competitor_id"),
                            "url": link["competitor_url"],
                            "competitor_title": parsed.get("title") or link.get("competitor_title"),
                            "competitor_sku": parsed.get("sku") or link.get("competitor_sku"),
                            "gtin": parsed.get("gtin") or link.get("gtin"),
                            "match_item_id": str(link["item_id"]),
                            "match_method": "link",
                            "match_confidence": link.get("confidence"),
                            "price": parsed.get("price"),
                            "compare_at_price": parsed.get("compare_at_price"),
                            "currency": parsed.get("currency"),
                            "in_stock": parsed.get("in_stock"),
                        }
                        obs_buffer.append(obs)
                        name = (competitor_names.get(link.get("competitor_id"))
                                or urlparse_domain(link["competitor_url"]))
                        event_buffer.extend(_build_events(prev_map, obs, name, item_lookup))
                        cid = link.get("competitor_id")
                        if cid:
                            per_competitor[cid] = per_competitor.get(cid, 0) + 1
                except Exception as e:
                    errors.append(f"link {link['competitor_url']}: {e}")
                links_done += 1
                _set_status(links_done=links_done)
            repository.load_rows(repository.T_OBSERVATIONS, obs_buffer)
            counters["observations"] += len(obs_buffer)
            repository.load_rows(repository.T_EVENTS, event_buffer)
            counters["changes"] += sum(1 for e in event_buffer if not e["acknowledged"])
            _set_status(observations=counters["observations"], changes=counters["changes"])
            for cid, n in per_competitor.items():
                repository.mark_competitor_scraped(cid, f"targeted ({n} links)")

        # --- tracked URLs (feature a) ---------------------------------------
        _set_status(phase="scraping tracked URLs")
        obs_buffer, event_buffer = [], []
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
                        item_id, method, confidence, _cand = index.match(
                            parsed, f"url:{url}"
                        )
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

        # --- match verification: flush candidate links, then LLM-verify ------
        _set_status(phase="verifying matches")
        try:
            # Best candidates first: exact-signal proposals (gtin 1.0, attr 0.97,
            # brand+SKU 0.9) outrank fuzzy ones, at most a handful per item,
            # capped at the per-run LLM budget. The rest are re-proposed on
            # later nights (their match_keys were never written).
            pending_links.sort(
                key=lambda r: r.get("confidence") or (r.get("fuzzy_score") or 0) / 100,
                reverse=True,
            )
            selected, per_item = [], {}
            for row in pending_links:
                if len(selected) >= config.MATCH_MAX_PAIRS_PER_RUN:
                    break
                n = per_item.get(row["item_id"], 0)
                if n >= CANDIDATES_PER_ITEM:
                    continue
                per_item[row["item_id"]] = n + 1
                selected.append(row)
            repository.insert_product_links(gtin_links + selected)
            # Always run: pending-unverified rows can come from earlier runs
            # (e.g. demoted links), not just this run's candidates. A clean
            # backlog costs one query.
            from . import match_verifier
            stats = match_verifier.verify_candidates()
            print(f"pi: match verification: {stats}")
        except Exception as e:
            errors.append(f"match verification: {e}")
            print(f"pi: match verification failed: {e}")

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
        # Slack dispatch: best-effort, in finally so a health alert still fires
        # even when the run threw before the digest stage.
        try:
            from . import notify
            notify.dispatch_run(run_id, status, counters, errors)
        except Exception as e:
            print(f"pi: slack dispatch failed: {e}")
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
