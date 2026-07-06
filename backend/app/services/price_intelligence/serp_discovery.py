"""SerpApi-backed product-URL discovery for non-crawlable competitors.

Shopify competitors expose products.json or a products sitemap, so the catalog
crawl surfaces match candidates for free. Competitors with
connector_type='unknown' have no catalog surface at all — for those, this
module issues domain-scoped Google searches (SerpApi, engine=google) for
tracked items still needing discovery, fetches the top organic hits with
PageScraper, and writes pi_product_links rows with source='serp':

  parsed page matches the index (gtin / brand+sku / fuzzy) -> confirmed link
  otherwise -> pending link anchored to the searched item, verified by the
  same post-run match_verifier pass as catalog candidates

Budget discipline (every search is paid): only items inside the discovery
window with no confirmed link are searched (the runner's `needy` list); an
item x competitor pair is skipped once ANY link row exists for it (pending
rows resolve via verification, rejected rows mean the SERP already served us
its best wrong answer); runs stop at SERP_MAX_SEARCHES_PER_RUN. A pair whose
search yields nothing re-tries on later nights until its discovery window
expires. Any search error aborts the whole phase — a bad key or exhausted
plan would otherwise fail every remaining query one by one.
"""
import re
import uuid
from urllib.parse import urlparse

import requests

from . import config, repository
from .connectors import PageScraper
from .matcher import build_match_key, strip_variant_tokens

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


def _domain(url: str) -> str:
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _search(query: str) -> list:
    """One SerpApi Google search -> organic result URLs. SerpApi reports
    'Google hasn't returned any results' as HTTP 200 + an error field, so
    that case is an empty list, not a failure."""
    resp = requests.get(
        SERPAPI_ENDPOINT,
        params={
            "engine": "google",
            "q": query,
            "api_key": config.SERPAPI_API_KEY,
            "num": 10,
            "gl": config.SERP_GL,
            "hl": config.SERP_HL,
        },
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"SerpApi {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("error"):
        return []
    return [r["link"] for r in data.get("organic_results", []) if r.get("link")]


def _build_query(domain: str, item: dict) -> str:
    """site:-scoped query at model grain: quoted brand + matrix description
    (or the variant-token-stripped title), brand deduped out of the model text."""
    model = (
        item.get("matrix_description")
        or strip_variant_tokens(item.get("title") or "")
        or item.get("title")
        or ""
    ).strip()
    brand = (item.get("brand") or "").strip()
    if brand:
        model = re.sub(re.escape(brand), "", model, flags=re.IGNORECASE).strip()
        return f'site:{domain} "{brand}" {model}'.strip()
    return f"site:{domain} {model}".strip() if model else ""


def discover(needy_item_ids: list, competitors: list, index,
             existing_link_keys: set) -> dict:
    """Searches needy item x unknown-connector competitor pairs and inserts the
    resulting links directly (paid finds are never subject to the catalog
    candidate cap). Shares existing_link_keys with the runner so the two
    proposal paths can't double-write a match_key."""
    if not config.SERPAPI_API_KEY:
        print("pi: SERPAPI_API_KEY not set; skipping serp discovery")
        return {"skipped": "no api key"}
    targets = [
        c for c in competitors
        if c.get("enabled") and c.get("connector_type") == "unknown"
    ]
    if not targets:
        return {"skipped": "no unknown-connector competitors"}

    all_links = repository.get_product_links(limit=5000)
    linked_pairs = {
        (str(l["item_id"]), l.get("competitor_id"))
        for l in all_links if l.get("item_id")
    }

    stats = {"searches": 0, "pages_fetched": 0, "confirmed": 0, "pending": 0,
             "skipped_pairs": 0}
    scraper = PageScraper()
    rows = []
    per_competitor = {}
    now = repository.utcnow_iso()
    aborted = None

    for item_id in needy_item_ids:
        if aborted or stats["searches"] >= config.SERP_MAX_SEARCHES_PER_RUN:
            break
        item = index.items.get(str(item_id))
        if not item:
            continue
        for comp in targets:
            if stats["searches"] >= config.SERP_MAX_SEARCHES_PER_RUN:
                break
            cid = comp["competitor_id"]
            if (str(item_id), cid) in linked_pairs:
                stats["skipped_pairs"] += 1
                continue
            domain = _domain(comp["base_url"])
            query = _build_query(domain, item)
            if not query:
                continue
            try:
                stats["searches"] += 1
                result_urls = _search(query)
            except Exception as e:
                aborted = str(e)[:200]
                print(f"pi: serp search failed ({query!r}): {e}")
                break
            per_competitor[cid] = per_competitor.get(cid, 0)
            on_domain = [
                u for u in result_urls
                if _domain(u) == domain or _domain(u).endswith("." + domain)
            ]
            for url in on_domain[:config.SERP_RESULTS_PER_SEARCH]:
                parsed = scraper.fetch(url)
                stats["pages_fetched"] += 1
                if not parsed or parsed.get("price") is None:
                    continue
                parsed["url"] = url
                match_key = build_match_key(cid, parsed)
                if match_key in existing_link_keys:
                    continue
                existing_link_keys.add(match_key)
                matched_id, method, confidence, _cand = index.match(parsed, match_key)
                target_id = str(matched_id or item_id)
                # One link per (item, competitor): the index can fuzzy-match a
                # different result of the same search onto the same item.
                if (target_id, cid) in linked_pairs:
                    continue
                target_item = index.items.get(target_id) or item
                rows.append({
                    "link_id": str(uuid.uuid4()),
                    "item_id": target_id,
                    "competitor_id": cid,
                    "match_key": match_key,
                    "competitor_url": url,
                    "competitor_sku": parsed.get("sku"),
                    "competitor_title": parsed.get("title"),
                    "gtin": parsed.get("gtin"),
                    "level": "variant" if matched_id
                             else ("model" if item.get("item_matrix_id") else "variant"),
                    "status": "confirmed" if matched_id else "pending",
                    "source": "serp",
                    "confidence": confidence if matched_id else None,
                    "fuzzy_score": None,
                    "llm_verdict": None,
                    "llm_reason": None,
                    "our_price": target_item.get("current_retail"),
                    "their_price": parsed.get("price"),
                    "decided_by": None,
                    "created_at": now,
                    "updated_at": now,
                })
                linked_pairs.add((target_id, cid))
                per_competitor[cid] += 1
                stats["confirmed" if matched_id else "pending"] += 1

    repository.insert_product_links(rows)
    for cid, n in per_competitor.items():
        repository.mark_competitor_scraped(cid, f"serp discovery ({n} links)")
    if aborted:
        stats["aborted"] = aborted
    return stats
