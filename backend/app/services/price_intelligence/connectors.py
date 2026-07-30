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
from collections import Counter
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


def _site_host(url: str) -> str:
    """Hostname of a URL, lowercased and with a leading `www.` dropped.

    Accepts a bare host ("example.com") as well as a full URL — callers hold
    competitor identity in both forms."""
    try:
        text = str(url or "").strip()
        if text and "//" not in text:
            text = "//" + text  # bare host: make urlparse read it as a netloc
        host = (urlparse(text).hostname or "").lower()
    except (ValueError, TypeError):
        return ""
    return host[4:] if host.startswith("www.") else host


def _same_site(url: str, base_url: str) -> bool:
    """True when `url` belongs to `base_url`'s site: the same host, or a
    subdomain of it.

    Crawl scope, not an SSRF check (that's _is_public_http_url). Sitemaps name
    whatever hosts they like — `robots.txt` Sitemap: lines and <sitemapindex>
    children in particular — and a page fetched off-site would still have its
    price recorded against this competitor_id. The leading dot on the suffix
    test is load-bearing: it's what stops 'evil-example.com' from passing as
    'example.com'.
    """
    host, base = _site_host(url), _site_host(base_url)
    if not host or not base:
        return False
    return host == base or host.endswith("." + base)


class _FetchStats:
    """HTTP outcomes for one competitor's crawl.

    Without this a competitor at zero products is unexplainable: polite_get
    hands back 403/429 without retrying and every caller only counts 200s, so
    "blocked", "sitemap 404s" and "filtered down to nothing" all end the night
    looking identical. The histogram rides along in crawl_state_json.
    """

    def __init__(self):
        self.status_counts: Dict[str, int] = {}
        self.fetch_errors = 0
        self.robots_blocked = 0
        self.unsafe_urls = 0

    def record_status(self, code: int):
        key = str(int(code))
        self.status_counts[key] = self.status_counts.get(key, 0) + 1

    @property
    def blocked(self) -> int:
        """Fetches refused by the site itself (403/429) — the block signal."""
        return sum(n for code, n in self.status_counts.items()
                   if code in ("401", "403", "429"))

    @property
    def total(self) -> int:
        return (sum(self.status_counts.values()) + self.fetch_errors
                + self.robots_blocked + self.unsafe_urls)

    def as_dict(self) -> dict:
        return {
            "status_counts": dict(self.status_counts),
            "fetch_errors": self.fetch_errors,
            "robots_blocked": self.robots_blocked,
            "blocked_fetches": self.blocked,
            "fetches": self.total,
        }


class _DomainThrottle:
    """Serializes requests per domain at REQUEST_INTERVAL_SECONDS, or at a
    per-competitor interval when the caller passes one.

    A 429 raises that host's floor for the rest of the process, so a
    per-competitor interval tuned too aggressively self-corrects instead of
    getting us blocked. The clamp keeps a mistyped setting (0, 3600) from
    hammering a store or stalling the night.
    """

    MIN_INTERVAL = 0.25
    MAX_INTERVAL = 30.0
    PENALTY_FACTOR = 2.0
    MAX_PENALTY = 8.0

    def __init__(self):
        self._last: Dict[str, float] = {}
        self._penalty: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _base_interval(self, interval) -> float:
        try:
            value = config.REQUEST_INTERVAL_SECONDS if interval is None else float(interval)
        except (TypeError, ValueError):
            value = config.REQUEST_INTERVAL_SECONDS
        return min(max(value, self.MIN_INTERVAL), self.MAX_INTERVAL)

    def penalize(self, domain: str):
        """Called on a 429: back this host off for the remainder of the run."""
        with self._lock:
            current = self._penalty.get(domain, 1.0)
            self._penalty[domain] = min(current * self.PENALTY_FACTOR, self.MAX_PENALTY)

    def penalty(self, domain: str) -> float:
        with self._lock:
            return self._penalty.get(domain, 1.0)

    def wait(self, domain: str, interval=None):
        with self._lock:
            target = self._base_interval(interval) * self._penalty.get(domain, 1.0)
            elapsed = time.time() - self._last.get(domain, 0.0)
            delay = max(0.0, target - elapsed)
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


def polite_get(url: str, *, respect_robots: bool = True, interval=None,
               stats: Optional[_FetchStats] = None) -> Optional[requests.Response]:
    """GET with throttle, robots check, and backoff on 429/5xx. Returns None when
    blocked by robots or after exhausting retries.

    `interval` overrides the global per-domain delay for this request (a
    per-competitor setting). `stats`, when given, collects the HTTP outcome so a
    crawl can report *why* it came back empty.
    """
    if not _is_public_http_url(url):
        print(f"pi: refusing to fetch non-public/unsafe URL {url}")
        if stats is not None:
            stats.unsafe_urls += 1
        return None
    if respect_robots and not _robots.can_fetch(url):
        print(f"pi: robots.txt disallows {url}")
        if stats is not None:
            stats.robots_blocked += 1
        return None
    domain = urlparse(url).netloc
    backoff = 2.0
    for attempt in range(config.MAX_RETRIES + 1):
        _throttle.wait(domain, interval)
        try:
            resp = _session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except Exception as e:
            print(f"pi: request error for {url}: {e}")
            resp = None
        if stats is not None:
            if resp is None:
                stats.fetch_errors += 1
            else:
                stats.record_status(resp.status_code)
        if resp is not None and resp.status_code < 400:
            return resp
        if resp is not None and 400 <= resp.status_code < 429:
            return resp  # client error other than throttling: retrying won't help
        if resp is not None and resp.status_code == 429:
            # Slow this host down for the rest of the run, not just this retry —
            # otherwise a too-fast per-competitor interval keeps earning 429s.
            _throttle.penalize(domain)
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


