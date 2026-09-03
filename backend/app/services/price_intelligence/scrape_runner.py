"""Scrape run lifecycle + the in-process nightly scheduler.

A run is triggered by POST /scrape or the scheduler, acquires a non-blocking lock
(409 if one is already going), and does all work on a daemon thread so the HTTP
request returns immediately (the 120s gunicorn timeout never applies). Live
progress is kept in an in-memory status dict the UI polls; the durable record is
one pi_scrape_runs row, opened at the start and updated at the end so that a run
killed mid-flight (OOM, redeploy) still leaves a marker the scheduler can see.

Memory discipline (512MB budget shared with the API): competitors are scraped
strictly sequentially, catalogs stream through generators, and every phase flushes
its observation, event and confirmed-link buffers to BigQuery as they fill (capped
at FLUSH_ROWS) rather than at the end, so peak state is one page of products plus
the small diff/match dicts. The match structures are released before the LLM
verification phase, which loads its own copy.
"""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, repository
from .connectors import (ItemTokenIndex, PageScraper, build_connector,
                         detect_connector_type)
from . import matcher
from .matcher import MatchIndex, build_match_key, _identifying_sku

_scrape_lock = threading.Lock()
_status_lock = threading.Lock()
_status = {"status": "idle"}

PRICE_EPSILON = 0.005

# Ranking multiplier for a candidate at a store where the model is already
# confirmed on a different page (matcher's `off_page`). Demotion, not exclusion.
OFF_PAGE_RANK_PENALTY = 0.5

# Longest a deployment may go without sweeping competitor catalogs. Discovery is
# otherwise driven entirely by `needy` (items activated recently and still
# unlinked), which empties as matching improves — and an empty needy list would
# quietly end all discovery of models we have never matched.
CATALOG_SWEEP_MAX_DAYS = 7


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _set_status(**fields):
    # Phase changes are the natural sampling points for memory: they bracket the run's
    # distinct working sets, so a climb can be attributed to a phase rather than
    # reconstructed from the CPU chart afterwards.
    if "phase" in fields:
        from app.services.memory_probe import log_rss
        fields["rss_mb"] = log_rss(f"scrape phase={fields['phase']}")
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
            "variants_extracted": 0, "exact_resolutions": 0,
            "ambiguous_pages": 0, "ranges_excluded": 0,
            "fallback_failures": 0, "extractor_methods": {},
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
    # A range is useful evidence, but is not a price for the matched item.
    if obs.get("price_scope") == "range":
        return []
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

    # MAP/undercut compare the listing against OUR CAD retail, so a listing
    # that self-reports another currency must not fire them (a USD price reads
    # ~35% cheaper than reality). Unreported currency → CAD, like the market
    # SQL's sql_cad_only guard. Price drop/increase above compare the listing
    # to itself, so they stay currency-safe without this.
    foreign_currency = ((obs.get("currency") or "CAD").strip().upper() or "CAD") != "CAD"

    # MAP intel: flag a competitor advertising below our MAP floor, on the
    # transition only (first sighting below, or a move from >= floor to < floor).
    # MAP == our retail price for tagged items (map_price override wins if set).
    map_floor = (item.get("map_price") or item.get("current_retail")) if item and item.get("is_map") else None
    if map_floor and new_price is not None and not foreign_currency:
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
    if our_price and new_price is not None and not foreign_currency:
        our_price = float(our_price)
        was_below = old_price is not None and old_price < our_price - PRICE_EPSILON
        is_below = new_price < our_price - PRICE_EPSILON
        if is_below and not was_below:
            event("undercut", old_price, new_price)

    # Update the in-memory prev map so re-observations within one run don't
    # duplicate events.
    prev_map[key] = {"price": new_price, "in_stock": obs.get("in_stock")}
    return events


# Colour AND size both agreeing on a tracked variant (matcher.attribute_match_score
# gives +1 per matching dimension, and only an exact colour set scores the full +1).
SIBLING_EXACT_SCORE = 2.0


