"""Google Merchant Center market benchmark, ingested as a synthetic competitor.

Merchant Center computes two prices per product we already submit in our feed:

  * price_competitiveness_product_view.benchmark_price — the click-weighted average
    price across every merchant advertising the same product on Shopping. This is
    the market benchmark; it is matched on GTIN, so coverage tracks how many of our
    items carry a real barcode (good on P&A, thin on complete bikes).
  * price_insights_product_view.suggested_price — Google's modelled price, optimized
    for *our ad performance* rather than describing the market. Sparser still: it
    needs reported conversions and only appears where Google sees an opportunity.

Both land in pi_price_observations as two fixed pseudo-competitors, so they render
in the per-store breakdown, the price chart, and the competitor filter exactly like a
scraped storefront — with no new table. They are display-only: repository's
sql_market_sources() keeps them out of market-min / position / undercut / MAP math,
and this module never emits change events, so they raise no alerts. See the module
comment on repository.SYNTHETIC_SOURCES for why.

Google only ever exposes the *latest* value, so history is built by appending one
snapshot per run — the same approach as pi_our_price_history.

Merchant Center's terms forbid reselling or publicly displaying this data; it is for
internal pricing decisions only.
"""
import json
import logging
import re

from . import config, repository

logger = logging.getLogger(__name__)

CONTENT_SCOPE = "https://www.googleapis.com/auth/content"

# Fixed ids so the pseudo-competitors are the same rows in every environment and
# survive re-seeding (a uuid would create a duplicate store on each deploy).
BENCHMARK_COMPETITOR_ID = "google-benchmark"
SUGGESTED_COMPETITOR_ID = "google-suggested"

_COMPETITORS = {
    BENCHMARK_COMPETITOR_ID: {
        "name": "Google Market Benchmark",
        "base_url": "https://merchants.google.com/benchmark",
        "notes": "Click-weighted average price across merchants advertising the same "
                 "GTIN on Google Shopping. Reference only — excluded from market-min, "
                 "price position, and alerting.",
    },
    SUGGESTED_COMPETITOR_ID: {
        "name": "Google Suggested Price",
        "base_url": "https://merchants.google.com/suggested",
        "notes": "Google's modelled price recommendation, optimized for ad performance "
                 "rather than describing the market. Reference only — excluded from "
                 "market-min, price position, and alerting.",
    },
}

BENCHMARK_QUERY = """
    SELECT report_country_code, id, offer_id, title, brand, price, benchmark_price
    FROM price_competitiveness_product_view
    WHERE report_country_code = '{country}'
"""

INSIGHTS_QUERY = """
    SELECT id, offer_id, title, brand, price, suggested_price, effectiveness,
           predicted_clicks_change_fraction
    FROM price_insights_product_view
"""

_DIGITS = re.compile(r"\D")


