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
import ipaddress
import socket
import urllib.robotparser
from typing import Dict, Iterator, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

_session = requests.Session()


def _is_public_http_url(url: str) -> bool:
    """SSRF guard: only allow http(s) requests to public, non-loopback hosts.

    Tracked URLs can be registered through the API and are fetched server-side,
    so a target of http://localhost / 127.0.0.1 / 169.254.169.254 (cloud
    metadata) / RFC-1918 space must be refused before we ever issue the GET.
    Resolves the hostname and rejects if ANY resolved address is private.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True
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
_magento_graphql_capabilities = {}
_magento_graphql_lock = threading.Lock()


def polite_get(url: str, *, respect_robots: bool = True) -> Optional[requests.Response]:
    """GET with throttle, robots check, and backoff on 429/5xx. Returns None when
    blocked by robots or after exhausting retries."""
    if not _is_public_http_url(url):
        print(f"pi: refusing to fetch non-public/unsafe URL {url}")
        return None
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
        raw = re.sub(r"[^\d,.-]", "", str(value)).strip()
        # 1.234,56 / 1 234,56 are decimal-comma prices; 1,234.56 is not.
        if "," in raw and "." not in raw:
            tail = raw.rsplit(",", 1)[-1]
            raw = raw.replace(",", ".") if len(tail) in (1, 2) else raw.replace(",", "")
        elif "," in raw and "." in raw:
            raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") \
                else raw.replace(".", "").replace(",", ".")
        cleaned = raw
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
        "price_low": _to_price(offer.get("lowPrice")),
        "price_high": _to_price(offer.get("highPrice")),
        "currency": offer.get("priceCurrency"),
        "in_stock": in_stock,
    }


def _brand_name(value):
    return value.get("name") if isinstance(value, dict) else value


def _gtin(*objects):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        value = (obj.get("gtin14") or obj.get("gtin13") or obj.get("gtin12")
                 or obj.get("gtin8") or obj.get("gtin"))
        if value:
            return str(value)
    return None


def _normalized_listing(**values) -> dict:
    row = {
        "title": None, "brand": None, "sku": None, "gtin": None,
        "variant_id": None, "variant_options": [], "price": None,
        "compare_at_price": None, "price_low": None, "price_high": None,
        "currency": None, "in_stock": None, "price_scope": "product",
        "extraction_method": None, "url": None,
    }
    row.update(values)
    row["variant_options"] = row.get("variant_options") or []
    return row


def _magento_listings(soup: BeautifulSoup, url: str) -> list:
    """Extract the main Magento configurable product's child records.

    Magento pages commonly contain several x-magento-init blocks for carousels and
    related products. Only the main swatch-options component is authoritative for
    the PDP; failing to find that exact key deliberately returns no records.
    """
    configs = []
    for script in soup.find_all("script", type="text/x-magento-init"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        swatch = data.get("[data-role=swatch-options]") if isinstance(data, dict) else None
        if not isinstance(swatch, dict):
            continue
        component = swatch.get("Magento_Swatches/js/swatch-renderer")
        if not isinstance(component, dict):
            continue
        cfg = component.get("jsonConfig")
        if isinstance(cfg, dict) and isinstance(cfg.get("index"), dict):
            configs.append(cfg)
    if not configs:
        return []
    cfg = max(configs, key=lambda c: len(c.get("index") or {}))
    index = cfg.get("index") or {}
    attributes = cfg.get("attributes") or {}
    skus = cfg.get("sku") or {}
    upcs = cfg.get("upc") or {}
    prices = cfg.get("optionPrices") or {}
    stock = cfg.get("stockInfo") or {}

    product = next(_iter_jsonld_products(soup), {})
    base_title = product.get("name") if isinstance(product, dict) else None
    base_brand = _brand_name(product.get("brand")) if isinstance(product, dict) else None
    if not base_title:
        title_tag = soup.find("meta", attrs={"property": "og:title"})
        base_title = title_tag.get("content") if title_tag else None

    option_labels = {}
    for attr_id, attr in attributes.items():
        values = {}
        for option in (attr or {}).get("options") or []:
            oid = str(option.get("id"))
            values[oid] = option.get("label")
        option_labels[str(attr_id)] = values

    rows = []
    for child_id, selected in index.items():
        child_id = str(child_id)
        options = []
        for attr_id, option_id in (selected or {}).items():
            label = option_labels.get(str(attr_id), {}).get(str(option_id))
            if label:
                options.append(str(label).strip())
        sku = skus.get(child_id)
        price_cfg = prices.get(child_id) or {}
        final = _to_price((price_cfg.get("finalPrice") or {}).get("amount"))
        regular = _to_price((price_cfg.get("oldPrice") or {}).get("amount"))
        stock_cfg = stock.get(child_id) or stock.get(str(sku)) or {}
        in_stock = stock_cfg.get("isSalable")
        rows.append(_normalized_listing(
            title=(f"{base_title} - {' / '.join(options)}" if base_title and options else base_title),
            brand=base_brand, sku=str(sku) if sku else None,
            gtin=str(upcs.get(str(sku))) if sku and upcs.get(str(sku)) else None,
            variant_id=child_id, variant_options=options, price=final,
            compare_at_price=regular if regular and final and regular > final else None,
            currency=next((o.get("priceCurrency") for o in (
                product.get("offers") if isinstance(product.get("offers"), list)
                else [product.get("offers")])
                if isinstance(o, dict) and o.get("priceCurrency")), None)
                if isinstance(product, dict) else None,
            in_stock=bool(in_stock) if in_stock is not None else None,
            price_scope="variant", extraction_method="magento_json_config", url=url,
        ))
    return [r for r in rows if r.get("price") is not None]


def _jsonld_listings(soup: BeautifulSoup, url: str) -> list:
    rows = []
    for product in _iter_jsonld_products(soup):
        brand = _brand_name(product.get("brand"))
        variants = product.get("hasVariant") or []
        if isinstance(variants, dict):
            variants = [variants]
        sources = variants if variants else [product]
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_brand = _brand_name(source.get("brand")) or brand
            offers = source.get("offers")
            if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
                offers = offers.get("offers")
            offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
            if not offer_list:
                continue
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                fields = _offer_fields(offer)
                low, high = fields.pop("price_low"), fields.pop("price_high")
                is_range = (low is not None and high is not None and abs(low - high) > .005
                            and not offer.get("sku") and source is product)
                price = fields.pop("price")
                if price is None and low is None:
                    continue
                sku = offer.get("sku") or source.get("sku")
                variant_options = []
                for key in ("color", "size"):
                    if source.get(key):
                        variant_options.append(str(source[key]))
                rows.append(_normalized_listing(
                    title=source.get("name") or product.get("name"), brand=source_brand,
                    sku=str(sku) if sku else None, gtin=_gtin(offer, source, product),
                    variant_id=str(source.get("productID")) if source.get("productID") else None,
                    variant_options=variant_options,
                    price=low if is_range else price, price_low=low, price_high=high,
                    currency=fields.get("currency"), in_stock=fields.get("in_stock"),
                    price_scope="range" if is_range else ("variant" if (variants or len(offer_list) > 1) else "product"),
                    extraction_method="jsonld", url=url,
                ))
    # ProductGroup children are often repeated as standalone Product nodes in
    # @graph. Collapse only truly identical records; differing seller prices for
    # the same SKU remain ambiguous rather than becoming order-dependent.
    deduped = {}
    for row in rows:
        key = (
            row.get("variant_id"), row.get("sku"), row.get("gtin"),
            tuple(row.get("variant_options") or []), row.get("price"),
            row.get("price_low"), row.get("price_high"), row.get("in_stock"),
        )
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = row
        else:
            for field, value in row.items():
                if existing.get(field) in (None, [], "") and value not in (None, [], ""):
                    existing[field] = value
    return list(deduped.values())


def extract_listings(html: str, url: str) -> list:
    """Return every independently identifiable price listing on a product page."""
    soup = BeautifulSoup(html, "lxml")
    magento = _magento_listings(soup, url)
    if magento:
        return magento
    jsonld = _jsonld_listings(soup, url)
    if jsonld:
        return jsonld

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
        return []
    availability = (_meta("product:availability", "og:availability") or "").lower()
    in_stock = None
    if availability:
        in_stock = "instock" in availability or "in stock" in availability
    return [_normalized_listing(
        title=_meta("og:title") or (soup.title.get_text(strip=True) if soup.title else None),
        brand=_meta("product:brand"), price=price,
        currency=_meta("product:price:currency", "og:price:currency"),
        in_stock=in_stock, price_scope="product", extraction_method="opengraph_microdata",
        url=url,
    )]


def _magento_graphql_listings(html: str, url: str) -> list:
    """Use anonymous Magento GraphQL when the storefront exposes it.

    Capability failures are cached for an hour so disabled endpoints cost one
    probe, not one request per product. Static extraction remains authoritative
    fallback and this helper is never used by the pure offline extractor.
    """
    soup = BeautifulSoup(html, "lxml")
    if not soup.find("script", type="text/x-magento-init"):
        return []
    parent = next(_iter_jsonld_products(soup), {})
    sku = parent.get("sku") if isinstance(parent, dict) else None
    if not sku:
        return []
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    with _magento_graphql_lock:
        cached = _magento_graphql_capabilities.get(origin)
    if cached and not cached[0] and time.time() - cached[1] < 3600:
        return []
    query = """query VariantPrices($sku:String!){products(filter:{sku:{eq:$sku}}){items{
      name sku ... on ConfigurableProduct {variants{attributes{code label value_index} product{
      uid name sku stock_status price_range{minimum_price{regular_price{value currency}
      final_price{value currency}}}}}}}}}"""
    endpoint = f"{origin}/graphql?{urlencode({'query': query, 'variables': json.dumps({'sku': str(sku)})})}"
    resp = polite_get(endpoint)
    try:
        payload = resp.json() if resp is not None and resp.status_code == 200 else None
    except ValueError:
        payload = None
    items = ((payload or {}).get("data") or {}).get("products", {}).get("items") or []
    variants = items[0].get("variants") or [] if items else []
    rows = []
    for variant in variants:
        child = variant.get("product") or {}
        price_info = ((child.get("price_range") or {}).get("minimum_price") or {})
        final = price_info.get("final_price") or {}
        regular = price_info.get("regular_price") or {}
        price = _to_price(final.get("value"))
        if price is None:
            continue
        options = [str(a.get("label")) for a in (variant.get("attributes") or []) if a.get("label")]
        regular_price = _to_price(regular.get("value"))
        stock_status = str(child.get("stock_status") or "").upper()
        rows.append(_normalized_listing(
            title=(f"{items[0].get('name')} - {' / '.join(options)}" if options else child.get("name")),
            brand=_brand_name(parent.get("brand")), sku=child.get("sku"),
            variant_id=str(child.get("uid")) if child.get("uid") else None,
            variant_options=options, price=price,
            compare_at_price=regular_price if regular_price and regular_price > price else None,
            currency=final.get("currency") or regular.get("currency"),
            in_stock=(stock_status == "IN_STOCK") if stock_status else None,
            price_scope="variant", extraction_method="magento_graphql", url=url,
        ))
    # Embedded config often carries UPCs/custom stock metadata omitted from the
    # public GraphQL schema. Join it by child SKU without replacing API prices.
    static_by_sku = {r.get("sku"): r for r in _magento_listings(soup, url) if r.get("sku")}
    for row in rows:
        static = static_by_sku.get(row.get("sku")) or {}
        for field in ("gtin", "in_stock"):
            if row.get(field) is None:
                row[field] = static.get(field)
        if not row.get("variant_options"):
            row["variant_options"] = static.get("variant_options") or []
    with _magento_graphql_lock:
        _magento_graphql_capabilities[origin] = (bool(rows), time.time())
    return rows


def _norm_identity(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def resolve_listing(listings: list, target_identity: Optional[dict] = None) -> dict:
    """Resolve a listing without allowing document order to choose a variant."""
    listings = [r for r in (listings or []) if r.get("price") is not None]
    if not listings:
        return {"status": "not_found", "listing": None, "candidates": []}
    target = target_identity or {}
    priorities = (
        ("variant_id", target.get("variant_id")),
        ("sku", target.get("sku")),
        ("gtin", target.get("gtin")),
    )
    for field, wanted in priorities:
        if wanted:
            matches = [r for r in listings if _norm_identity(r.get(field)) == _norm_identity(wanted)]
            if len(matches) == 1:
                return {"status": "exact", "matched_by": field,
                        "listing": matches[0], "candidates": matches}
    wanted_options = [_norm_identity(v) for v in (target.get("variant_options") or []) if v]
    if wanted_options:
        available = {_norm_identity(v) for r in listings for v in (r.get("variant_options") or [])}
        relevant_options = [w for w in wanted_options if w in available]
        matches = [r for r in listings if all(
            w in {_norm_identity(v) for v in (r.get("variant_options") or [])}
            for w in relevant_options)] if relevant_options else []
        if len(matches) == 1:
            return {"status": "exact", "matched_by": "variant_options",
                    "listing": matches[0], "candidates": matches}
    if len(listings) == 1:
        return {"status": "exact", "matched_by": "single_listing",
                "listing": listings[0], "candidates": listings}
    prices = [float(r["price"]) for r in listings if r.get("price") is not None]
    currencies = {r.get("currency") for r in listings if r.get("currency")}
    summary = _normalized_listing(
        title=listings[0].get("title"), brand=listings[0].get("brand"),
        price=min(prices), price_low=min(prices), price_high=max(prices),
        currency=next(iter(currencies)) if len(currencies) == 1 else None,
        in_stock=any(r.get("in_stock") is True for r in listings),
        price_scope="range", extraction_method="ambiguous_variant_set",
        url=listings[0].get("url"),
    )
    return {"status": "ambiguous", "listing": summary, "candidates": listings}


def parse_product_page(html: str, url: str) -> Optional[dict]:
    """Backward-compatible single-result wrapper; ambiguous pages return a range."""
    return resolve_listing(extract_listings(html, url)).get("listing")


def _shopify_listings(url: str) -> list:
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
        return []
    parsed = urlparse(url)
    handle = m.group(1)
    variant_id = (parse_qs(parsed.query).get("variant") or [None])[0]
    if not variant_id:
        # Some storefronts (e.g. Enroute) carry the variant in the path
        # ('/products/<handle>/<variant_id>') instead of a '?variant=' query.
        # Without this, a multi-variant pin with no query/SKU resolves to no
        # variant -> HTML fallback -> the default variant or no price at all.
        tail = re.match(rf"/products/{re.escape(handle)}/(\d+)", parsed.path or "")
        if tail:
            variant_id = tail.group(1)
    resp = polite_get(f"{parsed.scheme}://{parsed.netloc}/products/{handle}.js")
    if resp is None or resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    variants = data.get("variants") or []
    if not variants:
        return []
    cents = lambda c: round(c / 100.0, 2) if isinstance(c, (int, float)) else None
    rows = []
    for chosen in variants:
        pub = chosen.get("public_title") or chosen.get("title")
        title = data.get("title")
        options = [chosen.get(k) for k in ("option1", "option2", "option3")
                   if chosen.get(k) and chosen.get(k) != "Default Title"]
        rows.append(_normalized_listing(
            title=f"{title} - {pub}" if pub and pub != "Default Title" else title,
            brand=data.get("vendor"), sku=chosen.get("sku"),
            gtin=str(chosen["barcode"]) if chosen.get("barcode") else None,
            variant_id=str(chosen.get("id")) if chosen.get("id") else None,
            variant_options=options, price=cents(chosen.get("price")),
            compare_at_price=cents(chosen.get("compare_at_price")),
            in_stock=bool(chosen.get("available", True)), currency=data.get("currency"),
            price_scope="variant", extraction_method="shopify_js", url=url,
        ))
    # URL variant is an identity hint, not permission to discard the others.
    if variant_id:
        for row in rows:
            if row.get("variant_id") == str(variant_id):
                row["url"] = url
    return rows


def _shopify_variant(url: str, sku: Optional[str] = None) -> Optional[dict]:
    parsed = urlparse(url)
    variant_id = (parse_qs(parsed.query).get("variant") or [None])[0]
    return resolve_listing(_shopify_listings(url), {"variant_id": variant_id, "sku": sku}).get("listing")


class PageScraper:
    """Scrapes one registered product URL (feature a)."""

    def listings(self, url: str) -> list:
        shopify = _shopify_listings(url)
        if shopify:
            return shopify
        resp = polite_get(url)
        if resp is None or resp.status_code != 200:
            if resp is not None:
                print(f"pi: {resp.status_code} fetching tracked url {url}")
            return []
        return _magento_graphql_listings(resp.text, url) or extract_listings(resp.text, url)

    def fetch(self, url: str, sku: Optional[str] = None, gtin: Optional[str] = None,
              variant_id: Optional[str] = None, variant_options=None) -> Optional[dict]:
        # Shopify: resolve the exact variant via .js first (the PDP HTML only shows
        # the default variant's price); otherwise fall back to generic page parsing.
        parsed = urlparse(url)
        url_variant = (parse_qs(parsed.query).get("variant") or [None])[0]
        result = resolve_listing(self.listings(url), {
            "variant_id": variant_id or url_variant, "sku": sku, "gtin": gtin,
            "variant_options": variant_options or [],
        })
        listing = result.get("listing")
        if listing is not None:
            listing["_resolution_status"] = result["status"]
            listing["_matched_by"] = result.get("matched_by")
            listing["_candidates"] = result.get("candidates") or []
        return listing


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
                        "price_low": None,
                        "price_high": None,
                        "price_scope": "variant",
                        "extraction_method": "shopify_json",
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
            for parsed in (_magento_graphql_listings(resp.text, url)
                           or extract_listings(resp.text, url)):
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
            for parsed in (_magento_graphql_listings(resp.text, url)
                           or extract_listings(resp.text, url)):
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