def _sibling_matches(listings: list, siblings: list, taken_ids: set):
    """Pairs a confirmed page's OTHER listings with our unlinked variants of the
    same model.

    A confirmed link is a human's assertion that this store sells this model on
    this page, and the page's own markup already enumerates every variant on it —
    `PageScraper.listings` returns them all and `resolve` throws all but ours
    away. Reading the rest costs no extra request and is the only evidence that
    settles which variant is which on storefronts that publish no SKU.

    Yields (listing, item_id, method) where method is 'gtin' for a barcode
    equality (an identity) or 'sibling' for an exact colour+size agreement (a
    strong inference, so it goes to the review queue). Anything less is skipped:
    this path exists to complete a known model, not to guess at new ones."""
    by_upc = {}
    for row in siblings:
        upc = matcher.normalize_upc(row.get("upc_normalized"))
        if upc:
            by_upc[upc] = row
    for listing in listings:
        if listing.get("price") is None or listing.get("price_scope") == "range":
            continue
        gtin = matcher.normalize_upc(listing.get("gtin"))
        hit = by_upc.get(gtin) if gtin else None
        if hit is not None and str(hit["item_id"]) not in taken_ids:
            yield listing, str(hit["item_id"]), "gtin"
            continue
        options = (listing.get("variant_options")
                   or matcher.parse_variant_options(listing.get("title")))
        if not options:
            continue
        scored = [
            (matcher.attribute_match_score(
                options, [s.get("attribute_1"), s.get("attribute_2"),
                          s.get("attribute_3")]), str(s["item_id"]))
            for s in siblings
        ]
        exact = [item_id for score, item_id in scored if score >= SIBLING_EXACT_SCORE]
        # Exactly one variant may claim a listing. Several claimants means the
        # colourway or size is ambiguous on our side, and guessing there is how
        # one item ends up carrying every variant of a model.
        if len(exact) == 1 and exact[0] not in taken_ids:
            yield listing, exact[0], "sibling"


