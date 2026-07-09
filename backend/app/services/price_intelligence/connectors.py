"""Competitor scraping connectors.

Tiered, cheapest first:
  1. shopify_json — the store's public /products.json (full catalog, no barcodes on
     most stores, so matching is brand+SKU/fuzzy).
  2. shopify_html — product pages discovered via the products sitemap; JSON-LD on
     the page usually carries real GTINs. Costs one request per product, so the
     crawl is pre-filtered to tracked brands and capped.
  3. sitemap_html — the same sitemap-walk for non-Shopify stores (Magento,
     headless storefronts): sitemaps from robots.txt + /sitemap.xml, one level
     of index nesting, brand-slug filtered, parse_product_page per page.
  4. serp_discovery.py — SerpApi Google search (site:-scoped) finds product URLs
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
import unicodedata
import urllib.robotparser
from typing import Dict, Iterator, Optional
from urllib.parse import parse_qs, urljoin, urlparse

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


def _shopify_variant(url: str, sku: Optional[str] = None) -> Optional[dict]:
    """Resolve a *specific* Shopify variant's price/stock from /products/<handle>.js.

    A Shopify PDP's HTML/JSON-LD only ever reflects the default variant — the
    `?variant=<id>` switch is client-side — so parsing the page returns the landing
    variant's price, not the one we matched. The .js endpoint lists every variant
    (id/sku/price-in-cents/available/barcode); we pick by the URL's ?variant id, or
    by the known SKU (for older links stored as the base URL). Returns None when the
    URL isn't a Shopify product page, the endpoint is unavailable, or the variant
    can't be identified — callers fall back to generic HTML parsing."""
    m = re.search(r"/products/([^/?#]+)", url or "")
    if not m:
        return None
    parsed = urlparse(url)
    handle = m.group(1)
    variant_id = (parse_qs(parsed.query).get("variant") or [None])[0]
    resp = polite_get(f"{parsed.scheme}://{parsed.netloc}/products/{handle}.js")
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    variants = data.get("variants") or []
    if not variants:
        return None
    chosen = None
    if variant_id:
        chosen = next((v for v in variants if str(v.get("id")) == str(variant_id)), None)
    if chosen is None and sku:
        chosen = next((v for v in variants if str(v.get("sku") or "") == str(sku)), None)
    if chosen is None and len(variants) == 1:
        chosen = variants[0]
    if chosen is None:
        return None  # ambiguous (base URL, no id/sku) — let HTML fallback decide
    cents = lambda c: round(c / 100.0, 2) if isinstance(c, (int, float)) else None
    pub = chosen.get("public_title") or chosen.get("title")
    title = data.get("title")
    return {
        "title": f"{title} - {pub}" if pub and pub != "Default Title" else title,
        "brand": data.get("vendor"),
        "sku": chosen.get("sku"),
        "gtin": str(chosen["barcode"]) if chosen.get("barcode") else None,
        "price": cents(chosen.get("price")),
        "compare_at_price": cents(chosen.get("compare_at_price")),
        "in_stock": bool(chosen.get("available", True)),
        "currency": data.get("currency"),
    }


class PageScraper:
    """Scrapes one registered product URL (feature a)."""

    def fetch(self, url: str, sku: Optional[str] = None) -> Optional[dict]:
        # Shopify: resolve the exact variant via .js first (the PDP HTML only shows
        # the default variant's price); otherwise fall back to generic page parsing.
        variant = _shopify_variant(url, sku=sku)
        if variant is not None and variant.get("price") is not None:
            return variant
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
                    # Variant-qualified URL so the confirmed link points at the exact
                    # variant, and targeted re-scrapes resolve its price via .js
                    # instead of landing on the default variant.
                    vid = variant.get("id")
                    variant_url = (f"{product_url}?variant={vid}"
                                   if product_url and vid else product_url)
                    yield {
                        "title": full_title,
                        "brand": brand,
                        "sku": variant.get("sku"),
                        "gtin": str(variant["barcode"]) if variant.get("barcode") else None,
                        "price": _to_price(variant.get("price")),
                        "compare_at_price": _to_price(variant.get("compare_at_price")),
                        "in_stock": bool(variant.get("available", True)),
                        "url": variant_url,
                        "variant_id": str(vid) if vid else None,
                        "currency": None,
                        "variant_options": options,
                    }


def _brand_slug_tokens(brands) -> list:
    """Brand names -> URL-slug tokens, accent-folded ("Cervélo" must match a
    "cervelo-soloist" slug). Each brand yields its hyphenated and squashed forms."""
    tokens = set()
    for brand in brands or []:
        if not brand:
            continue
        folded = unicodedata.normalize("NFKD", str(brand))
        folded = "".join(c for c in folded if not unicodedata.combining(c)).strip().lower()
        if not folded:
            continue
        tokens.add(folded.replace(" ", "-"))
        tokens.add(folded.replace(" ", ""))
    return sorted(tokens)


def _fold_slug(value) -> str:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in folded if not unicodedata.combining(c)).strip().lower()


def _slug_brand(path: str, brand_names) -> Optional[str]:
    """The tracked brand a product URL slug begins with, or None. CMS storefronts
    (SmartEtailing, etc.) frequently omit the brand from page markup but keep it in
    the URL ('/product/continental-gp5000-...'); recovering it lets those products
    clear the brand-gated persistence and participate in matching. A trailing
    separator is required so a short brand ("poc") can't match inside a word
    ("pocket"); longest brand first so a multi-word name wins over a bare prefix."""
    slug = _fold_slug(str(path).rsplit("/", 1)[-1])
    if not slug:
        return None
    for brand in sorted((b for b in (brand_names or []) if b), key=len, reverse=True):
        folded = _fold_slug(brand)
        for form in (folded.replace(" ", "-"), folded.replace(" ", "")):
            if form and (slug == form or slug.startswith(form + "-")
                         or slug.startswith(form + "_")):
                return brand
    return None


