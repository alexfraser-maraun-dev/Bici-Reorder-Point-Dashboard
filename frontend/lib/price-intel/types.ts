// Payload shapes for /api/price-intel/* (see backend/app/services/price_intelligence/router.py)

export interface Competitor {
  competitor_id: string
  name: string
  base_url: string
  // 'benchmark' marks a synthetic, never-crawled source (Google Merchant Center
  // reference prices) — registered so it renders like a store, but pulled by its
  // own scrape phase rather than a connector.
  connector_type: 'shopify_json' | 'shopify_html' | 'sitemap_html' | 'unknown'
    | 'benchmark' | null
  enabled: boolean
  notes: string | null
  created_at: string | null
  updated_at: string | null
  last_scraped_at: string | null
  last_scrape_status: string | null
  // JSON: {cursor, pages_done, products_seen, cap_hit, catalog_exhausted,
  // updated_at, + crawl diagnostics} — last catalog-crawl coverage, rotation
  // cursor and why the crawl went the way it did. Written by the crawl.
  crawl_state_json?: string | null
  // JSON: per-competitor crawl overrides, written by the user. See
  // CompetitorCrawlSettings. Every key optional; unset falls back to the global.
  settings_json?: string | null
}

// Per-competitor crawl overrides (pi_competitors.settings_json). POST
// /competitors accepts these under a `settings` key and rejects unknown ones.
export interface CompetitorCrawlSettings {
  // Only crawl paths matching this regex (e.g. '/product/'). Unset = all.
  url_allow_pattern?: string
  // Additional never-a-product paths, on top of the built-in deny-list.
  url_deny_pattern?: string
  // 'auto' drops the brand filter on stores that don't put brands in their
  // URLs; 'on' always filters; 'off' never does.
  brand_filter?: 'auto' | 'on' | 'off'
  // Seconds between requests to this store. Lower = deeper nightly coverage;
  // a 429 automatically backs the store off for the rest of the run.
  request_interval_seconds?: number
  max_product_pages?: number
  max_catalog_pages?: number
  max_sitemap_fetches?: number
  max_candidate_urls?: number
  // Explicit sitemap URLs when robots.txt / sitemap.xml discovery fails.
  sitemap_urls?: string[]
  // Off only for a store that legitimately serves products from a second host.
  confine_to_domain?: boolean
  // Notification mutes. True hides this store's price-change / stock-change
  // events from the change feed, the unread badge and Slack. The events are
  // still recorded, so un-muting brings the history back. MAP violations and
  // undercuts are never muted — they're the decision signals.
  mute_price_alerts?: boolean
  mute_stock_alerts?: boolean
}

export interface TrackedUrl {
  url_id: string
  url: string
  competitor_id: string | null
  item_id: string | null
  label: string | null
  enabled: boolean
  created_by: string | null
  created_at: string | null
  last_scraped_at: string | null
  last_status: string | null
  competitor_sku: string | null
  competitor_variant_id: string | null
  competitor_gtin: string | null
  variant_options_json: string | null
  // joined from pi_tracked_products when item_id is set
  item_title: string | null
  item_brand: string | null
  item_upc: string | null
  item_system_sku: string | null
}

export interface TrackedProduct {
  item_id: string
  sku: string | null
  system_sku: string | null
  upc_normalized: string | null
  brand: string | null
  title: string | null
  source: 'auto_top_revenue' | 'manual'
  pinned: boolean
  excluded: boolean
  revenue_rank: number | null
  trailing_revenue_90d: number | null
  current_retail: number | null
  current_cost: number | null
  min_price_override: number | null
  is_map: boolean
  map_price: number | null
  item_matrix_id: string | null
  matrix_description: string | null
  attribute_1: string | null
  attribute_2: string | null
  attribute_3: string | null
  updated_at: string | null
  // joined market fields; exact observations only, never matrix-propagated
  market_min_in_stock: number | null
  market_min: number | null
  market_median: number | null
  competitor_count: number | null
  competitor_ids: string[] | null
  last_observed_at: string | null
}

