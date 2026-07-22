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

# The pin/search picker only surfaces catalog items carrying this Lightspeed tag
# (comma-separated in item_tags). The tag reliably lives on active *variants* even
# when the matrix parent isn't tagged, so the filter is applied per variant and a
# matrix stays fully searchable whenever any one of its active variants is tagged.
# Set to an empty string to disable the filter (surface the whole catalog).
SEARCH_TAG = os.getenv("PI_SEARCH_TAG", "add").strip().lower()
# Optional: restrict auto-seeding to items that have a UPC/EAN. Off by default —
# competitor catalogs rarely expose barcodes, so this mostly shrinks coverage.
REQUIRE_UPC = _flag("PI_REQUIRE_UPC")

# Targeted scraping: nightly runs only re-check confirmed-link URLs unless some
# active item activated within this window still has no confirmed match — only
# then does a full catalog crawl (and its LLM verification) run.
DISCOVERY_DAYS = int(os.getenv("PI_DISCOVERY_DAYS", "7"))

# Match confirmation policy. Off (default): every proposed link — UPC/GTIN,
# brand+SKU, fuzzy, LLM-verified, SERP — lands in the Matching queue as
# 'pending' and only a human confirms; observations only match via already-
# confirmed links. The LLM verifier still annotates pending rows and still
# auto-rejects clear non-matches. On: restores automatic confirmation
# (gtin links + LLM same_variant/resolved same_model).
AUTO_CONFIRM = _flag("PI_AUTO_CONFIRM")

# SERP discovery (SerpApi, engine=google): finds candidate product URLs on
# competitors we can't crawl (connector_type='unknown'). Only fires for items
# inside the discovery window that still lack a confirmed link, so search spend
# scales with newly tracked items, not catalog size or nightly cadence.
SERP_ENABLED = _flag("PI_SERP_ENABLED")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
SERP_MAX_SEARCHES_PER_RUN = int(os.getenv("PI_SERP_MAX_SEARCHES_PER_RUN", "20"))
# Top organic results per search that get fetched/parsed (storefront requests,
# free — but each is a polite_get, so keep it small).
SERP_RESULTS_PER_SEARCH = int(os.getenv("PI_SERP_RESULTS_PER_SEARCH", "3"))
SERP_GL = os.getenv("PI_SERP_GL", "ca")
SERP_HL = os.getenv("PI_SERP_HL", "en")

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

# Slack notifications (best-effort, posted after each run from the scrape thread).
# Off until a webhook is set, so existing deployments are unaffected. The digest
# and health alerts go to SLACK_WEBHOOK_URL; MAP/undercut priority pings go to
# SLACK_ALERTS_WEBHOOK_URL, falling back to the main webhook when unset.
SLACK_ENABLED = _flag("PI_SLACK_ENABLED")
SLACK_WEBHOOK_URL = os.getenv("PI_SLACK_WEBHOOK_URL", "")
SLACK_ALERTS_WEBHOOK_URL = os.getenv("PI_SLACK_ALERTS_WEBHOOK_URL", "")

# LLM digest.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DIGEST_MODEL = os.getenv("PI_DIGEST_MODEL", "claude-haiku-4-5")
DIGEST_MAX_TOKENS = int(os.getenv("PI_DIGEST_MAX_TOKENS", "1000"))
# Volume-vs-margin guardrail the digest reasons with: aim to sit just under the
# next-lowest competitor, never recommend pricing more than this % below it.
DIGEST_UNDERCUT_TARGET_PCT = float(os.getenv("PI_DIGEST_UNDERCUT_TARGET_PCT", "5"))

# LLM match verification: near-miss fuzzy candidates are batched to a small
# model after each scrape run. The per-run pair cap bounds cost (~10 requests
# of ~20 pairs at the default).
MATCH_MODEL = os.getenv("PI_MATCH_MODEL", "claude-haiku-4-5")
MATCH_MAX_PAIRS_PER_RUN = int(os.getenv("PI_MATCH_MAX_PAIRS_PER_RUN", "200"))
MATCH_BATCH_SIZE = int(os.getenv("PI_MATCH_BATCH_SIZE", "20"))
MATCH_MAX_TOKENS = int(os.getenv("PI_MATCH_MAX_TOKENS", "2000"))

# Structured attribute matching (first-pass): compare a scraped variant's
# color/size options against our attribute_1/2/3 before proposing a candidate.
# Routes each competitor variant to the exact tracked variant it corresponds to,
# and suppresses variants that clearly conflict (wrong color/size) so they never
# reach the review queue. AUTO_CONFIRM off = exact color+size matches become
# high-confidence candidates you approve; on = they're confirmed without review.
ATTR_MATCH_ENABLED = os.getenv("PI_ATTR_MATCH_ENABLED", "true").lower() == "true"
ATTR_AUTO_CONFIRM = os.getenv("PI_ATTR_AUTO_CONFIRM", "false").lower() == "true"