class ShopifyHtmlConnector:
    """Fallback for stores that block /products.json: walk the products sitemap and
    parse each product page's JSON-LD (which usually includes a real GTIN).

    One request per product, so URLs are pre-filtered to slugs mentioning a tracked
    brand and the crawl is capped at MAX_HTML_PRODUCT_PAGES."""

    connector_type = "shopify_html"

    def __init__(self, base_url: str, brand_tokens=None):
        self.base_url = base_url.rstrip("/")
        self._brand_names = [b for b in (brand_tokens or []) if b]
        self.brand_tokens = _brand_slug_tokens(brand_tokens)

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
                if not parsed.get("brand"):
                    parsed["brand"] = _slug_brand(slug, self._brand_names)
                parsed["url"] = url
                parsed.setdefault("compare_at_price", None)
                yield parsed


class GenericSitemapConnector:
    """Catalog crawl for non-Shopify stores (Magento, headless storefronts, ...)
    via their public sitemaps: robots.txt `Sitemap:` lines + /sitemap.xml, one
    level of <sitemapindex> nesting. Page URLs are filtered to tracked-brand
    slugs and parsed with parse_product_page, so it behaves exactly like the
    shopify_html crawl — one request per candidate page, capped."""

    connector_type = "sitemap_html"

    # Path segments that are never product detail pages.
    NON_PRODUCT_RE = re.compile(
        r"/(collections?|categor(y|ies)|cms|blogs?|pages?|news|apps)(/|$)"
    )
    MAX_SITEMAP_FETCHES = 15

    def __init__(self, base_url: str, brand_tokens=None):
        self.base_url = base_url.rstrip("/")
        self._brand_names = [b for b in (brand_tokens or []) if b]
        self.brand_tokens = _brand_slug_tokens(brand_tokens)

    def _sitemap_sources(self) -> list:
        """Sitemap URLs from robots.txt plus the conventional /sitemap.xml.
        Prefers product-hinting filenames, and _en_ over _fr_ duplicates."""
        sources = []
        resp = polite_get(f"{self.base_url}/robots.txt", respect_robots=False)
        if resp is not None and resp.status_code == 200:
            sources = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", resp.text)
        sources.append(f"{self.base_url}/sitemap.xml")
        # De-dupe, keep order.
        seen, ordered = set(), []
        for url in sources:
            if url not in seen:
                seen.add(url)
                ordered.append(url)
        product_maps = [u for u in ordered if "product" in u.lower()]
        maps = product_maps or ordered
        if any("_en" in u.lower() for u in maps):
            maps = [u for u in maps if "_fr" not in u.lower()]
        return maps

    def _iter_page_urls(self) -> Iterator[str]:
        fetches = 0
        queue = self._sitemap_sources()
        seen_pages = set()
        while queue and fetches < self.MAX_SITEMAP_FETCHES:
            sitemap_url = queue.pop(0)
            resp = polite_get(sitemap_url.strip())
            fetches += 1
            if resp is None or resp.status_code != 200 or "<" not in resp.text[:200]:
                continue
            locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
            if "<sitemapindex" in resp.text:
                # One level of nesting: children go on the queue (product-
                # hinting first so the fetch budget is spent well).
                locs.sort(key=lambda u: "product" not in u.lower())
                queue.extend(locs)
                continue
            for loc in locs:
                loc = loc.strip()
                if loc in seen_pages:
                    continue
                seen_pages.add(loc)
                yield loc

    def iter_products(self) -> Iterator[dict]:
        fetched = 0
        for url in self._iter_page_urls():
            if fetched >= config.MAX_HTML_PRODUCT_PAGES:
                return
            path = urlparse(url).path.lower()
            if not path or path == "/" or self.NON_PRODUCT_RE.search(path):
                continue
            if self.brand_tokens and not any(tok in path for tok in self.brand_tokens):
                continue
            resp = polite_get(url)
            if resp is None or resp.status_code != 200:
                continue
            fetched += 1
            parsed = parse_product_page(resp.text, url)
            if parsed and parsed.get("price") is not None:
                if not parsed.get("brand"):
                    parsed["brand"] = _slug_brand(path, self._brand_names)
                parsed["url"] = url
                parsed.setdefault("compare_at_price", None)
                yield parsed


def detect_connector_type(base_url: str) -> str:
    """Probes a competitor site: open /products.json -> shopify_json; a Shopify
    products sitemap -> shopify_html; any other usable sitemap -> sitemap_html;
    otherwise unknown (tracked-URL/SERP only)."""
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
    # Non-Shopify: usable if any sitemap yields at least one page URL.
    probe = GenericSitemapConnector(base)
    for _ in probe._iter_page_urls():
        return "sitemap_html"
    return "unknown"


def build_connector(competitor: dict, brand_tokens=None):
    ctype = competitor.get("connector_type")
    base_url = competitor["base_url"]
    if ctype == "shopify_json":
        return ShopifyJsonConnector(base_url)
    if ctype == "shopify_html":
        return ShopifyHtmlConnector(base_url, brand_tokens=brand_tokens)
    if ctype == "sitemap_html":
        return GenericSitemapConnector(base_url, brand_tokens=brand_tokens)
    return None