// Change-point-compressed price history for the expandable chart.
export interface PriceHistoryPoint {
  observed_at: string
  price: number | null
  in_stock?: boolean | null
}

export interface PriceHistoryCompetitor {
  competitor_id: string | null
  competitor_name: string
  points: PriceHistoryPoint[]
}

export interface ItemPriceHistory {
  ours: PriceHistoryPoint[]
  competitors: PriceHistoryCompetitor[]
}

export type ChangeEventType =
  | 'price_drop'
  | 'price_increase'
  | 'back_in_stock'
  | 'out_of_stock'
  | 'new_match'
  | 'first_observation'
  | 'map_violation'
  | 'undercut'

export interface ChangeEvent {
  event_id: string
  occurred_at: string
  run_id: string | null
  event_type: ChangeEventType
  competitor_id: string | null
  competitor_name: string | null
  item_id: string | null
  item_title: string | null
  item_brand: string | null
  url: string | null
  old_price: number | null
  new_price: number | null
  pct_change: number | null
  acknowledged: boolean
  acknowledged_at: string | null
}

export interface ChangeFeedFilters {
  days?: number
  unacknowledgedOnly?: boolean
  competitorId?: string | null
  eventTypes?: ChangeEventType[]
  minPct?: number | null
  brand?: string | null
}

export interface ScrapeStatus {
  status: 'idle' | 'running' | 'success' | 'partial' | 'failed'
  run_id?: string
  trigger?: string
  phase?: string
  started_at?: string
  finished_at?: string
  competitors_done?: number
  competitors_total?: number
  urls_done?: number
  urls_total?: number
  links_done?: number
  links_total?: number
  observations?: number
  changes?: number
  variants_extracted?: number
  exact_resolutions?: number
  ambiguous_pages?: number
  ranges_excluded?: number
  fallback_failures?: number
  extractor_methods?: Record<string, number>
  errors?: string[]
}

export interface ScrapeRun {
  run_id: string
  started_at: string
  finished_at: string | null
  trigger: string
  status: string
  competitors_done: number
  urls_done: number
  observations_count: number
  changes_count: number
  error: string | null
  stats_json: string | null
}

export interface PriceIntelSummary {
  tracked_count: number
  compared_count: number
  cheaper: number
  parity: number
  pricier: number
  price_index_vs_market_min: number | null
  unacknowledged_changes: number
  map_tracked_count: number
  upc_tracked_count: number
  pending_links: number
  last_run: ScrapeRun | null
  scrape_status: ScrapeStatus
  scrape_health: ScrapeHealth
}

export interface ScrapeHealthCompetitor {
  competitor_id: string | null
  name: string | null
  connector_type: string | null
  status: string | null
  // 'blocked' is split out of 'empty': the site refused us (403/429) rather
  // than the sitemap or the filters coming back with nothing.
  bucket: 'ok' | 'empty' | 'no_matches' | 'blocked' | 'failed' | 'skipped' | 'unknown'
  last_scraped_at: string | null
  // Last catalog-crawl coverage (from crawl_state_json). cap_hit means the
  // store is bigger than the nightly page budget and the crawl is rotating
  // through it across nights.
  products_seen?: number | null
  pages_done?: number | null
  cap_hit?: boolean
  // Crawl diagnostics: the funnel from sitemap URLs down to pages fetched, how
  // much of the budget went to relevance-ranked pages, and what the site
  // answered. Present from the first run after the diagnostics change.
  sitemap_urls_seen?: number | null
  candidates_crawlable?: number | null
  // Share of candidate URLs carrying a tracked brand. brand_gate_applied is
  // false when that share was so low the brand filter would have discarded the
  // whole catalog — the crawl ranked on relevance instead.
  brand_hit_rate?: number | null
  brand_gate_applied?: boolean | null
  targeted_candidates?: number | null
  targeted_pages_done?: number | null
  off_domain_dropped?: number | null
  blocked_fetches?: number | null
  fetches?: number | null
  status_counts?: Record<string, number>
}