class LinkProposer:
    """Buffers proposed pi_product_links rows and enforces who may propose what.

    Module level rather than a closure in _run so the phases that use it can be
    driven on their own — the matrix fan-out in particular is worth running (and
    replaying) without a whole scrape around it.
    """

    MAX_COLLECTED_CANDIDATES = 5000

    def __init__(self, item_lookup: dict, existing_link_keys: set,
                 confirmed_pairs: set):
        self.item_lookup = item_lookup
        self.existing_link_keys = existing_link_keys
        self.confirmed_pairs = confirmed_pairs
        self.pending_links = []
        self.gtin_links = []
        # Colour+size matches harvested off pages already confirmed to sell the
        # model. Kept separate because they bypass the LLM budget and are written
        # in full rather than sampled (see _fan_out_matrix).
        self.sibling_links = []
        # Totals across every flush, for the end-of-run log line — the buffers
        # themselves are emptied as they are written.
        self.gtin_written = 0
        self.sibling_written = 0

    def flush_confirmed(self, force: bool = False):
        """Persists buffered identity matches (gtin + sibling) and empties the buffers.

        Unlike pending_links these are deliberately uncapped: they are confirmed matches
        rather than candidates competing for a review budget, so every one must be
        written (see the note at the end-of-run write). Uncapped *and* held to the end of
        the run, though, made them the one run-lifetime accumulator with no ceiling at
        all, so they go out as they build instead.

        Safe to batch: insert_product_links is an insert-only MERGE on match_key, and
        existing_link_keys already stops a key being proposed twice in one run.
        """
        buffered = len(self.gtin_links) + len(self.sibling_links)
        if not buffered or (not force and buffered < config.FLUSH_ROWS):
            return
        repository.insert_product_links(self.gtin_links + self.sibling_links)
        self.gtin_written += len(self.gtin_links)
        self.sibling_written += len(self.sibling_links)
        del self.gtin_links[:]
        del self.sibling_links[:]

    def propose(self, match_key, candidate, competitor_id, product, item_id=None,
                method=None, confidence=None):
        """Buffers a link row and returns it, or None when it isn't allowed.

        Confirmed only for identity matches (item_id set: gtin/attr_exact under
        their auto-confirm settings); everything else — including gtin/brand+SKU/
        fuzzy hits in manual-review mode — is a pending proposal for the Matching
        queue. Skips keys already decided/proposed and (item, competitor) pairs
        that already hold a confirmed link: a decided pair never gets further
        proposals."""
        if match_key in self.existing_link_keys:
            return None
        method = method or (candidate or {}).get("method")
        confidence = confidence if confidence is not None \
            else (candidate or {}).get("confidence")
        target_id = str(item_id or candidate["item_id"])
        if (target_id, competitor_id) in self.confirmed_pairs:
            return None
        self.existing_link_keys.add(match_key)
        is_confirmed = item_id is not None and method in ("gtin", "attr_exact")
        # A sibling proposal comes off a page a human already confirmed sells this
        # model, and matched a tracked variant on colour AND size. It is not a
        # fuzzy guess, so it neither competes for the candidate cap nor spends
        # LLM budget.
        if (not is_confirmed and method != "sibling"
                and len(self.pending_links) >= self.MAX_COLLECTED_CANDIDATES):
            return None
        item = self.item_lookup.get(target_id) or {}
        level = "variant" if is_confirmed else (candidate or {}).get("level", "variant")
        # A model-grain proposal identifies the model, not the variant: the page's
        # variant identifiers belong to whichever listing happened to propose
        # first, so storing one would assert a variant we haven't established
        # (and would later be re-scraped as though it were ours).
        model_grain = not is_confirmed and level == "model"
        if (candidate or {}).get("off_page"):
            # Ranked below clean candidates so the per-item/per-run review budget
            # goes to them first — still eligible, just not ahead.
            base = confidence if confidence is not None \
                else (candidate.get("fuzzy_score") or 0) / 100
            confidence = round(base * OFF_PAGE_RANK_PENALTY, 3)
        row = {
            "link_id": str(uuid.uuid4()),
            "item_id": target_id,
            "competitor_id": competitor_id,
            "match_key": match_key,
            "competitor_url": product.get("url"),
            "competitor_sku": product.get("sku"),
            "competitor_title": product.get("title"),
            "gtin": None if model_grain else product.get("gtin"),
            "variant_id": None if model_grain else product.get("variant_id"),
            "variant_options_json": json.dumps(product.get("variant_options") or []),
            "level": level,
            "status": "confirmed" if is_confirmed else "pending",
            "source": ("gtin" if method == "gtin"
                       else "attr" if method in ("attr", "attr_exact")
                       else "sibling" if method == "sibling"
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
            self.confirmed_pairs.add((target_id, competitor_id))
            self.gtin_links.append(row)
        elif method == "sibling":
            self.sibling_links.append(row)
        else:
            self.pending_links.append(row)
        return row


def _fan_out_matrix(link, page_listings, resolved, index, item_lookup, propose,
                    fanned_pages, run_id, counters) -> list:
    """Links our other variants of this model to the other variants on this page.

    Variant coverage, not model discovery, is where breadth was being lost: 88%
    of tracked models had a confirmed link somewhere, but only 43% of variants
    did — 1 of 27 POC Cytal variants at a store whose page lists all 27 with a
    barcode each. The catalog crawl can't fix that on its own (every variant of a
    SKU-less page shares one URL), and it costs a page fetch we're already making.

    Returns observation rows for the variants confirmed by barcode, so their
    prices land tonight rather than on the next run."""
    item = item_lookup.get(str(link.get("item_id") or "")) or {}
    matrix_id = item.get("item_matrix_id")
    cid = link.get("competitor_id")
    if not matrix_id or not cid or len(page_listings) < 2:
        return []
    page = matcher.page_key(link.get("competitor_url"))
    if not page or (cid, page) in fanned_pages:
        return []
    fanned_pages.add((cid, page))

    siblings = [s for s in index.variants_by_matrix.get(str(matrix_id), [])
                if str(s["item_id"]) != str(link["item_id"])]
    if not siblings:
        return []
    # Claims made while walking this page. Variants already linked at this store
    # are blocked by _propose_link's confirmed_pairs guard, which keeps the
    # one-confirmed-link-per-(item, store) invariant in one place.
    taken = set()
    # Identify the link's own listing by its match_key, not its URL: on a
    # SmartEtailing page all 27 variants share one URL, so a URL test skips the
    # entire page instead of one row.
    resolved_key = build_match_key(cid, resolved) if resolved else None

    observations = []
    for listing, item_id, method in _sibling_matches(page_listings, siblings, taken):
        match_key = build_match_key(cid, listing)
        if match_key == resolved_key:
            continue  # this is the link's own variant
        row = propose(match_key, None, cid, listing, item_id=item_id,
                      method=method,
                      confidence=1.0 if method == "gtin" else None)
        if row is None:
            continue
        taken.add(item_id)
        counters["siblings_linked"] = counters.get("siblings_linked", 0) + 1
        if row["status"] != "confirmed":
            continue
        observations.append({
            "observed_at": _now_iso(), "run_id": run_id, "source": "link",
            "diff_key": f"link:{row['link_id']}", "competitor_id": cid,
            "url": listing.get("url") or link.get("competitor_url"),
            "competitor_title": listing.get("title"),
            "competitor_sku": listing.get("sku"), "gtin": listing.get("gtin"),
            "match_item_id": item_id, "match_method": "link",
            "match_confidence": 1.0, "price": listing.get("price"),
            "compare_at_price": listing.get("compare_at_price"),
            "currency": listing.get("currency"), "in_stock": listing.get("in_stock"),
            **_extraction_fields(listing),
        })
    return observations


def _extraction_fields(product: dict) -> dict:
    return {
        "extraction_method": product.get("extraction_method"),
        "price_scope": product.get("price_scope") or "variant",
        "variant_id": product.get("variant_id"),
        "variant_options_json": json.dumps(product.get("variant_options") or []),
        "price_low": product.get("price_low"),
        "price_high": product.get("price_high"),
    }


def _url_diff_key(url: str, product: dict) -> str:
    identity = (product.get("variant_id") or product.get("sku") or product.get("gtin")
                or "unresolved")
    return f"url:{url}:{identity}"


def _count_extraction(counters: dict, product: dict):
    counters["variants_extracted"] += int(product.get("price_scope") == "variant")
    status = product.get("_resolution_status")
    counters["exact_resolutions"] += int(status == "exact")
    counters["ambiguous_pages"] += int(status == "ambiguous")
    counters["ranges_excluded"] += int(product.get("price_scope") == "range")
    method = product.get("extraction_method") or "unknown"
    counters["extractor_methods"][method] = counters["extractor_methods"].get(method, 0) + 1


def _flush_buffers(obs_buffer: list, event_buffer: list, counters: dict,
                   force: bool = False):
    """Writes buffered observations and events out, emptying both buffers in place.

    Called mid-loop, not only at phase end. A phase that flushed once at the end held
    every row it produced -- observations *and* the change events derived from them --
    on the heap for its whole duration, which on a long confirmed-link or tracked-URL
    pass is the largest single allocation of the night. The buffers are cleared in place
    so callers keep their references and no rebinding is needed at the call site.

    Observations and events go to different tables and `prev_map` is read once per phase,
    so interleaving the writes does not change what gets recorded.
    """
    if force or len(obs_buffer) >= config.FLUSH_ROWS:
        repository.load_rows(repository.T_OBSERVATIONS, obs_buffer)
        counters["observations"] += len(obs_buffer)
        del obs_buffer[:]
    if force or len(event_buffer) >= config.FLUSH_ROWS:
        repository.load_rows(repository.T_EVENTS, event_buffer)
        counters["changes"] += sum(1 for e in event_buffer if not e["acknowledged"])
        del event_buffer[:]


def _run(run_id: str, trigger: str, force_full: bool = False):
    started_at = _now_iso()
    counters = {
        "competitors_done": 0, "urls_done": 0, "observations": 0, "changes": 0,
        "variants_extracted": 0, "exact_resolutions": 0, "ambiguous_pages": 0,
        "ranges_excluded": 0, "fallback_failures": 0, "extractor_methods": {},
    }
    errors = []
    # Open the run row up front. The end-of-run save in `finally` cannot be relied on as
    # the restart guard: an OOM kill is SIGKILL, so `finally` never runs, no row lands,
    # and has_scheduler_blocking_run_on() then sees nothing on reboot — the scheduler's
    # `due` check spans the rest of the day, so the whole run re-fires within 60s of
    # boot. A row written here closes that loop, and one left at status='running' with
    # no finished_at is an accurate record that the run was killed.
    run_row_opened = False
    try:
        _set_status(phase="preparing")
        repository.ensure_pi_tables()
        try:
            repository.save_scrape_run({
                "run_id": run_id, "started_at": started_at, "finished_at": None,
                "trigger": trigger, "status": "running", "competitors_done": 0,
                "urls_done": 0, "observations_count": 0, "changes_count": 0,
                "error": None, "stats_json": None,
            })
            run_row_opened = True
        except Exception as e:
            # Non-fatal: the run still works, it just loses its restart guard.
            print(f"pi: failed to open run record (restart guard inactive): {e}")

        # Keep the comparison list current before matching against it.
        try:
            from . import seeding
            seeding.refresh_tracked_products(
                trigger=trigger, raise_on_index_error=True,
            )
        except Exception as e:
            errors.append(f"seeding: {e}")
            print(f"pi: seeding failed (continuing with existing list): {e}")

        # Record our own price change-points (after the refresh updates
        # current_retail) so the price-history chart has an "our price" line.
        repository.record_our_price_snapshot()

        index = MatchIndex.load()
        item_lookup = index.items
        # prev_map (for change-event diffing) is loaded per phase/competitor by
        # diff_key prefix — peak memory is one store's listings, never every
        # store's trailing-45-day history. Namespaces ('cat:{cid}:', 'link:',
        # 'url:') don't overlap, so event semantics are unchanged.
        # Slug tokens for every tracked item, computed once. Each competitor
        # then gets targets built from the items it specifically can't price
        # yet — that's what decides which product pages the nightly page budget
        # is spent on (see _HtmlPageCrawler._rank_candidates).
        token_index = ItemTokenIndex(item_lookup)

        # Candidate links for LLM/human verification. Collection is generous
        # (competitor catalogs surface thousands of same-brand near-misses);
        # the flush step keeps only the best-scoring few per item, up to the
        # per-run pair cap, so the LLM sees quality candidates first. Keys that
        # already have a row (incl. rejections) are never re-proposed.
        CANDIDATES_PER_ITEM = 5
        # confirmed_pairs is filled in just below, once confirmed_links is loaded;
        # the proposer holds the same set object, so it sees those additions.
        confirmed_pairs = set()
        proposer = LinkProposer(item_lookup, repository.get_link_match_keys(),
                                confirmed_pairs)
        _propose_link = proposer.propose
        pending_links = proposer.pending_links
        existing_link_keys = proposer.existing_link_keys

        # Synthetic sources (Google benchmark/suggested) are pulled by their own
        # phase below, not crawled — excluded here so they don't inflate
        # competitors_total (leaving progress stuck short of the total) or get
        # handed to SERP discovery.
        competitors = [c for c in repository.get_competitors()
                       if c.get("enabled")
                       and c.get("connector_type") != repository.BENCHMARK_CONNECTOR]
        urls = repository.get_tracked_urls(include_disabled=False)
        _set_status(competitors_total=len(competitors), urls_total=len(urls))

        # --- full catalog crawl vs targeted (confirmed URLs only)? ----------
        confirmed_links = repository.get_product_links(status="confirmed", limit=5000)
        # Decided (item, competitor) pairs: _propose_link never proposes into
        # a pair that already holds a confirmed link.
        # Updated in place, never rebound: the proposer holds this same set.
        confirmed_pairs.update(
            (str(l["item_id"]), l.get("competitor_id"))
            for l in confirmed_links if l.get("item_id")
        )
        needy = _needs_catalog_discovery(item_lookup, confirmed_links, urls)
        # `needy` only counts items activated inside the discovery window, so once
        # everything recent is linked it empties and catalog crawling would stop
        # for good — along with any chance of finding the models we have never
        # matched. The floor keeps a sweep running on its own schedule.
        stale = repository.days_since_last_catalog_crawl()
        full_scan = (force_full or bool(needy) or not (confirmed_links or urls)
                     or (stale is None or stale >= CATALOG_SWEEP_MAX_DAYS))
        print(f"pi: run mode = {'full catalog' if full_scan else 'targeted'} "
              f"(forced={force_full}, items needing discovery={len(needy)}, "
              f"confirmed links={len(confirmed_links)}, "
              f"days since last catalog crawl={stale})")

        # --- catalog scrape, one competitor at a time -----------------------
        # Competitors whose catalog actually got crawled this run — the
        # confirmed-link phase below re-checks links on everyone else.
        crawled_competitor_ids = set()
        for competitor in competitors if full_scan else []:
            cid = competitor["competitor_id"]
            _set_status(phase=f"scraping {competitor['name']}")
            comp_status = "success"
            # None = leave the stored cursor untouched (a failed crawl retries
            # the same slice); set after a completed iteration below.
            crawl_state = None
            try:
                if not competitor.get("connector_type") or competitor["connector_type"] == "unknown":
                    detected = detect_connector_type(competitor["base_url"])
                    competitor["connector_type"] = detected
                    repository.upsert_competitor(competitor)
                try:
                    cursor = int(json.loads(
                        competitor.get("crawl_state_json") or "{}").get("cursor") or 0)
                except (TypeError, ValueError):
                    cursor = 0
                # Items we still can't price *on this competitor* are what the
                # crawl hunts: their model tokens promote candidate URLs ahead
                # of the alphabetical sweep. Items already linked here are
                # re-checked by URL in the confirmed-link phase instead.
                targets = token_index.targets_for(
                    {str(i) for i in item_lookup if (str(i), cid) not in confirmed_pairs})
                connector = build_connector(competitor, brand_tokens=targets,
                                            cursor=cursor)
                if connector is None:
                    repository.mark_competitor_scraped(cid, "skipped_no_connector")
                    counters["competitors_done"] += 1
                    _set_status(competitors_done=counters["competitors_done"])
                    continue
                crawled_competitor_ids.add(cid)
                # This store's slice of the diff map only — see the note at the
                # top of the run about per-phase prev_map loading.
                prev_map = repository.get_latest_observation_map(
                    diff_key_prefix=f"cat:{cid}:")

                obs_buffer, event_buffer = [], []
                obs_before = counters["observations"]
                products_seen = 0
                for product in connector.iter_products():
                    products_seen += 1
                    _count_extraction(counters, product)
                    match_key = build_match_key(cid, product)
                    item_id, method, confidence, candidate = index.match(
                        product, match_key, competitor_id=cid)
                    if item_id is not None and product.get("price_scope") == "product":
                        item = item_lookup.get(str(item_id)) or {}
                        if item.get("item_matrix_id"):
                            if method in ("gtin", "brand_sku", "attr_exact"):
                                product["price_scope"] = "variant"
                            else:
                                product["price_scope"] = "range"
                                product["price_low"] = product.get("price")
                                product["price_high"] = product.get("price")
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
                        **_extraction_fields(product),
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
                    _flush_buffers(obs_buffer, event_buffer, counters)
                _flush_buffers(obs_buffer, event_buffer, counters, force=True)
                # Distinguish a healthy crawl from silent failures: a connector that
                # yields nothing (bad sitemap / blocked) vs one that yields products
                # but persists none (no tracked-brand overlap) — both looked like
                # plain "success" before and hid, e.g., the Oak Bay 0-result crawl.
                obs_added = counters["observations"] - obs_before
                diagnostics = (connector.diagnostics()
                               if hasattr(connector, "diagnostics") else {})
                if products_seen == 0:
                    # A zero crawl has several very different causes and they
                    # used to be indistinguishable. Name the one we can prove:
                    # the site refusing us outright is not a bad sitemap.
                    fetches = diagnostics.get("fetches") or 0
                    blocked = diagnostics.get("blocked_fetches") or 0
                    comp_status = ("success_blocked" if fetches and blocked >= fetches / 2
                                   else "success_no_products")
                elif obs_added == 0:
                    comp_status = "success_no_matches"
                # Persist the rotation cursor + coverage. cap_hit means the
                # catalog is bigger than the nightly budget: tomorrow resumes
                # at next_cursor instead of re-crawling the same slice.
                if getattr(connector, "cap_hit", False):
                    comp_status += " (cap hit — rotating)"
                crawl_state = {
                    "cursor": getattr(connector, "next_cursor", 0),
                    "pages_done": getattr(connector, "pages_done", 0),
                    "products_seen": getattr(connector, "products_seen", 0),
                    "cap_hit": bool(getattr(connector, "cap_hit", False)),
                    "catalog_exhausted": not getattr(connector, "cap_hit", False),
                    "updated_at": _now_iso(),
                    # Why the crawl went the way it did: HTTP outcomes, how many
                    # URLs survived each filter, and how much of the budget went
                    # to relevance-ranked pages vs the alphabetical sweep.
                    **diagnostics,
                }
            except Exception as e:
                comp_status = f"failed: {e}"
                errors.append(f"{competitor['name']}: {e}")
                print(f"pi: competitor {competitor['name']} failed: {e}")
            repository.mark_competitor_scraped(cid, comp_status[:200], crawl_state)
            proposer.flush_confirmed()
            counters["competitors_done"] += 1
            _set_status(competitors_done=counters["competitors_done"],
                        observations=counters["observations"], changes=counters["changes"],
                        variants_extracted=counters["variants_extracted"],
                        ranges_excluded=counters["ranges_excluded"],
                        extractor_methods=dict(counters["extractor_methods"]))

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

        # --- re-check each confirmed link's URL ------------------------------
        # Targeted mode re-checks every confirmed link. Full-scan mode still
        # re-checks the links whose competitor's catalog was NOT crawled this
        # run (connector-less SERP/manual matches, or no competitor at all) —
        # otherwise those prices silently go stale on every full-scan night.
        scraper = PageScraper()
        competitor_names = {c["competitor_id"]: c["name"] for c in repository.get_competitors()}
        # Tracked-URL rows are scraped in their own phase below; skip links
        # that would fetch the same page twice.
        tracked_url_items = {(u["url"], str(u.get("item_id") or "")) for u in urls}
        link_targets = [
            l for l in confirmed_links
            if l.get("competitor_url") and l.get("item_id")
            and str(l["item_id"]) in item_lookup
            and (l["competitor_url"], str(l.get("item_id") or "")) not in tracked_url_items
            and not (full_scan and l.get("competitor_id")
                     and l["competitor_id"] in crawled_competitor_ids)
        ]
        _set_status(phase="scraping confirmed links", links_total=len(link_targets))
        prev_map = repository.get_latest_observation_map(diff_key_prefix="link:")
        obs_buffer, event_buffer = [], []
        links_done, per_competitor = 0, {}
        # Several confirmed links can point at one page (siblings already matched
        # there); fan it out once.
        fanned_pages = set()
        for link in link_targets:
            try:
                # One fetch, both jobs: our own variant's price, and every other
                # variant the page lists (the matrix fan-out below).
                page_listings = scraper.listings(link["competitor_url"])
                # Pass the known SKU so Shopify variant resolution works even for
                # older links stored as the base (non-?variant) product URL.
                parsed = scraper.resolve(
                    page_listings, link["competitor_url"], sku=link.get("competitor_sku"),
                    gtin=link.get("gtin"), variant_id=link.get("variant_id"),
                    variant_options=json.loads(link.get("variant_options_json") or "[]"),
                )
                obs_buffer.extend(_fan_out_matrix(
                    link, page_listings, parsed, index, item_lookup, _propose_link,
                    fanned_pages, run_id, counters))
                if parsed is not None and parsed.get("price") is not None:
                    if parsed.get("_matched_by") in ("sku", "gtin", "variant_id", "variant_options"):
                        parsed["price_scope"] = "variant"
                    _count_extraction(counters, parsed)
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
                        **_extraction_fields(parsed),
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
            _flush_buffers(obs_buffer, event_buffer, counters)
            _set_status(links_done=links_done)
        _flush_buffers(obs_buffer, event_buffer, counters, force=True)
        _set_status(observations=counters["observations"], changes=counters["changes"])
        for cid, n in per_competitor.items():
            repository.mark_competitor_scraped(cid, f"targeted ({n} links)")

        # --- matrix fan-out --------------------------------------------------
        # The loop above only visits links whose competitor wasn't crawled this
        # run, so on a full-scan night it fans out almost nothing. This phase
        # covers the rest: one representative page per (store, model) that still
        # has unlinked variants there, one fetch each, and it empties itself as
        # coverage fills in. `fanned_pages` is shared, so pages the loop above
        # already handled are never fetched twice.
        fanout_targets = {}
        for l in confirmed_links:
            item = item_lookup.get(str(l.get("item_id") or ""))
            cid, page = l.get("competitor_id"), matcher.page_key(l.get("competitor_url"))
            if not (item and cid and page and item.get("item_matrix_id")):
                continue
            if (cid, page) in fanned_pages or (cid, page) in fanout_targets:
                continue
            siblings = index.variants_by_matrix.get(str(item["item_matrix_id"]), [])
            if not any((str(s["item_id"]), cid) not in confirmed_pairs
                       for s in siblings):
                continue  # every variant of this model is already linked here
            fanout_targets[(cid, page)] = l
        targets = list(fanout_targets.values())[:config.FANOUT_MAX_PAGES]
        _set_status(phase="completing matrix variants", fanout_total=len(targets))
        obs_buffer, event_buffer = [], []
        for link in targets:
            try:
                listings = scraper.listings(link["competitor_url"])
                resolved = scraper.resolve(
                    listings, link["competitor_url"], sku=link.get("competitor_sku"),
                    gtin=link.get("gtin"), variant_id=link.get("variant_id"),
                    variant_options=json.loads(link.get("variant_options_json") or "[]"),
                )
                for obs in _fan_out_matrix(link, listings, resolved, index,
                                           item_lookup, _propose_link, fanned_pages,
                                           run_id, counters):
                    obs_buffer.append(obs)
                    name = (competitor_names.get(link.get("competitor_id"))
                            or urlparse_domain(link["competitor_url"]))
                    event_buffer.extend(
                        _build_events(prev_map, obs, name, item_lookup))
            except Exception as e:
                errors.append(f"fan-out {link['competitor_url']}: {e}")
            _flush_buffers(obs_buffer, event_buffer, counters)
            proposer.flush_confirmed()
        _flush_buffers(obs_buffer, event_buffer, counters, force=True)
        print(f"pi: matrix fan-out: {len(targets)} pages, "
              f"{counters.get('siblings_linked', 0)} variants linked")
        _set_status(observations=counters["observations"], changes=counters["changes"])

        # --- tracked URLs (feature a) ---------------------------------------
        _set_status(phase="scraping tracked URLs")
        prev_map = repository.get_latest_observation_map(diff_key_prefix="url:")
        obs_buffer, event_buffer = [], []
        for url_row in urls:
            url = url_row["url"]
            try:
                parsed = scraper.fetch(
                    url, sku=url_row.get("competitor_sku"),
                    gtin=url_row.get("competitor_gtin"),
                    variant_id=url_row.get("competitor_variant_id"),
                    variant_options=json.loads(url_row.get("variant_options_json") or "[]"),
                )
                if parsed is None or parsed.get("price") is None:
                    counters["fallback_failures"] += 1
                    repository.mark_url_scraped(url_row["url_id"], "no_price")
                else:
                    if parsed.get("_matched_by") in ("sku", "gtin", "variant_id", "variant_options"):
                        parsed["price_scope"] = "variant"
                    _count_extraction(counters, parsed)
                    item_id = str(url_row["item_id"]) if url_row.get("item_id") else None
                    method, confidence = ("manual_url", 1.0) if item_id else (None, 0.0)
                    if not item_id:
                        item_id, method, confidence, _cand = index.match(
                            parsed, f"url:{url}",
                            competitor_id=url_row.get("competitor_id"),
                        )
                    obs = {
                        "observed_at": _now_iso(),
                        "run_id": run_id,
                        "source": "url",
                        "diff_key": _url_diff_key(url, parsed),
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
                        **_extraction_fields(parsed),
                    }
                    obs_buffer.append(obs)
                    name = competitor_names.get(url_row.get("competitor_id")) or urlparse_domain(url)
                    event_buffer.extend(_build_events(prev_map, obs, name, item_lookup))
                    repository.mark_url_scraped(url_row["url_id"], "success")
            except Exception as e:
                errors.append(f"url {url}: {e}")
                repository.mark_url_scraped(url_row["url_id"], f"failed: {e}"[:200])
            counters["urls_done"] += 1
            _flush_buffers(obs_buffer, event_buffer, counters)
            _set_status(urls_done=counters["urls_done"])
        _flush_buffers(obs_buffer, event_buffer, counters, force=True)
        _set_status(observations=counters["observations"], changes=counters["changes"])

        # --- Google Merchant benchmark: pulled, not crawled ------------------
        # Appends this run's market-benchmark / suggested-price snapshot as two
        # synthetic competitors. Never fails the run — an unconfigured or
        # unavailable Merchant Center just means no benchmark rows tonight. Emits
        # no change events by design: these are modelled prices, not shelf prices.
        from . import google_benchmark, settings as pi_settings
        if pi_settings.get("google_benchmark_enabled"):
            _set_status(phase="fetching Google benchmark")
            try:
                gb_stats = google_benchmark.run_benchmark_sync(
                    run_id, observed_at=_now_iso())
                counters["observations"] += gb_stats.get("observations", 0)
                counters["google_benchmark"] = gb_stats
                _set_status(observations=counters["observations"])
                print(f"pi: google benchmark: {gb_stats}")
            except google_benchmark.BenchmarkUnavailable as e:
                counters["google_benchmark"] = {"skipped": str(e)}
                print(f"pi: google benchmark skipped: {e}")
            except Exception as e:
                errors.append(f"google benchmark: {e}")
                counters["google_benchmark"] = {"error": str(e)[:200]}
                print(f"pi: google benchmark failed: {e}")

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
            # gtin/sibling links are written in full and unsampled: each already has
            # an identity match (barcode, or colour+size on a page a human confirmed
            # sells the model), so there is nothing for the sampling to choose between.
            proposer.flush_confirmed(force=True)
            repository.insert_product_links(selected)
            print(f"pi: links written — confirmed {proposer.gtin_written}, "
                  f"sibling {proposer.sibling_written}, candidates {len(selected)} "
                  f"of {len(pending_links)}")
            # Every phase above is done with the in-memory match structures, and
            # verify_candidates() below loads its own copy of the tracked set and the
            # confirmed links. Release ours first so the two never coexist: together
            # they are the largest live allocation of the run, and this is its last
            # phase. Rebound rather than `del`ed so an unbound name cannot turn a
            # memory tidy-up into a skipped verification.
            index = token_index = item_lookup = None
            confirmed_links = link_targets = prev_map = None
            proposer.item_lookup = {}
            del proposer.pending_links[:]

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
        final_row = {
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
            "stats_json": json.dumps({
                k: v for k, v in counters.items()
                if k not in ("competitors_done", "urls_done", "observations", "changes")
            }),
        }
        try:
            # Update the row opened above, so a run stays one row. Only insert here if
            # opening it failed, which would otherwise lose the run record entirely.
            if run_row_opened:
                repository.update_scrape_run(run_id, final_row)
            else:
                repository.save_scrape_run(final_row)
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
                    errors=errors[:20], **{
                        k: v for k, v in counters.items()
                        if k not in ("competitors_done", "urls_done", "observations", "changes")
                    })
        _scrape_lock.release()


def urlparse_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


# ---------------------------------------------------------------------------
# Nightly scheduler (in-process; Render starter tier never idles the process out)
# ---------------------------------------------------------------------------
_scheduler_started = False


def start_scheduler():
    """Starts the nightly-scrape daemon thread once per process. The thread
    always runs; each tick reads the effective enabled/time settings so the
    admin console can enable, disable, or retime the nightly run without a
    redeploy (settings default to the PI_SCHEDULE_* env vars)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def _scheduler_loop():
    from . import settings
    # In-process one-attempt-per-day marker. The BQ guard below is the
    # restart-safe primary; this also holds when BigQuery itself is down (the
    # failed run's row can't be saved, so only this stops a 60s retry storm).
    last_attempt_date = None
    while True:
        try:
            if settings.get("schedule_enabled"):
                tz_name = settings.get("schedule_timezone")
                now = datetime.now(ZoneInfo(tz_name))
                due = (now.hour, now.minute) >= (
                    settings.get("schedule_hour"), settings.get("schedule_minute")
                )
                if due and get_status().get("status") != "running":
                    today = now.strftime("%Y-%m-%d")
                    # BQ-backed guard so restarts/redeploys can't double-run a
                    # night; counts failed scheduled attempts as terminal too.
                    if last_attempt_date != today and \
                            not repository.has_scheduler_blocking_run_on(today, tz_name):
                        last_attempt_date = today
                        print(f"pi: scheduler firing nightly scrape for {today}")
                        start_scrape(trigger="scheduled")
        except Exception as e:
            print(f"pi: scheduler tick failed: {e}")
        time.sleep(60)