# A variant label is "<Colour> / <Size>" — the separator is a SPACED slash, so a
# colourway that itself contains a slash ("Matte Black/Frequency Orange / Medium")
# survives intact.
_OPTION_SEPARATOR = " / "


def _options_from_label(label) -> list:
    """Splits a storefront variant label into option values, or [] when the
    string isn't a variant label at all.

    Real SKUs ("GR-7202216", "0058217001") never carry the spaced slash, so
    requiring it keeps genuine SKUs out of the option channel — a SKU misread as
    an option would hand the matcher an imaginary colour/size to conflict on."""
    text = str(label or "").strip()
    if _OPTION_SEPARATOR not in text:
        return []
    return [part.strip() for part in text.split(_OPTION_SEPARATOR) if part.strip()]


def _gtin_key(value) -> str:
    """Digits-only, leading zeros stripped — the same convention matcher.normalize_upc
    uses, so a 12-digit UPC in the spec table matches its zero-padded EAN form."""
    return re.sub(r"\D", "", str(value or "")).lstrip("0")


def _spec_table_options(soup: BeautifulSoup) -> dict:
    """Maps GTIN -> variant label from a SmartEtailing product page's spec table.

    Those storefronts (Oak Bay Bikes, The Bike Zone, ...) publish one JSON-LD
    Offer per purchasable variant carrying *only* a gtin — no colour, no size —
    but the same page renders a table that names each one:

        <tr><td data-th="Option">Matte White / Small</td>
            <td data-th="UPC">199270032023</td> ...

    Recovering that label is what lets the matcher tell a store's variants apart
    at all; without it every variant of a model looks identical and only the
    model-grain fuzzy pass can fire."""
    labels = {}
    for row in soup.find_all("tr"):
        option = row.find("td", attrs={"data-th": "Option"})
        upc = row.find("td", attrs={"data-th": "UPC"})
        if not option or not upc:
            continue
        digits = _gtin_key(upc.get_text(strip=True))
        label = option.get_text(strip=True)
        if digits and label:
            labels[digits] = label
    return labels


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
    spec_labels = _spec_table_options(soup)
    for product in _iter_jsonld_products(soup):
        brand = _brand_name(product.get("brand"))
        variants = product.get("hasVariant") or []
        if isinstance(variants, dict):
            variants = [variants]
        sources = variants if variants else [product]
        # (source, offer) pairs up front: whether this node describes ONE thing
        # or a whole variant set decides if the product-level gtin may be
        # inherited (see below), and that can't be known mid-loop.
        pairs = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            offers = source.get("offers")
            if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
                offers = offers.get("offers")
            offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
            for offer in offer_list:
                if isinstance(offer, dict):
                    pairs.append((source, offer, len(offer_list)))
        # A product-level gtin identifies the product, not any one variant. When
        # the node yields several listings, inheriting it stamps every variant
        # with the same barcode — which then collapses them onto one match_key
        # and one price-history diff_key (Enroute: 30 sizes/colours sharing one
        # barcode and two prices, so every run reads as a price swing), and
        # would GTIN-match all of them to a single item at confidence 1.0.
        sole_listing = len(pairs) == 1
        for source, offer, offers_in_source in pairs:
            fields = _offer_fields(offer)
            low, high = fields.pop("price_low"), fields.pop("price_high")
            is_range = (low is not None and high is not None and abs(low - high) > .005
                        and not offer.get("sku") and source is product)
            price = fields.pop("price")
            if price is None and low is None:
                continue
            sku = offer.get("sku") or source.get("sku")
            # Offer first, then the variant node; the product node only when it
            # IS this listing (`source is product` means there were no
            # hasVariant children, so a product-level gtin would otherwise be
            # copied onto every offer on the page).
            chain = [offer]
            if source is not product:
                chain.append(source)
            if sole_listing:
                chain.append(product)
            gtin = _gtin(*chain)
            variant_options = []
            for key in ("color", "size"):
                if source.get(key):
                    variant_options.append(str(source[key]))
            if not variant_options:
                # Storefronts that don't use schema.org color/size still name the
                # variant somewhere: Shopify themes put the label in the offer's
                # SKU slot ("Deep Navy / XL"), SmartEtailing keeps it in the
                # page's spec table keyed by UPC.
                variant_options = (
                    _options_from_label(sku)
                    or _options_from_label(spec_labels.get(_gtin_key(gtin)))
                )
            rows.append(_normalized_listing(
                title=source.get("name") or product.get("name"), brand=_brand_name(source.get("brand")) or brand,
                sku=str(sku) if sku else None, gtin=gtin,
                variant_id=str(source.get("productID")) if source.get("productID") else None,
                variant_options=variant_options,
                price=low if is_range else price, price_low=low, price_high=high,
                currency=fields.get("currency"), in_stock=fields.get("in_stock"),
                price_scope="range" if is_range else ("variant" if (variants or offers_in_source > 1) else "product"),
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

class _CrawlStatsMixin:
    """Cursor + consumption counters every catalog connector exposes after
    iter_products finishes. The runner persists them per competitor
    (pi_competitors.crawl_state_json) so the next night's crawl resumes where
    this one stopped instead of re-crawling the same first slice forever."""

    def _init_stats(self, start_cursor):
        self.pages_done = 0        # pages/product-pages actually fetched
        self.products_seen = 0     # products (JSON) / listings (HTML) yielded
        self.cap_hit = False       # budget exhausted before the catalog ended
        self.next_cursor = start_cursor  # where tomorrow's crawl should start

    @property
    def stats(self) -> _FetchStats:
        """HTTP outcomes for this crawl (lazily created so subclasses don't all
        need to remember to build one in __init__)."""
        existing = getattr(self, "_stats", None)
        if existing is None:
            existing = _FetchStats()
            self._stats = existing
        return existing

    def diagnostics(self) -> dict:
        """Everything the health panel needs to explain a disappointing crawl:
        how many URLs the sitemaps offered, how many survived each filter, what
        share carried a tracked brand, and what the site actually answered."""
        return {
            **self.stats.as_dict(),
            "sitemap_urls_seen": getattr(self, "sitemap_urls_seen", 0),
            "candidates_shape_ok": getattr(self, "candidates_shape_ok", 0),
            "candidates_crawlable": getattr(self, "candidates_crawlable", 0),
            "off_domain_dropped": getattr(self, "off_domain_dropped", 0),
            "brand_hit_rate": getattr(self, "brand_hit_rate", None),
            "brand_gate_applied": getattr(self, "brand_gate_applied", None),
            "targeted_candidates": getattr(self, "targeted_candidates", 0),
            "targeted_pages_done": getattr(self, "targeted_pages_done", 0),
            # Hunted tokens that turned out to be this store's house vocabulary.
            "common_tokens_dropped": list(getattr(self, "common_tokens_dropped", [])),
        }


def _rotate(urls: list, offset: int) -> list:
    """urls[offset:] + urls[:offset] with a safe modulo — rotation order for
    cursor-based crawls over a (sorted, deterministic) candidate list."""
    if not urls:
        return urls
    offset = offset % len(urls)
    return urls[offset:] + urls[:offset]


class ShopifyJsonConnector(_CrawlStatsMixin):
    """Iterates a Shopify store's public /products.json, yielding one record per
    variant. Generator-paginated so at most one page (250 products) is in memory.
    `start_page` (from the stored crawl cursor) rotates the MAX_CATALOG_PAGES
    budget through catalogs larger than the cap."""

    connector_type = "shopify_json"

    def __init__(self, base_url: str, start_page: int = 1, settings=None):
        self.base_url = base_url.rstrip("/")
        self.start_page = max(1, int(start_page or 1))
        self.settings = settings if isinstance(settings, CrawlSettings) else CrawlSettings(settings)

    def iter_products(self) -> Iterator[dict]:
        self._init_stats(start_cursor=self.start_page)  # error mid-run → retry same slice
        page = self.start_page
        budget = self.settings.max_catalog_pages
        while budget > 0:
            url = f"{self.base_url}/products.json?limit=250&page={page}"
            resp = polite_get(url, interval=self.settings.request_interval, stats=self.stats)
            if resp is None or resp.status_code != 200:
                return
            try:
                products = resp.json().get("products", [])
            except ValueError:
                return
            if not products:
                if page > 1 and page == self.start_page:
                    # Catalog shrank below the stored cursor: restart from the
                    # front tonight instead of wasting the night on empty pages.
                    page = 1
                    continue
                self.next_cursor = 1  # catalog ended — wrap for tomorrow
                return
            self.pages_done += 1
            self.products_seen += len(products)
            budget -= 1
            page += 1
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
        # Budget consumed without seeing the catalog end: resume here tomorrow.
        self.cap_hit = True
        self.next_cursor = page


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


# ---------------------------------------------------------------------------
# Crawl targeting: what the nightly page budget should be spent looking for
# ---------------------------------------------------------------------------

# Slug tokens that carry no model signal. Half a bike shop's catalog is a black
# medium 2024 carbon road bike, so scoring on these words would float noise to
# the top of the crawl order and bury the tokens that actually discriminate.
# Brand tokens are matched separately and are never stoplisted here.
_GENERIC_SLUG_TOKENS = frozenset("""
bike bikes bicycle bicycles cycle cycles cycling road mountain mtb gravel
hybrid commuter touring electric ebike kids kid youth junior mens womens
men women unisex adult
frame frameset fork forks wheel wheels wheelset tire tyre tires tyres tube
tubes rim rims spoke spokes hub hubs
helmet helmets glove gloves shoe shoes jersey jerseys short shorts bib bibs
jacket jackets vest vests sock socks cap caps eyewear glasses
pedal pedals saddle saddles seatpost seatposts stem stems bar bars handlebar
handlebars grip grips tape
chain chains cassette cassettes derailleur derailleurs brake brakes rotor
rotors pad pads lever levers shifter shifters crank cranks chainring bottom
bracket headset
pump pumps light lights lock locks rack racks bag bags bottle bottles cage
cages tool tools kit kits fender fenders trainer
new sale clearance used demo sold out shop product products collection item
black white red blue green grey gray silver yellow orange pink purple brown
beige tan navy teal olive
matte gloss glossy carbon alloy aluminum aluminium steel titanium chrome
xs sm md lg xl xxl xxxl small medium large size sizes wide narrow regular
and the for with without from plus incl including set pack pair
pro expert elite comp sport advanced performance premium base core standard
team edition series gen generation version model unit kit upgrade replacement
smart wireless bluetooth ant usb charging cable mount adapter spare
""".split())

# Drivetrain, groupset and spec vocabulary. These read like model names but sit
# on hundreds of unrelated accessory pages — the Oak Bay crawl surfaced charging
# cables and brake pads ahead of the bikes we actually track because they name
# "dura ace ultegra di2". Scoring on them buries the real model tokens.
_GENERIC_SLUG_TOKENS |= frozenset("""
shimano sram campagnolo microshift box
dura ace ultegra tiagra sora claris grx xtr xt slx deore cues
red force rival apex eagle transmission axs etap di2 epsi
speed spd hydraulic mechanical electronic tubeless tubetype clincher
power meter watt torque wrench cadence sensor computer radar
mips spherical wavecel fidlock boa
""".split())

# Bare years ("2024") appear on most of a catalog and discriminate nothing.
_YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")

# Weights. A brand hit says "we stock this brand"; a model token says "this may
# be the exact item we still can't price", which is the whole point of ranking.
BRAND_TOKEN_SCORE = 1
MODEL_TOKEN_SCORE = 3


def _slug_tokens(text) -> set:
    """Distinctive slug tokens from a title or URL path.

    Accent-folded, split on any non-alphanumeric, with generic vocabulary and
    bare years dropped. Alphanumeric model codes ("gp5000", "slr01", "aeroad")
    survive deliberately — they're the strongest signal a URL can carry, and
    they're often present on exactly the stores that leave the brand out of the
    slug entirely.
    """
    tokens = set()
    for token in re.split(r"[^a-z0-9]+", _fold_slug(text)):
        if len(token) < 3 or token in _GENERIC_SLUG_TOKENS:
            continue
        if _YEAR_TOKEN_RE.match(token):
            continue
        if token.isdigit() and len(token) < 5:
            # Sizes, counts, pack quantities. Longer digit runs can be genuine
            # model numbers (and product-id URLs), so those are kept.
            continue
        tokens.add(token)
    return tokens


class CrawlTargets:
    """What a catalog crawl is hunting for.

    `brand_tokens` is the long-standing brand-slug signal. `model_tokens` is
    the new one: distinctive tokens from tracked items that have no confirmed
    link on this competitor yet, so the nightly page budget goes to the items we
    still can't price instead of walking the catalog alphabetically.
    """

    def __init__(self, brand_names=None, model_tokens=None):
        self.brand_names = sorted({b for b in (brand_names or []) if b})
        self.brand_tokens = _brand_slug_tokens(self.brand_names)
        self.model_tokens = frozenset(model_tokens or ())

    @classmethod
    def from_items(cls, items: dict, unmatched_ids=None) -> "CrawlTargets":
        """`items` is MatchIndex.items (item_id -> row). `unmatched_ids` limits
        the model-token side to items still lacking a confirmed link on the
        competitor about to be crawled; None means every tracked item counts."""
        return ItemTokenIndex(items).targets_for(unmatched_ids)

    def score_parts(self, path: str):
        """(brand hit as 0/1, distinct model-token hits) for a candidate path.

        Kept separate because the two mean different things to the crawl order:
        a brand hit is already the gate, so on its own it can't promote a URL
        past the rotation sweep — only a model hit can.
        """
        brand = 1 if self.has_brand(path) else 0
        models = len(_slug_tokens(path) & self.model_tokens) if self.model_tokens else 0
        return brand, models

    def score(self, path: str) -> int:
        """Relevance of a candidate product URL to what we still need to price."""
        brand, models = self.score_parts(path)
        return brand * BRAND_TOKEN_SCORE + models * MODEL_TOKEN_SCORE

    def has_brand(self, path: str) -> bool:
        return bool(self.brand_tokens) and any(tok in path for tok in self.brand_tokens)


class ItemTokenIndex:
    """Per-item slug tokens, computed once per run.

    Crawl targets are per competitor (an item linked on store A is still being
    hunted on store B), so without this the whole tracked list would be
    re-tokenized for every store in the loop."""

    def __init__(self, items: dict):
        self.brands = sorted({(row.get("brand") or "").strip()
                              for row in (items or {}).values()} - {""})
        # A brand name is not a model signal: it scores every page of that brand
        # identically and would drown the tokens that actually pick one product
        # out of the brand's shelf.
        brand_tokens = {t for brand in self.brands for t in _slug_tokens(brand)}
        self.tokens_by_item = {
            str(item_id): _slug_tokens(row.get("title")) - brand_tokens
            for item_id, row in (items or {}).items()
        }

    def targets_for(self, unmatched_ids=None) -> "CrawlTargets":
        """CrawlTargets whose model tokens come from `unmatched_ids` only
        (None = every tracked item)."""
        models = set()
        for item_id, tokens in self.tokens_by_item.items():
            if unmatched_ids is None or item_id in unmatched_ids:
                models |= tokens
        return CrawlTargets(brand_names=self.brands, model_tokens=models)


def _compile_pattern(pattern) -> Optional[re.Pattern]:
    """User-supplied URL regex from the competitor's settings. A bad pattern is
    ignored with a log line rather than killing the whole run."""
    if not pattern or not str(pattern).strip():
        return None
    try:
        return re.compile(str(pattern), re.I)
    except re.error as e:
        print(f"pi: ignoring invalid crawl URL pattern {pattern!r}: {e}")
        return None


def _positive_number(value, cast):
    try:
        parsed = cast(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class CrawlSettings:
    """Per-competitor crawl overrides (pi_competitors.settings_json).

    Every key is optional and falls back to its config.py global, so a
    competitor with no settings behaves exactly as it did before this existed.
    """

    DEFAULT_MAX_SITEMAP_FETCHES = 15
    DEFAULT_MAX_CANDIDATE_URLS = 20000

    def __init__(self, raw=None):
        data = raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw) if raw.strip() else {}
            except ValueError:
                print("pi: ignoring unparseable competitor settings_json")
                data = {}
        if not isinstance(data, dict):
            data = {}
        self.raw = data
        # Your avenue #1: a positive path shape filter, per competitor. The
        # global NON_PRODUCT_RE deny-list still applies; url_deny_pattern adds
        # to it rather than replacing it.
        self.allow_pattern = _compile_pattern(data.get("url_allow_pattern"))
        self.deny_pattern = _compile_pattern(data.get("url_deny_pattern"))
        brand_filter = str(data.get("brand_filter") or "auto").strip().lower()
        self.brand_filter = brand_filter if brand_filter in ("auto", "on", "off") else "auto"
        self.request_interval = _positive_number(
            data.get("request_interval_seconds"), float)
        # Overrides are resolved eagerly, fallbacks lazily (below) so the global
        # stays live — env changes and test patches of config.* still take.
        self._max_product_pages = _positive_number(data.get("max_product_pages"), int)
        self._max_catalog_pages = _positive_number(data.get("max_catalog_pages"), int)
        self.max_sitemap_fetches = (_positive_number(data.get("max_sitemap_fetches"), int)
                                    or self.DEFAULT_MAX_SITEMAP_FETCHES)
        self.max_candidate_urls = (_positive_number(data.get("max_candidate_urls"), int)
                                   or self.DEFAULT_MAX_CANDIDATE_URLS)
        # Escape hatch for stores whose robots.txt/sitemap.xml discovery fails
        # but whose sitemap lives at a known URL.
        self.sitemap_urls = [u.strip() for u in (data.get("sitemap_urls") or [])
                             if isinstance(u, str) and u.strip()]
        # Off only for the rare store that legitimately serves products from a
        # second host; on for everyone else.
        self.confine_to_domain = data.get("confine_to_domain", True) is not False

    @property
    def max_product_pages(self) -> int:
        return self._max_product_pages or config.MAX_HTML_PRODUCT_PAGES

    @property
    def max_catalog_pages(self) -> int:
        return self._max_catalog_pages or config.MAX_CATALOG_PAGES

    def path_allowed(self, path: str) -> bool:
        if self.allow_pattern is not None and not self.allow_pattern.search(path):
            return False
        if self.deny_pattern is not None and self.deny_pattern.search(path):
            return False
        return True


class _HtmlPageCrawler(_CrawlStatsMixin):
    """Shared candidate-selection and fetch/parse loop for the connectors that
    crawl individual product pages.

    Candidates are filtered by *scope* (same site, product-shaped path, English
    CA locale), then ordered by *relevance* rather than filtered by it: pages
    whose slug mentions a tracked item we still can't price are crawled first
    (the head), and the remainder is swept alphabetically from the stored cursor
    (the tail) so the rest of the catalog is still covered night over night.

    Brand is a ranking and gating signal, never a hard drop on its own — see
    _apply_brand_gate for why."""

    # Head/tail budget split. The head is where the value is, but letting it
    # take the whole night would freeze the sweep, and the sweep is what finds
    # products whose slug spells the model differently than we do.
    HEAD_BUDGET_SHARE = 0.6

    # Share/absolute-count floors below which the brand gate is self-defeating.
    BRAND_HIT_RATE_FLOOR = 0.02
    BRAND_HIT_ABS_FLOOR = 25

    # A hunted token on more than this share of a store's candidates (but at
    # least this many pages) is that store's house vocabulary, not a model name.
    COMMON_TOKEN_SHARE = 0.02
    COMMON_TOKEN_FLOOR = 20

    # Subclass deny-list for paths that are never product detail pages.
    NON_PRODUCT_RE = None

    def _init_crawler(self, base_url, brand_tokens=None, start_offset=0, settings=None):
        """Shared __init__ tail. `brand_tokens` accepts either a plain list of
        brand names (the long-standing form) or a CrawlTargets carrying model
        tokens as well."""
        self.base_url = base_url.rstrip("/")
        self.settings = settings if isinstance(settings, CrawlSettings) else CrawlSettings(settings)
        self.targets = (brand_tokens if isinstance(brand_tokens, CrawlTargets)
                        else CrawlTargets(brand_names=brand_tokens))
        self._brand_names = self.targets.brand_names
        self.brand_tokens = self.targets.brand_tokens
        self.start_offset = max(0, int(start_offset or 0))
        self.sitemap_urls_seen = 0
        self.candidates_shape_ok = 0
        self.candidates_crawlable = 0
        self.off_domain_dropped = 0
        self.brand_hit_rate = None
        self.brand_gate_applied = None
        self.targeted_candidates = 0
        self.targeted_pages_done = 0
        self.common_tokens_dropped = []

    def _get(self, url: str, **kwargs):
        """polite_get bound to this competitor's crawl delay and stats."""
        return polite_get(url, interval=self.settings.request_interval,
                          stats=self.stats, **kwargs)

    # -- candidate selection ------------------------------------------------

    def _url_in_scope(self, url: str) -> bool:
        """One candidate URL against every scope filter: same site, product-shaped
        path, English-CA locale, per-competitor allow/deny patterns. Brand is
        deliberately absent — it's handled later, and conditionally."""
        if self.settings.confine_to_domain and not _same_site(url, self.base_url):
            self.off_domain_dropped += 1
            return False
        path = urlparse(url).path.lower()
        if not path or path == "/":
            return False
        if self.NON_PRODUCT_RE is not None and self.NON_PRODUCT_RE.search(path):
            return False
        if NON_ENGLISH_PATH_RE.search(path):
            return False
        if not self.settings.path_allowed(path):
            return False
        self.candidates_shape_ok += 1
        return True

    def _collect_candidates(self, url_iter) -> list:
        """Scope-filtered candidates, with brand-matching URLs kept preferentially.

        The candidate cap used to apply after the brand filter. Now that the
        brand gate runs later, a huge catalog could push every brand-relevant URL
        out of the list before the gate ever saw it — so hits and misses go in
        separate buckets and the cap bites on the misses first."""
        hits, misses = [], []
        cap = self.settings.max_candidate_urls
        for url in url_iter:
            self.sitemap_urls_seen += 1
            if not self._url_in_scope(url):
                continue
            if self.targets.has_brand(urlparse(url).path.lower()):
                hits.append(url)
            elif len(misses) < cap:
                misses.append(url)
            if len(hits) >= cap:
                break
        if self.targets.brand_tokens and self.candidates_shape_ok:
            self.brand_hit_rate = round(len(hits) / self.candidates_shape_ok, 4)
        return prefer_ca_english(self._apply_brand_gate(hits, misses))

    def _apply_brand_gate(self, hits: list, misses: list) -> list:
        """The brand gate, applied only where it isn't self-defeating.

        Dropping every URL without a tracked brand in its path is a good filter
        on stores that put brands in slugs, and catastrophic on stores that don't
        (/product/12345-carbon-wheelset): it discards the entire catalog, and the
        run reports "no products", which reads exactly like a broken sitemap. So
        when almost nothing carries a brand, keep everything and let the shape
        filter and relevance ranking choose the pages instead."""
        mode = self.settings.brand_filter
        if not self.targets.brand_tokens or mode == "off":
            self.brand_gate_applied = False
            return hits + misses
        if mode == "on":
            self.brand_gate_applied = True
            return hits
        gate_is_futile = (len(hits) < self.BRAND_HIT_ABS_FLOOR
                          and (self.brand_hit_rate or 0.0) < self.BRAND_HIT_RATE_FLOOR)
        self.brand_gate_applied = not gate_is_futile
        if gate_is_futile:
            print(f"pi: {self.base_url} puts almost no tracked brands in its product "
                  f"URLs ({len(hits)} of {self.candidates_shape_ok}) — crawling on "
                  "relevance rank instead of the brand filter")
            return hits + misses
        return hits

    def _discriminating_tokens(self, paths) -> frozenset:
        """The hunted model tokens that actually single a page out on this store.

        The stoplist catches vocabulary that's generic everywhere; this catches
        vocabulary that's generic *here*. A token sitting on a large share of a
        store's catalog can't distinguish anything on it, and hunting it floods
        the head with near-identical accessory pages. Measured per store, so no
        per-store list has to be maintained by hand."""
        hunted = self.targets.model_tokens
        if not hunted or not paths:
            return hunted
        frequency = Counter()
        for path in paths:
            frequency.update(_slug_tokens(path) & hunted)
        limit = max(self.COMMON_TOKEN_FLOOR, int(len(paths) * self.COMMON_TOKEN_SHARE))
        common = {token for token, n in frequency.items() if n > limit}
        self.common_tokens_dropped = sorted(common)
        return frozenset(hunted - common)

    def _rank_candidates(self, candidates: list):
        """Split candidates into a relevance head and an alphabetical tail.

        Only a model-token hit promotes a URL into the head. A brand hit alone
        must not: with the gate on, every candidate carries a brand, so scoring
        on it would make the head the whole list and the rotation cursor would
        stop advancing — re-crawling the same alphabetical slice forever, which
        is the behaviour the cursor exists to prevent."""
        self.candidates_crawlable = len(candidates)
        # Paths are re-derived rather than kept alongside a token set per URL:
        # 20k retained token sets is memory this process doesn't have to spare.
        paths = [urlparse(url).path.lower() for url in candidates]
        hunted = self._discriminating_tokens(paths)
        scored, plain = [], []
        for url, path in zip(candidates, paths):
            models = len(_slug_tokens(path) & hunted) if hunted else 0
            if models:
                brand = BRAND_TOKEN_SCORE if self.targets.has_brand(path) else 0
                scored.append((brand + models * MODEL_TOKEN_SCORE, url))
            else:
                plain.append(url)
        # Deterministic across nights: score desc, then URL.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [url for _, url in scored], sorted(plain)

    # -- fetch loop ---------------------------------------------------------

    def _fetch_and_parse(self, url: str) -> Iterator[dict]:
        slug = urlparse(url).path.lower()
        resp = polite_get(url, interval=self.settings.request_interval, stats=self.stats)
        if resp is None or resp.status_code != 200:
            return
        self.pages_done += 1
        for parsed in (_magento_graphql_listings(resp.text, url)
                       or extract_listings(resp.text, url)):
            if not parsed.get("brand"):
                parsed["brand"] = _slug_brand(slug, self._brand_names)
            parsed["url"] = url
            parsed.setdefault("compare_at_price", None)
            self.products_seen += 1
            yield parsed

    def _crawl_candidates(self, candidates: list) -> Iterator[dict]:
        """Fetch and parse candidates, highest relevance first.

        Head then tail, so both jobs get done: the head hunts the specific items
        we still can't price, the tail keeps sweeping the catalog from the stored
        cursor. Only tail progress moves the cursor — the head is re-derived from
        the unmatched set every night, so counting it would drift the cursor and
        leave stretches of the catalog never visited."""
        head, tail = self._rank_candidates(candidates)
        total = len(tail)
        self._init_stats(start_cursor=(self.start_offset % total) if total else 0)
        self.targeted_candidates = len(head)
        budget = self.settings.max_product_pages

        # The head gets the whole budget only when there is no tail to starve.
        head_budget = int(budget * self.HEAD_BUDGET_SHARE) if tail else budget
        head_truncated = len(head) > head_budget
        for url in head[:head_budget]:
            yield from self._fetch_and_parse(url)
        self.targeted_pages_done = self.pages_done

        attempted = 0
        for url in _rotate(tail, self.start_offset):
            if self.pages_done >= budget:
                self.cap_hit = True
                self.next_cursor = (self.start_offset + attempted) % total
                return
            attempted += 1
            yield from self._fetch_and_parse(url)
        self.next_cursor = 0  # whole tail visited — start at the front next time
        # A truncated head still means we didn't cover everything we wanted to.
        self.cap_hit = self.cap_hit or head_truncated


class ShopifyHtmlConnector(_HtmlPageCrawler):
    """Fallback for stores that block /products.json: walk the products sitemap and
    parse each product page's JSON-LD (which usually includes a real GTIN).

    One request per product, so the candidate list is scope-filtered and the
    crawl is capped per night, spending its budget on the pages most likely to
    be items we still can't price (see _HtmlPageCrawler)."""

    connector_type = "shopify_html"

    def __init__(self, base_url: str, brand_tokens=None, start_offset: int = 0,
                 settings=None):
        self._init_crawler(base_url, brand_tokens, start_offset, settings)

    def _product_urls(self) -> Iterator[str]:
        product_sitemaps = list(self.settings.sitemap_urls)
        if not product_sitemaps:
            resp = self._get(f"{self.base_url}/sitemap.xml")
            if resp is None or resp.status_code != 200:
                return
            product_sitemaps = re.findall(
                r"<loc>([^<]*sitemap_products[^<]*)</loc>", resp.text)
        for sitemap_url in product_sitemaps:
            sm = self._get(sitemap_url.strip())
            if sm is None or sm.status_code != 200:
                continue
            for loc in re.findall(r"<loc>([^<]+)</loc>", sm.text):
                loc = loc.strip()
                if "/products/" in loc:
                    yield loc

    def iter_products(self) -> Iterator[dict]:
        # Locale confinement, domain confinement and the brand gate all live in
        # _collect_candidates now — Shopify Markets stores emit localized URL
        # variants (/fr-ca/products/…, /en-us/…) that must not be crawled.
        yield from self._crawl_candidates(self._collect_candidates(self._product_urls()))


# Locale confinement for multi-geo/multi-language storefronts. We sell in
# English CAD, so: non-English locales are never crawled, and when a site
# publishes the same catalog under several English geos (/en-ca/, /en-us/, …)
# only the Canadian/neutral one is — a US page's price would be USD and (since
# the sql_cad_only guard) discarded anyway, so fetching it wastes page budget.
#
# Two strictness levels. Page-URL paths only match a *complete* path segment
# ("/fr/", "/fr-ca/", "/en_ca/") — product slugs like /de-rosa-frame or
# /no-tubes-sealant must never read as German/Norwegian. Sitemap filenames use
# the looser delimiter form so "sitemap_fr.xml" / "1_fr_0.xml" / ".fr/" hit.
_NON_ENGLISH_LANGS = r"(?:fr|de|es|it|nl|pt|pl|sv|da|fi|no|ja|ko|zh)(?:[-_][a-z]{2})?"
NON_ENGLISH_PATH_RE = re.compile(rf"(?:^|/){_NON_ENGLISH_LANGS}(?=/|$)", re.I)
NON_ENGLISH_SITEMAP_RE = re.compile(rf"[/_.-]{_NON_ENGLISH_LANGS}(?=[/_.-]|$)", re.I)
# English-but-not-Canadian geo prefixes (en-us, en-gb, …); bare /en/ is kept.
NON_CA_EN_PATH_RE = re.compile(r"(?:^|/)en[-_](?!ca(?=/|$))[a-z]{2}(?=/|$)", re.I)
NON_CA_EN_SITEMAP_RE = re.compile(r"[/_.-]en[-_](?!ca(?=[/_.-]|$))[a-z]{2}(?=[/_.-]|$)", re.I)
CA_HINT_RE = re.compile(r"[/_.-](?:en[-_]ca|ca)(?=[/_.-]|$)", re.I)


def prefer_ca_english(urls: list, non_ca_re=NON_CA_EN_PATH_RE) -> list:
    """Keeps the Canadian/neutral-English subset of a multi-geo URL list.

    Other-geo English URLs (/en-us/…) are dropped only when CA/neutral
    counterparts exist, so a store that publishes exclusively under /en-us/
    still gets crawled. (Non-English URLs are filtered before this point.)
    Pass NON_CA_EN_SITEMAP_RE when the list holds sitemap-file URLs."""
    non_ca = [u for u in urls if non_ca_re.search(u)]
    if non_ca and len(non_ca) < len(urls):
        dropped = set(non_ca)
        return [u for u in urls if u not in dropped]
    return urls


class GenericSitemapConnector(_HtmlPageCrawler):
    """Catalog crawl for non-Shopify stores (Magento, headless storefronts, ...)
    via their public sitemaps: robots.txt `Sitemap:` lines + /sitemap.xml, one
    level of <sitemapindex> nesting. Page URLs are filtered to tracked-brand
    slugs and the Canadian/English locale, then parsed with parse_product_page,
    so it behaves exactly like the shopify_html crawl — one request per
    candidate page, capped."""

    connector_type = "sitemap_html"

    # Path segments that are never product detail pages.
    NON_PRODUCT_RE = re.compile(
        r"/(collections?|categor(y|ies)|cms|blogs?|pages?|news|apps)(/|$)"
    )

    def __init__(self, base_url: str, brand_tokens=None, start_offset: int = 0,
                 settings=None):
        self._init_crawler(base_url, brand_tokens, start_offset, settings)

    def _sitemap_sources(self) -> list:
        """Sitemap URLs from robots.txt plus the conventional /sitemap.xml.
        Prefers product-hinting filenames, and _en_ over _fr_ duplicates.

        A per-competitor sitemap_urls override short-circuits discovery entirely
        — the escape hatch for stores whose robots.txt hides it."""
        if self.settings.sitemap_urls:
            return list(self.settings.sitemap_urls)
        sources = []
        resp = self._get(f"{self.base_url}/robots.txt", respect_robots=False)
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
        # Locale confinement: drop non-English sitemap files when an English or
        # locale-neutral one exists, drop other-geo English duplicates when a
        # CA/neutral one exists, and spend the fetch budget on Canadian-hinting
        # maps first.
        english = [u for u in maps if not NON_ENGLISH_SITEMAP_RE.search(u)]
        maps = english or maps
        maps = prefer_ca_english(maps, NON_CA_EN_SITEMAP_RE)
        maps.sort(key=lambda u: 0 if CA_HINT_RE.search(u) else 1)
        return maps

    def _iter_page_urls(self) -> Iterator[str]:
        fetches = 0
        queue = self._sitemap_sources()
        seen_pages = set()
        while queue and fetches < self.settings.max_sitemap_fetches:
            sitemap_url = queue.pop(0)
            resp = self._get(sitemap_url.strip())
            fetches += 1
            if resp is None or resp.status_code != 200 or "<" not in resp.text[:200]:
                continue
            locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
            if "<sitemapindex" in resp.text:
                # One level of nesting: children go on the queue with the same
                # locale confinement as the top-level sources, product-hinting
                # and Canadian-hinting first so the fetch budget is spent well.
                english = [u for u in locs if not NON_ENGLISH_SITEMAP_RE.search(u)]
                locs = prefer_ca_english(english or locs, NON_CA_EN_SITEMAP_RE)
                locs.sort(key=lambda u: (
                    "product" not in u.lower(),
                    0 if CA_HINT_RE.search(u) else 1,
                ))
                queue.extend(locs)
                continue
            for loc in locs:
                loc = loc.strip()
                if loc in seen_pages:
                    continue
                seen_pages.add(loc)
                yield loc

    def _candidate_page_urls(self) -> list:
        """Sitemap page URLs worth fetching: on-site, product-shaped, English,
        and geo-deduped. Materialized (URLs only, a few MB worst case) so the
        CA-vs-other-geo preference and the brand gate can both see the whole
        list before the page fetch budget is spent."""
        return self._collect_candidates(self._iter_page_urls())

    def iter_products(self) -> Iterator[dict]:
        yield from self._crawl_candidates(self._candidate_page_urls())


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


def build_connector(competitor: dict, brand_tokens=None, cursor: int = 0):
    """`cursor` is the stored per-competitor crawl position (crawl_state_json):
    a 1-based /products.json page for shopify_json, a tail-list offset for the
    HTML crawls. 0/absent means start from the front.

    `brand_tokens` may be a plain list of brand names or a CrawlTargets carrying
    the model tokens of items we still can't price on this competitor."""
    ctype = competitor.get("connector_type")
    base_url = competitor["base_url"]
    settings = CrawlSettings(competitor.get("settings_json"))
    if ctype == "shopify_json":
        return ShopifyJsonConnector(base_url, start_page=cursor or 1, settings=settings)
    if ctype == "shopify_html":
        return ShopifyHtmlConnector(base_url, brand_tokens=brand_tokens,
                                    start_offset=cursor, settings=settings)
    if ctype == "sitemap_html":
        return GenericSitemapConnector(base_url, brand_tokens=brand_tokens,
                                       start_offset=cursor, settings=settings)
    return None
