import os


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Master switch. main.py only mounts the router (and starts the scheduler) when
# this is on, and the import itself lives inside that guard, so a disabled
# deployment never loads this package.
PRICE_INTEL_ENABLED = _flag("PRICE_INTEL_ENABLED")

# Optional shared secret for POST /scrape so an external scheduler (e.g. a GitHub
# Actions watchdog) can trigger runs. Only enforced when REQUIRE_SCRAPE_TOKEN is
# on — the in-app button and the in-process scheduler don't need it.
SCRAPE_TOKEN = os.getenv("PRICE_INTEL_SCRAPE_TOKEN", "")
REQUIRE_SCRAPE_TOKEN = _flag("PRICE_INTEL_REQUIRE_TOKEN")

# Scraper politeness. The UA is deliberately identifiable so storefronts can
# contact/block us rather than mistake us for an attack.
USER_AGENT = os.getenv(
    "PI_USER_AGENT",
    "BiciPriceIntel/1.0 (+https://bici.cc; retail price comparison; contact info@bici.cc)",
)
REQUEST_INTERVAL_SECONDS = float(os.getenv("PI_REQUEST_INTERVAL_SECONDS", "1.0"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("PI_REQUEST_TIMEOUT_SECONDS", "20"))
MAX_RETRIES = int(os.getenv("PI_MAX_RETRIES", "3"))

# Catalog crawl caps keep a single competitor from consuming the whole night (or
# the 512MB memory budget) — 40 pages x 250 products covers a 10k-SKU store.
MAX_CATALOG_PAGES = int(os.getenv("PI_MAX_CATALOG_PAGES", "40"))
MAX_HTML_PRODUCT_PAGES = int(os.getenv("PI_MAX_HTML_PRODUCT_PAGES", "300"))
FLUSH_ROWS = int(os.getenv("PI_FLUSH_ROWS", "2000"))

# Comparison-list seeding. 'track_tag' seeds from items tagged (default) in
# Lightspeed; 'top_revenue' is the old top-N-by-90d-revenue behavior. Untagged
# items are archived (kept with all their history), never deleted.
SEED_MODE = os.getenv("PI_SEED_MODE", "track_tag").strip().lower()
TRACK_TAG = os.getenv("PI_TRACK_TAG", "track").strip().lower()
TOP_REVENUE_COUNT = int(os.getenv("PI_TOP_REVENUE_COUNT", "100"))
# Optional: restrict auto-seeding to items that have a UPC/EAN. Off by default —
# competitor catalogs rarely expose barcodes, so this mostly shrinks coverage.
REQUIRE_UPC = _flag("PI_REQUIRE_UPC")

# Targeted scraping: nightly runs only re-check confirmed-link URLs unless some
# active item activated within this window still has no confirmed match — only
# then does a full catalog crawl (and its LLM verification) run.
DISCOVERY_DAYS = int(os.getenv("PI_DISCOVERY_DAYS", "7"))

# Price-push floor: never suggest/allow (without explicit override) a price below
# cost * (1 + this margin). MAP price and per-item overrides take precedence when set.
MARGIN_FLOOR_PCT = float(os.getenv("PI_MARGIN_FLOOR_PCT", "0.15"))
# Sanity guard: reject single pushes that move price more than this fraction.
MAX_PUSH_CHANGE_PCT = float(os.getenv("PI_MAX_PUSH_CHANGE_PCT", "0.5"))

# Nightly scheduler (in-process daemon thread; Render starter tier never idles out).
# Fires after the overnight Lightspeed->BigQuery sync lands.
SCHEDULE_ENABLED = _flag("PI_SCHEDULE_ENABLED", "true")
SCHEDULE_HOUR_LOCAL = int(os.getenv("PI_SCHEDULE_HOUR_LOCAL", "2"))
SCHEDULE_MINUTE_LOCAL = int(os.getenv("PI_SCHEDULE_MINUTE_LOCAL", "30"))
SCHEDULE_TIMEZONE = os.getenv("PI_SCHEDULE_TIMEZONE", "America/Vancouver")

# LLM digest.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DIGEST_MODEL = os.getenv("PI_DIGEST_MODEL", "claude-haiku-4-5")
DIGEST_MAX_TOKENS = int(os.getenv("PI_DIGEST_MAX_TOKENS", "1000"))

# LLM match verification: near-miss fuzzy candidates are batched to a small
# model after each scrape run. The per-run pair cap bounds cost (~10 requests
# of ~20 pairs at the default).
MATCH_MODEL = os.getenv("PI_MATCH_MODEL", "claude-haiku-4-5")
MATCH_MAX_PAIRS_PER_RUN = int(os.getenv("PI_MATCH_MAX_PAIRS_PER_RUN", "200"))
MATCH_BATCH_SIZE = int(os.getenv("PI_MATCH_BATCH_SIZE", "20"))
MATCH_MAX_TOKENS = int(os.getenv("PI_MATCH_MAX_TOKENS", "2000"))