export interface ScrapeHealth {
  overall: 'healthy' | 'degraded' | 'failed' | 'running' | 'never' | string
  last_run: ScrapeRun | null
  counts: {
    total: number
    ok: number
    empty: number
    no_matches: number
    blocked: number
    failed: number
    skipped: number
  }
  competitors: ScrapeHealthCompetitor[]
}

export interface ItemObservation {
  observed_at: string
  competitor_id: string | null
  url: string | null
  price: number | null
  compare_at_price: number | null
  in_stock: boolean | null
  match_method: string | null
  match_confidence: number | null
  competitor_title: string | null
  extraction_method: string | null
  price_scope: 'variant' | 'product' | 'range' | null
  price_low: number | null
  price_high: number | null
  variant_id: string | null
  variant_options_json: string | null
}

export interface Digest {
  digest_id?: string
  created_at?: string
  run_id?: string
  model?: string
  digest_md?: string
  stats_json?: string
}

export interface PricePushPreview {
  item_id: string
  title: string | null
  sku: string | null
  current_price: number | null
  proposed_price: number
  floor_price: number | null
  floor_source: 'map_price' | 'min_price_override' | 'cost_margin_floor' | null
  is_map: boolean
  map_price: number | null
  current_cost: number | null
  market_min: number | null
  market_min_in_stock: number | null
  market_median: number | null
  guard: 'ok' | 'below_floor' | 'rejected'
  guard_reasons: string[]
}

// Whole-matrix push preview: one row per variant, enumerated live from Lightspeed.
export interface MatrixVariantPreview {
  item_id: string
  description: string | null
  system_sku: string | null
  attributes: string[]
  current_price: number | null
  cost: number | null
  tracked: boolean
  floor_price: number | null
  floor_source: 'map_price' | 'min_price_override' | 'cost_margin_floor' | null
  guard: 'ok' | 'below_floor' | 'rejected'
  guard_reasons: string[]
}

export interface MatrixPushPreview {
  item_matrix_id: string
  matrix_description: string | null
  anchor_item_id: string
  proposed_price: number
  variant_count: number
  variants: MatrixVariantPreview[]
  guard: 'ok' | 'below_floor' | 'rejected'
  guard_reasons: string[]
  warnings: string[]
}

export interface MatrixPushResult {
  item_matrix_id: string
  matrix_description: string | null
  proposed_price: number
  variant_count: number
  status: string
  results: { item_id: string; description: string | null; status: string; error: string | null }[]
}

export interface ItemSearchResult {
  item_id: string
  title: string | null
  brand: string | null
  manufacturer_sku: string | null
  system_sku: string | null
  upc_normalized: string | null
  current_retail: number | null
  item_matrix_id: string | null
  matrix_description: string | null
  attribute_1: string | null
  attribute_2: string | null
  attribute_3: string | null
}

export interface ItemSearchIndexStatus {
  status: 'idle' | 'missing' | 'running' | 'ready' | 'success' | 'failed'
  exists?: boolean
  trigger?: string
  started_at?: string | null
  finished_at?: string | null
  built_at?: string | null
  duration_seconds?: number
  row_count?: number
  bytes?: number
  bytes_processed?: number | null
  stale?: boolean
  error?: string | null
  already_running?: boolean
}

export interface SeedRefreshStatus {
  status: 'idle' | 'queued' | 'running' | 'success' | 'partial' | 'failed'
  trigger?: string
  mode?: 'track_tag' | 'top_revenue' | string
  started_at?: string | null
  finished_at?: string | null
  count?: number | null
  error?: string | null
  index_error?: string | null
  search_index: ItemSearchIndexStatus
}