class BenchmarkUnavailable(Exception):
    """Configuration is missing or unusable — the phase is skipped, not failed."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _merchant_credentials():
    """Explicit service-account credentials for the Merchant API.

    Deliberately not ADC: GOOGLE_APPLICATION_CREDENTIALS points at the BigQuery
    service account, which has no Merchant Center access, and repointing it would
    break every BigQuery call in the app. This is the separate SA already registered
    in Merchant Center, supplied either as a path to its JSON key (local dev, or a
    Render secret file) or as the JSON itself (a plain Render env var).
    """
    from google.oauth2 import service_account

    raw = (config.GOOGLE_MERCHANT_CREDENTIALS or "").strip()
    if not raw:
        raise BenchmarkUnavailable("PI_GOOGLE_MERCHANT_CREDENTIALS is not set")
    try:
        if raw.startswith("{"):
            creds = service_account.Credentials.from_service_account_info(json.loads(raw))
        else:
            creds = service_account.Credentials.from_service_account_file(raw)
    except (ValueError, OSError, KeyError) as exc:
        raise BenchmarkUnavailable(f"Merchant Center credentials are unusable: {exc}") from exc
    return creds.with_scopes([CONTENT_SCOPE])


def _report_client():
    try:
        from google.shopping.merchant_reports_v1 import ReportServiceClient
    except ImportError as exc:  # dependency not installed in this deployment
        raise BenchmarkUnavailable(
            "google-shopping-merchant-reports is not installed"
        ) from exc
    if not config.GOOGLE_MERCHANT_ID:
        raise BenchmarkUnavailable("PI_GOOGLE_MERCHANT_ID is not set")
    return ReportServiceClient(credentials=_merchant_credentials())


# ---------------------------------------------------------------------------
# Offer id -> Lightspeed item_id
# ---------------------------------------------------------------------------

def normalize_gtin(value) -> str:
    """Digits only, leading zeros stripped — so a 12-digit UPC equals its
    13-digit zero-padded EAN. Same convention as matcher.py and seeding.py."""
    if value is None:
        return ""
    return _DIGITS.sub("", str(value)).lstrip("0")


def _offer_id_from_report_id(report_id: str) -> str:
    """Last segment of the report's composite id (channel/language/feed/offer).

    The Merchant API joins those with tildes; the BigQuery transfer of the same
    report uses colons. Split on both so this doesn't depend on the ingest path.
    """
    if not report_id:
        return ""
    return re.split(r"[~:]", str(report_id))[-1].strip()


class OfferResolver:
    """Maps a Merchant Center offer id to a Lightspeed item_id.

    Our feed's offer ids come from Shopify (the Google & YouTube app emits
    `shopify_{country}_{productId}_{variantId}`), so the bridge is Shopify's
    variant id or sku, which repository.get_google_offer_map() has already joined
    to Lightspeed on sku == system_sku. The format is not hard-coded: each
    candidate key is tried in turn, cheapest and most precise first.

    Note there is no GTIN fallback: neither price_competitiveness_product_view nor
    price_insights_product_view returns a gtin field, so there is nothing to key one
    on. Unresolved offers are counted instead — that number is the honest coverage
    signal, and a persistently high one means the feed's offer id format changed.
    """

    def __init__(self, rows=None):
        rows = repository.get_google_offer_map() if rows is None else rows
        self.by_variant, self.by_sku = {}, {}
        for r in rows:
            item_id = r.get("item_id")
            if not item_id:
                continue
            if r.get("variant_id"):
                self.by_variant[str(r["variant_id"])] = item_id
            if r.get("sku"):
                self.by_sku[str(r["sku"]).strip().lower()] = item_id

    def resolve(self, offer_id: str, report_id: str = ""):
        """Returns (item_id, how) — how is None when unresolved."""
        for value in ((offer_id or "").strip(), _offer_id_from_report_id(report_id)):
            if not value:
                continue
            # `shopify_CA_{productId}_{variantId}` -> the variant id; a bare
            # numeric offer id is already one.
            trailing = value.rsplit("_", 1)[-1]
            if trailing.isdigit() and trailing in self.by_variant:
                return self.by_variant[trailing], "variant_id"
            if value.lower() in self.by_sku:
                return self.by_sku[value.lower()], "sku"
        return None, None


# ---------------------------------------------------------------------------
# Report rows -> observation rows
# ---------------------------------------------------------------------------

def _price_parts(price):
    """(amount, currency) from a Merchant API Price message.

    The new API nests these; the retired Content API v2.1 exposed flat
    *_micros / *_currency_code scalars, so accept either shape rather than
    breaking on a client-library difference.
    """
    if price is None:
        return None, None
    micros = getattr(price, "amount_micros", None)
    currency = getattr(price, "currency_code", None)
    if micros is None and isinstance(price, dict):
        micros = price.get("amount_micros") or price.get("amountMicros")
        currency = price.get("currency_code") or price.get("currencyCode")
    if micros in (None, 0) and not currency:
        return None, None
    return (round(int(micros) / 1_000_000, 2) if micros is not None else None,
            (currency or "").upper() or None)


def build_observation(view, *, run_id, observed_at, item_id, competitor_id,
                      source, price, currency, country=None):
    """One pi_price_observations row for a resolved Google report row.

    in_stock is True and price_scope is 'variant' so the row reaches the per-store
    breakdown and the chart at all — both filter those out otherwise. compare_at_price
    stays NULL: Google reports *our* current price alongside the benchmark, and
    putting it there would render as a struck-through was-price in the UI.
    """
    offer_id = getattr(view, "offer_id", None)
    diff_suffix = f":{country}" if country else ""
    return {
        "observed_at": observed_at,
        "run_id": run_id,
        "source": source,
        "diff_key": f"{source}:{item_id}{diff_suffix}",
        "competitor_id": competitor_id,
        "url": None,
        "competitor_title": getattr(view, "title", None) or None,
        "competitor_sku": offer_id,
        "gtin": None,
        "match_item_id": str(item_id),
        "match_method": ("google_benchmark" if source == "gmb_benchmark"
                         else "google_suggested"),
        "match_confidence": 1.0,
        "price": price,
        "compare_at_price": None,
        "currency": currency,
        "in_stock": True,
        "extraction_method": "merchant_api",
        "price_scope": "variant",
        "variant_id": None,
        "variant_options_json": json.dumps([]),
        "price_low": None,
        "price_high": None,
    }


def _collect(views, *, resolver, run_id, observed_at, competitor_id, source,
             price_attr, country=None):
    """Turns report rows into observation rows, counting why rows were dropped."""
    rows, stats = [], {"returned": 0, "unresolved": 0, "no_price": 0, "wrong_currency": 0}
    for view in views:
        stats["returned"] += 1
        price, currency = _price_parts(getattr(view, price_attr, None))
        if price is None or price <= 0:
            stats["no_price"] += 1
            continue
        # Comparison math is CAD-only (repository.sql_cad_only); a USD benchmark
        # would read ~35% cheap, so drop it rather than show a misleading number.
        if currency and currency != "CAD":
            stats["wrong_currency"] += 1
            continue
        item_id, _how = resolver.resolve(
            getattr(view, "offer_id", ""), getattr(view, "id", ""))
        if not item_id:
            stats["unresolved"] += 1
            continue
        rows.append(build_observation(
            view, run_id=run_id, observed_at=observed_at, item_id=item_id,
            competitor_id=competitor_id, source=source, price=price,
            currency=currency or "CAD", country=country,
        ))
    stats["written"] = len(rows)
    return rows, stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def ensure_competitors():
    """Registers the pseudo-competitors if absent.

    Insert-only on purpose: re-upserting every run would flip `enabled` back on
    after someone disabled the source from the Competitors tab.
    """
    existing = {c.get("competitor_id") for c in repository.get_competitors()}
    for competitor_id, spec in _COMPETITORS.items():
        if competitor_id in existing:
            continue
        repository.upsert_competitor({
            "competitor_id": competitor_id,
            "connector_type": repository.BENCHMARK_CONNECTOR,
            **spec,
        })


def _search(client, query):
    from google.shopping.merchant_reports_v1 import SearchRequest

    request = SearchRequest(
        parent=f"accounts/{config.GOOGLE_MERCHANT_ID}",
        query=query.strip(),
        page_size=1000,
    )
    return client.search(request=request)  # pager follows nextPageToken itself


def run_benchmark_sync(run_id: str, observed_at: str, dry_run: bool = False) -> dict:
    """Pulls both reports and appends a price snapshot. Returns a stats dict.

    Raises BenchmarkUnavailable when it isn't configured; the caller treats that as
    a skip. Any other failure is the caller's to catch — a Google outage must not
    fail the scrape run.
    """
    country = config.GOOGLE_BENCHMARK_COUNTRY or "CA"
    client = _report_client()
    resolver = OfferResolver()
    stats = {"country": country,
             "offer_map_variants": len(resolver.by_variant),
             "offer_map_skus": len(resolver.by_sku)}

    pulls = [(BENCHMARK_COMPETITOR_ID, "gmb_benchmark", "benchmark",
              (r.price_competitiveness_product_view
               for r in _search(client, BENCHMARK_QUERY.format(country=country))),
              "benchmark_price", country)]
    if config.GOOGLE_INSIGHTS_ENABLED:
        pulls.append((SUGGESTED_COMPETITOR_ID, "gmb_suggested", "suggested",
                      (r.price_insights_product_view
                       for r in _search(client, INSIGHTS_QUERY)),
                      "suggested_price", None))

    collected = []
    for competitor_id, source, label, views, price_attr, view_country in pulls:
        rows, stats[label] = _collect(
            views, resolver=resolver, run_id=run_id, observed_at=observed_at,
            competitor_id=competitor_id, source=source, price_attr=price_attr,
            country=view_country,
        )
        collected.append((competitor_id, rows))

    stats["observations"] = sum(len(rows) for _, rows in collected)
    if dry_run:
        stats["dry_run"] = True
        return stats

    ensure_competitors()
    for competitor_id, rows in collected:
        repository.load_rows(repository.T_OBSERVATIONS, rows)
        repository.mark_competitor_scraped(
            competitor_id, "success" if rows else "success_no_products")
    return stats
