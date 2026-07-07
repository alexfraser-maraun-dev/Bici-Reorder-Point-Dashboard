// Payload shapes for /api/price-intel/* (see backend/app/services/price_intelligence/router.py)

export interface Competitor {
  competitor_id: string
  name: string
  base_url: string
  connector_type: 'shopify_json' | 'shopify_html' | 'unknown' | null
  enabled: boolean
  notes: string | null
  created_at: string | null
  updated_at: string | null
  last_scraped_at: string | null
  last_scrape_status: string | null
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
  // joined market fields (matrix-grain when the item belongs to a matrix)
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

export interface ItemCompetitorPrice {
  competitor_id: string | null
  competitor_name: string | null
  source: 'catalog' | 'url'
  url: string | null
  competitor_title: string | null
  price: number | null
  compare_at_price: number | null
  in_stock: boolean | null
  observed_at: string
  match_method: string | null
  match_confidence: number | null
}