// Matrix-grain rollup for the tracked table (one row per tracked matrix). Market
// numbers are aggregated only from variants' own exact observations — never
// propagated across siblings. `subscribed` = has a live pi_tracked_matrices row.
export interface TrackedMatrix {
  item_matrix_id: string
  matrix_description: string | null
  brand: string | null
  variants_total: number
  variants_with_market: number
  matrix_market_min_in_stock: number | null
  matrix_market_min: number | null
  current_retail_min: number | null
  current_retail_max: number | null
  competitor_count: number
  competitor_ids: string[] | null
  last_observed_at: string | null
  revenue_rank: number | null
  subscribed: boolean
}

// Per-competitor coverage for one matrix: how many of our variants that competitor
// carries (has an exact observation for) and how many undercut our retail.
export interface MatrixCoverage {
  competitor_key: string
  competitor_id: string | null
  competitor_name: string | null
  url: string | null
  variants_carried: number
  variants_undercut: number
  price_min: number | null
  price_max: number | null
  last_observed_at: string | null
}

export type ProductLinkStatus = 'pending' | 'confirmed' | 'rejected'

export interface ProductLink {
  link_id: string
  item_id: string | null
  competitor_id: string | null
  match_key: string
  competitor_url: string | null
  competitor_sku: string | null
  competitor_title: string | null
  gtin: string | null
  variant_id: string | null
  variant_options_json: string | null
  level: 'variant' | 'model' | null
  status: ProductLinkStatus
  source: 'gtin' | 'llm' | 'human' | 'manual_url'
  confidence: number | null
  fuzzy_score: number | null
  llm_verdict: string | null
  llm_reason: string | null
  our_price: number | null
  their_price: number | null
  decided_by: string | null
  created_at: string | null
  updated_at: string | null
  // joined from pi_tracked_products (available even for archived items)
  item_title: string | null
  item_brand: string | null
  item_matrix_description: string | null
  item_attribute_1: string | null
  item_attribute_2: string | null
  item_attribute_3: string | null
  item_upc: string | null
  item_system_sku: string | null
}

// 'gmb_benchmark' / 'gmb_suggested' are Google Merchant Center reference prices,
// not scraped storefronts. They render like a store but are excluded server-side
// from market-min / position / alerting — see repository.sql_market_sources.
export type PriceSource = 'catalog' | 'url' | 'link' | 'serp'
  | 'gmb_benchmark' | 'gmb_suggested'

export const SYNTHETIC_PRICE_SOURCES: PriceSource[] = ['gmb_benchmark', 'gmb_suggested']

export function isSyntheticSource(source: string | null | undefined): boolean {
  return SYNTHETIC_PRICE_SOURCES.includes(source as PriceSource)
}

export interface ItemCompetitorPrice {
  competitor_id: string | null
  competitor_name: string | null
  source: PriceSource
  url: string | null
  competitor_title: string | null
  price: number | null
  compare_at_price: number | null
  in_stock: boolean | null
  observed_at: string
  match_method: string | null
  match_confidence: number | null
  extraction_method: string | null
  price_scope: 'variant' | 'product' | 'range' | null
  price_low: number | null
  price_high: number | null
  variant_id: string | null
  variant_options_json: string | null
}

export interface VariantCandidate {
  title: string | null
  sku: string | null
  gtin: string | null
  variant_id: string | null
  variant_options: string[]
  price: number | null
  compare_at_price: number | null
  currency: string | null
  in_stock: boolean | null
  price_scope: 'variant' | 'product' | 'range'
  extraction_method: string | null
}

export interface VariantSelectionRequired {
  code: 'variant_selection_required'
  message: string
  candidates: VariantCandidate[]
}

// Admin console: one row per runtime setting. Secret values (Slack webhooks)
// arrive masked ("••••abcdef") — the console only ever sends new values.
export interface AdminSetting {
  key: string
  value: string | number | boolean | null
  default: string | number | boolean | null
  overridden: boolean
  secret: boolean
}
