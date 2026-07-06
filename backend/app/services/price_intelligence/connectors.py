"""Competitor scraping connectors.

Tiered, cheapest first:
  1. shopify_json — the store's public /products.json (full catalog, no barcodes on
     most stores, so matching is brand+SKU/fuzzy).
  2. shopify_html — product pages discovered via the products sitemap; JSON-LD on
     the page usually carries real GTINs. Costs one request per product, so the
     crawl is pre-filtered to tracked brands and capped.
  3. serp_discovery.py — SerpApi Google search (site:-scoped) finds product URLs
     on competitors with no crawlable catalog (connector_type='unknown');
     env-gated (PI_SERP_ENABLED + SERPAPI_API_KEY), paid per search.
Plus PageScraper: a single-URL scraper for user-registered tracked URLs
(JSON-LD -> OpenGraph -> microdata price fallbacks).

Politeness is enforced here for every request: identifiable User-Agent, per-domain
throttle, exponential backoff on 429/5xx, and robots.txt honored. robots.txt is
fetched with OUR user agent — urllib.robotparser's default UA gets 403'd by many
storefronts, and a failed fetch would otherwise read as "disallow everything".
"""
import json
import re
import threading
import time
import urllib.robotparser
from typing import Dict, Iterator, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "*/*"})


class _DomainThrottle:
    """Serializes requests per domain at REQUEST_INTERVAL_SECONDS."""

    def __init__(self):
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str):
        with self._lock:
            elapsed = time.time() - self._last.get(domain, 0.0)
            delay = max(0.0, config.REQUEST_INTERVAL_SECONDS - elapsed)
            # Reserve the slot before sleeping so concurrent callers queue up.
            self._last[domain] = time.time() + delay
        if delay > 0:
            time.sleep(delay)


_throttle = _DomainThrottle()


class _RobotsCache:
    """Per-domain robots.txt, fetched with our own UA."""

    def __init__(self):
        self._parsers: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._lock = threading.Lock()

    def can_fetch(self, url: str) -> bool:
        domain = urlparse(url).netloc
        with self._lock:
            if domain not in self._parsers:
                parser = urllib.robotparser.RobotFileParser()
                try:
                    resp = _session.get(f"https://{domain}/robots.txt", timeout=config.REQUEST_TIMEOUT_SECONDS)
                    if resp.status_code == 200:
                        parser.parse(resp.text.splitlines())
                    elif resp.status_code in (401, 403):
                        # Site hides robots.txt from us entirely: treat as no rules
                        # (the RFC 9309 interpretation for unreachable-by-policy is
                        # ambiguous; we stay conservative elsewhere via throttling).
                        parser.parse([])
                    else:
                        parser.parse([])
                except Exception:
                    parser.parse([])
                self._parsers[domain] = parser
            parser = self._parsers[domain]
        try:
            return parser.can_fetch(config.USER_AGENT, url)
        except Exception:
            return True


_robots = _RobotsCache()


def polite_get(url: str, *, respect_robots: bool = True) -> Optional[requests.Response]:
    """GET with throttle, robots check, and backoff on 429/5xx. Returns None when
    blocked by robots or after exhausting retries."""
    if respect_robots and not _robots.can_fetch(url):
        print(f"pi: robots.txt disallows {url}")
        return None
    domain = urlparse(url).netloc
    backoff = 2.0
    for attempt in range(config.MAX_RETRIES + 1):
        _throttle.wait(domain)
        try:
            resp = _session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except Exception as e:
            print(f"pi: request error for {url}: {e}")
            resp = None
        if resp is not None and resp.status_code < 400:
            return resp
        if resp is not None and 400 <= resp.status_code < 429:
            return resp  # client error other than throttling: retrying won't help
        if attempt < config.MAX_RETRIES:
            retry_after = 0.0
            if resp is not None and resp.status_code == 429:
                try:
                    retry_after = float(resp.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
            time.sleep(max(backoff, retry_after))
            backoff *= 2
    return None


# ---------------------------------------------------------------------------
# Price extraction helpers
# ---------------------------------------------------------------------------

def _to_price(value) -> Optional[float]:
    if value is None:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


def _iter_jsonld_products(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            if isinstance(entry.get("@graph"), list):
                candidates.extend(e for e in entry["@graph"] if isinstance(e, dict))
                continue
            entry_type = entry.get("@type")
            types = entry_type if isinstance(entry_type, list) else [entry_type]
            if any(t in ("Product", "ProductGroup") for t in types if t):
                yield entry


def _offer_fields(offer: dict) -> dict:
    price = _to_price(offer.get("price") or offer.get("lowPrice"))
    availability = str(offer.get("availability") or "").lower()
    in_stock = None
    if availability:
        in_stock = "instock" in availability or "limitedavailability" in availability
    return {
        "price": price,
        "currency": offer.get("priceCurrency"),
        "in_stock": in_stock,
    }


def parse_product_page(html: str, url: str) -> Optional[dict]:
    """Extracts {title, price, currency, in_stock, gtin, sku, brand} from a product
    page. JSON-LD first (most reliable, carries GTIN), then OpenGraph/microdata."""
    soup = BeautifulSoup(html, "lxml")

    for product in _iter_jsonld_products(soup):
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            offers = {}
        fields = _offer_fields(offers)
        if fields["price"] is None:
            continue
        brand = product.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        gtin = (product.get("gtin13") or product.get("gtin12") or product.get("gtin14")
                or product.get("gtin") or offers.get("gtin13") or offers.get("gtin12"))
        return {
            "title": product.get("name"),
            "brand": brand,
            "sku": product.get("sku") or offers.get("sku"),
            "gtin": str(gtin) if gtin else None,
            **fields,
        }

    # OpenGraph / Twitter product meta.
    def _meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"]
        return None

    price = _to_price(_meta("product:price:amount", "og:price:amount", "twitter:data1"))
    if price is None:
        # Microdata itemprop=price as a last resort.
        tag = soup.find(attrs={"itemprop": "price"})
        if tag is not None:
            price = _to_price(tag.get("content") or tag.get_text())
    if price is None:
        return None
    availability = (_meta("product:availability", "og:availability") or "").lower()
    in_stock = None
    if availability:
        in_stock = "instock" in availability or "in stock" in availability
    return {
        "title": _meta("og:title") or (soup.title.get_text(strip=True) if soup.title else None),
        "brand": _meta("product:brand"),
        "sku": None,
        "gtin": None,
        "price": price,
        "currency": _meta("product:price:currency", "og:price:currency"),
        "in_stock": in_stock,
    }


class PageScraper:
    """Scrapes one registered product URL (feature a)."""

    def fetch(self, url: str) -> Optional[dict]:
        resp = polite_get(url)
        if resp is None:
            return None
        if resp.status_code != 200:
            print(f"pi: {resp.status_code} fetching tracked url {url}")
            return None
        return parse_product_page(resp.text, url)


# ---------------------------------------------------------------------------
# Catalog connectors
# ---------------------------------------------------------------------------

class ShopifyJsonConnector:
    """Iterates a Shopify store's public /products.json, yielding one record per
    variant. Generator-paginated so at most one page (250 products) is in memory."""

    connector_type = "shopify_json"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def iter_products(self) -> Iterator[dict]:
        for page in range(1, config.MAX_CATALOG_PAGES + 1):
            url = f"{self.base_url}/products.json?limit=250&page={page}"
            resp = polite_get(url)
            if resp is None or resp.status_code != 200:
                return
            try:
                products = resp.json().get("products", [])
            except ValueError:
                return
            if not products:
                return
            for product in products:
                brand = product.get("vendor")
                title = product.get("title")
                handle = product.get("handle")
                product_url = f"{self.base_url}/products/{handle}" if handle else None
                for variant in product.get("variants", []):
                    variant_title = variant.get("title")
                    full_title = title if variant_title in (None, "Default Title") \
                        else f"{title} - {variant_title}"
                    # Shopify's structured variant options (e.g. ["Anodized Black",
                    # "58"]) — kept so the matcher can compare color/size against our
                    # attribute_1/2/3 instead of leaning on fuzzy title text alone.
                    options = [
                        variant.get(k) for k in ("option1", "option2", "option3")
                        if variant.get(k) and variant.get(k) != "Default Title"
                    ]
                    yield {
                        "title": full_title,
                        "brand": brand,
                        "sku": variant.get("sku"),
                        "gtin": str(variant["barcode"]) if variant.get("barcode") else None,
                        "price": _to_price(variant.get("price")),
                        "compare_at_price": _to_price(variant.get("compare_at_price")),
                        "in_stock": bool(variant.get("available", True)),
                        "url": product_url,
                        "currency": None,
                        "variant_options": options,
                    }


class ShopifyHtmlConnector:
    """Fallback for stores that block /products.json: walk the products sitemap and
    parse each product page's JSON-LD (which usually includes a real GTIN).

    One request per product, so URLs are pre-filtered to slugs mentioning a tracked
    brand and the crawl is capped at MAX_HTML_PRODUCT_PAGES."""

    connector_type = "shopify_html"

    def __init__(self, base_url: str, brand_tokens=None):
        self.base_url = base_url.rstrip("/")
        self.brand_tokens = [b.lower().replace(" ", "-") for b in (brand_tokens or []) if b]

    def _product_urls(self) -> Iterator[str]:
        resp = polite_get(f"{self.base_url}/sitemap.xml")
        if resp is None or resp.status_code != 200:
            return
        product_sitemaps = re.findall(r"<loc>([^<]*sitemap_products[^<]*)</loc>", resp.text)
        for sitemap_url in product_sitemaps:
            sm = polite_get(sitemap_url.strip())
            if sm is None or sm.status_code != 200:
                continue
            for loc in re.findall(r"<loc>([^<]+)</loc>", sm.text):
                loc = loc.strip()
                if "/products/" in loc:
                    yield loc

    def iter_products(self) -> Iterator[dict]:
        fetched = 0
        for url in self._product_urls():
            if fetched >= config.MAX_HTML_PRODUCT_PAGES:
                return
            slug = urlparse(url).path.lower()
            if self.brand_tokens and not any(tok in slug for tok in self.brand_tokens):
                continue
            resp = polite_get(url)
            if resp is None or resp.status_code != 200:
                continue
            fetched += 1
            parsed = parse_product_page(resp.text, url)
            if parsed and parsed.get("price") is not None:
                parsed["url"] = url
                parsed.setdefault("compare_at_price", None)
                yield parsed


def detect_connector_type(base_url: str) -> str:
    """Probes a competitor site: open /products.json -> shopify_json; a products
    sitemap -> shopify_html; otherwise unknown (tracked-URL-only)."""
    base = base_url.rstrip("/")
    resp = polite_get(f"{base}/products.json?limit=1")
    if resp is not None and resp.status_code == 200:
        try:
            if isinstance(resp.json().get("products"), list):
                return "shopify_json"
        except ValueError:
            pass
    resp = polite_get(f"{base}/sitemap.xml")
    if resp is not None and resp.status_code == 200 and "sitemap_products" in resp.text:
        return "shopify_html"
    return "unknown"


def build_connector(competitor: dict, brand_tokens=None):
    ctype = competitor.get("connector_type")
    base_url = competitor["base_url"]
    if ctype == "shopify_json":
        return ShopifyJsonConnector(base_url)
    if ctype == "shopify_html":
        return ShopifyHtmlConnector(base_url, brand_tokens=brand_tokens)
    return None
