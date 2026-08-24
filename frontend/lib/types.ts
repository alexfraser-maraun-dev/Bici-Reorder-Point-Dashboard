// SKU × Location row type
export interface SkuLocationRow {
  id: string
  lightspeedItemId?: string
  sku: string
  vendorId?: string | number
  product: string
  brand: string
  vendor: string
  category: string
  location: string
  trailingUnitsSold: number
  daysOutOfStock: number
  avgDailySales: number
  rawUnitsSold14d?: number
  adjustedDailySales14d?: number
  daysOutOfStock14?: number
  activeDays14?: number
  distinctSaleDays14?: number
  momentumStatus?: 'surging' | 'rising' | 'spiky' | 'flat' | 'cooling' | 'insufficient_data'
  momentumLabel?: string
  momentumRank?: number
  momentumReason?: string
  leadTimeDays: number
  onHand: number
  onOrder: number
  inventoryPosition: number
  inventoryStatus?: InventoryStatus
  inventoryStatusLabel?: string
  inventoryStatusRank?: number
  inventoryStatusReason?: string
  currentReorderPoint: number
  recommendedReorderPoint: number
  currentDesiredLevel: number
  recommendedDesiredLevel: number
  suggestedBuyQty: number
  needsOrder: boolean
  changed: boolean
  locked: boolean
  override: boolean
  writebackStatus: WritebackStatus
  safetyStock: number
  lastPushedAt?: string
}

export type WritebackStatus = 'pending' | 'success' | 'failed' | 'not_pushed'
export type InventoryStatus =
  | 'critical'
  | 'low'
  | 'warning'
  | 'healthy'
  | 'incoming'
  | 'on_target'
  | 'high'
  | 'overstock'
  | 'no_demand'

// Recommendation Run
export interface RecommendationRun {
  id: string
  runDate: string
  status: 'completed' | 'running' | 'failed'
  trailingDays: number
  forecastDays: number
  safetyDays: number
  totalRows: number
  changedRows: number
  needsOrderCount: number
  duration: string
}

// Writeback Audit Entry
export interface WritebackAuditEntry {
  id: string
  timestamp: string
  user: string
  sku: string
  location: string
  field: 'reorder_point' | 'desired_level'
  oldValue: number
  newValue: number
  status: 'success' | 'failed'
  errorMessage?: string
}

// Managed SKU
export interface ManagedSku {
  id: string
  sku: string
  product: string
  brand: string
  vendor: string
  category: string
  active: boolean
  addedAt: string
  addedBy: string
}

// Settings
export interface Settings {
  defaultTrailingDays: number
  forecastDays: number
  safetyDays: number
  showMonthlyCadence: boolean
  locationPolicyDefaults: Record<string, LocationPolicy>
}

export interface LocationPolicy {
  safetyDays: number
  leadTimeDays: number
}

// Filter state
export interface FilterState {
  search: string
  locations: string[]
  vendors: string[]
  brands: string[]
  categories: string[]
  needsOrderOnly: boolean
  changedOnly: boolean
  lockedOnly: boolean
  overriddenOnly: boolean
  writebackFailedOnly: boolean
  recommendationRunId: string | null
}

// API hook types for future backend integration
export interface UseSkuDataResult {
  data: SkuLocationRow[]
  isLoading: boolean
  error: Error | null
  refetch: () => void
}

export interface UseRecommendationRunsResult {
  data: RecommendationRun[]
  isLoading: boolean
  error: Error | null
}

export interface UseWritebackAuditResult {
  data: WritebackAuditEntry[]
  isLoading: boolean
  error: Error | null
}

export interface UseManagedSkusResult {
  data: ManagedSku[]
  isLoading: boolean
  error: Error | null
}

// Advanced weekly planning and purchase orders
export type POStatus =
  | 'draft'
  | 'approved'
  | 'previewed'
  | 'ready_for_push'
  | 'synchronized'
  | 'partial_failure'
  | 'cancelled'
export type POReconciliation = 'new_po' | 'append_to_open_po' | 'already_on_po'

export interface PODraftLine {
  line_id?: string
  draft_id?: string
  recommendation_id?: string | null
  sku: string | null
  description?: string | null
  brand?: string | null
  category_top_level?: string | null
  item_id: string
  location_id: string
  quantity: number
  unit_cost: number | null
  landed_cost?: number | null
  currency?: string
  source: string
  reconciliation: POReconciliation
  target_lightspeed_order_id?: string | null
  need_by_week?: string | null
  case_pack?: number | null
  moq?: number | null
  constraint_warning?: string | null
}

export interface PurchaseOrderDraft {
  draft_id: string
  vendor_id: string
  vendor_name?: string | null
  shop_id: string
  status: POStatus
  version: number
  created_by?: string
  created_at?: string
  updated_at?: string
  lightspeed_order_id?: string | null
  notes?: string | null
  lines?: PODraftLine[]
}

export interface WeeklyForecastPoint {
  week_start: string
  p50: number
  p80: number
  p90: number
  p95: number
  forecast_cogs: number | null
  forecast_revenue: number | null
}

export interface PurchaseRecommendation {
  recommendation_id: string
  run_id: string
  model_version: string
  assumption_version: string
  source_snapshot_at: string
  item_id: string
  sku?: string | null
  description?: string | null
  brand?: string | null
  category?: string | null
  category_top_level?: string | null
  location_id: string
  location?: string | null
  vendor_id?: string | null
  vendor_name?: string | null
  champion_model: string
  requested_model?: string
  seasonal_source?: string
  vendor_resolution_source?: string | null
  confidence: 'high' | 'medium' | 'low'
  forecast_metrics: { wape?: number; mase?: number; bias?: number }
  forecast: WeeklyForecastPoint[]
  need_by_week?: string | null
  order_coverage_weeks: number
  protection_horizon_weeks: number
  incoming_within_protection: number
  recommended_quantity: number
  case_pack: number
  moq: number
  constraint_extra_units: number
  landed_unit_cost: number | null
  currency: string
  purchase_commitment_spend: number | null
  blocked: boolean
  reason_codes: string[]
}

export interface MonthlyPlanningRollup {
  category: string
  month: string
  units: number
  cogs: number
  revenue: number
  missing_cogs: boolean
}

export interface ForecastRun {
  run_id: string
  status: 'complete'
  created_at: string
  source_snapshot_at: string
  as_of_date: string
  horizon_weeks: number
  model_version: string
  assumption_version: string
  scope_type: PlanningScope
  scope_value?: string | null
  config: PlanningConfig
  recommendation_count: number
  blocking_exception_count: number
  recommendations: PurchaseRecommendation[]
  monthly_rollups: MonthlyPlanningRollup[]
}

export type PlanningScope = 'auto_replen' | 'brand' | 'vendor' | 'category' | 'item_ids'

export interface PlanningConfig {
  model: 'auto' | 'current_velocity' | 'seasonal_naive' | 'hierarchical_seasonal' | 'tsb' | 'ets_damped'
  service_quantile: 0.5 | 0.8 | 0.9 | 0.95
  history_years: number
  order_coverage_weeks: number
  review_period_weeks?: number
  demand_multiplier: number
  seasonal_smoothing_weeks: number
  seasonal_shrinkage: number
  lead_time_days?: number | null
}

export interface LightspeedOpenOrder {
  orderID: string
  vendorID: string
  shopID: string
  po_state: 'unsent' | 'ordered' | 'partially_received' | 'complete' | 'archived'
  createTime?: string | null
  orderedDate?: string | null
  expectedDate?: string | null
  refNum?: string | null
  OrderLine?: Array<{ orderLineID: string; itemID: string; quantity: number; numReceived?: number }>
}

export interface LightspeedPreviewOperation {
  action: 'create_unsent_order' | 'add_order_line' | 'update_order_line'
  order_id?: string
  order_line_id?: string
  vendor_id?: string
  shop_id?: string
  item_id?: string
  sku?: string | null
  quantity?: number
  increment_quantity?: number
  resulting_quantity?: number
  unit_cost?: number | null
  ordered_date?: null
}

export interface LightspeedPreview {
  mode: 'preview'
  draft_id: string
  draft_version: number
  target_order_id?: string | null
  operations: LightspeedPreviewOperation[]
  read_only_inbound_orders: Array<{ order_id: string; state: string }>
  writes_performed: false
  draft?: PurchaseOrderDraft
}

// Demand & Seasonality visualization layer
export interface SeasonalProfile {
  category_label: string
  level: string
  // Multiplicative seasonal index per month number (1..12); mean ~= 1.0.
  indices: Record<string, number>
  sample_units: number
}

export interface DemandHistoryPoint {
  month: number
  year?: number
  units: number
}

export interface ForecastPoint {
  month: number
  units: number
  seasonal_index: number
}

export interface CoverageMonth {
  month: number
  weeks: number
  stockout_risk: 'critical' | 'low' | 'healthy'
}

export interface CoverageRow {
  sku: string
  lightspeed_item_id?: string | null
  product: string
  location: string
  weeks_of_cover: CoverageMonth[]
}

// KPI Summary
export interface KpiSummary {
  totalManagedRows: number
  needsOrder: number
  changedRows: number
  lockedRows: number
  overrides: number
  readyToPush: number
  failedWritebacks: number
}

// ---------------------------------------------------------------------------
// Special Orders
// ---------------------------------------------------------------------------

// The SO's position in the procurement flow, derived server-side from the attached PO's
// real state (not the SpecialOrder.status string). See special_order_service.
export type ProcurementStage =
  | 'open_pool'     // no PO attached yet
  | 'unordered_po'  // PO attached but not yet placed with the vendor
  | 'ordered'       // PO placed with the vendor (Order.orderedDate is set)
  | 'received'      // SO has been checked in / received

// The one thing (if any) that needs attention within a stage. 'none' = nothing to action.
export type SpecialOrderFlag =
  | 'none'
  | 'overdue'          // 1–2 days past the classification date (or sitting in stage)
  | 'overdue_mid'      // 3–7 days
  | 'critical'         // 8+ days
  | 'no_eta'           // ordered, no date to judge against
  | 'ready_not_called' // received but customer not yet contacted

// Whether a live LS SO was matched to a Shopify `SO`-tagged order.
export type ShopifyMatch = 'matched' | 'ambiguous' | 'none'

// Which identity tier produced a match (or made it ambiguous). 'manual' = a human linked it.
export type ShopifyMatchBasis =
  | 'email_sku'
  | 'phone_sku'
  | 'name_sku'
  | 'sku_only'
  | 'sku_conflict' // single SKU-level candidate but the identity signals disagree
  | 'manual'

// A candidate Shopify order behind an 'ambiguous' match — enough to resolve it by hand.
export interface ShopifyCandidate {
  order_id: string
  order_name: string | null
  customer_email: string | null
  shopify_expected_date: string | null
  created_at: string | null
}

// The triage tile axis: the Shopify inbound stage and the cross-cutting Recommended Action tile
// sit left of the four LS procurement stages. Both are "overlay" tiles — a single order can appear
// in one of them AND in its flow stage, since they're derived from the same per-row state.
export type TriageStage = 'shopify' | 'recommended_action' | ProcurementStage

// A Shopify `SO`-tagged order with no matching live LS SO — the "Unmatched" population.
export interface ShopifyOnlyOrder {
  order_id: string
  order_name: string | null
  customer_email: string | null
  shopify_expected_date: string | null
  created_at: string | null
  fulfillment_status: string | null
  financial_status: string | null
  skus: string[]
  shopify_order_url: string | null
  // True when some LS SO could plausibly claim this order (an ambiguous candidate) —
  // shown as "Possible match" instead of "Unmatched".
  ambiguous_candidate?: boolean
}

// One line of a Shopify order, as shown in the manual-link confirmation step.
export interface ShopifyLineItem {
  sku: string | null
  title: string | null
  variant_title: string | null
  quantity: number | null
}

// A Shopify order found by free-text lookup (`/api/special-orders/shopify-lookup`).
// Unlike ShopifyOnlyOrder this can be ANY order — fulfilled, untagged, cancelled, years old
// — so the state flags matter: the UI shows them before the user confirms the link.
export interface ShopifyOrderLookup {
  order_id: string
  order_name: string | null
  customer_email: string | null
  customer_phone: string | null
  customer_name: string | null
  created_at: string | null
  shopify_expected_date: string | null
  fulfillment_status: string | null
  financial_status: string | null
  cancelled: boolean
  closed: boolean
  test: boolean
  tags: string[]
  line_items: ShopifyLineItem[]
  shopify_order_url: string | null
}

// A vendor that can supply a SKU's brand, with its median lead time to the SO's store.
export interface AvailableVendor {
  vendor_id: string
  vendor_name: string
  lead_time_days: number | null
  lead_time_source: 'store' | 'vendor_median' | null
  distinct_items?: number | null
}

export interface SpecialOrder {
  special_order_id: string
  status: string
  unit_quantity: string | null
  shop_id: string | null
  store: string | null
  timestamp: string | null
  created_date: string | null
  days_since_creation: number | null
  contacted: boolean
  completed: boolean
  // Customer
  customer_id: string | null
  customer_name: string | null
  customer_phone: string | null
  // Item / product
  item_id: string | null
  system_sku: string | null
  upc: string | null
  brand: string | null
  // Brand-level "Available from" vendors, fastest lead time first (top 3).
  available_vendors: AvailableVendor[]
  description: string | null
  // Attached purchase order
  order_id: string | null
  vendor_id: string | null
  vendor_name: string | null
  // The PO's "Order Type v2" ('Replenishment' | 'Booking'). Lightspeed only stores the
  // non-default choice, so this is the stored value where there is one and the field's
  // default ('Replenishment') otherwise. Null only when no PO is attached yet.
  order_type: string | null
  expected_date: string | null
  ordered_date: string | null
  po_ordered: boolean
  po_complete: boolean
  received_started: boolean
  // Triage: procurement stage + within-stage attention flag
  procurement_stage: ProcurementStage
  procurement_stage_index: number   // 0=open_pool, 1=unordered_po, 2=ordered, 3=received
  flag: SpecialOrderFlag
  days_overdue: number | null       // signed; only set for the 'ordered' stage
  is_overdue: boolean               // flag is overdue or critical
  // Customer (Shopify) identity + matched promise date
  customer_email: string | null
  shopify_match: ShopifyMatch
  shopify_match_basis: ShopifyMatchBasis | null
  shopify_order_id: string | null
  shopify_order_name: string | null
  shopify_order_url: string | null
  shopify_expected_date: string | null   // the customer-promised ETA from Shopify
  shopify_candidates: ShopifyCandidate[] // ambiguous only: the orders it could be
  // Attached service workorder (when the SO was raised from the bench), with the bench's
  // own notes: `note` is customer-facing, `internal_note` staff-only, `hook_in` the tag
  // written when the bike came in.
  workorder_id: string | null
  workorder_status: string | null
  workorder_note: string | null
  workorder_internal_note: string | null
  workorder_hook_in: string | null
  workorder_eta_out: string | null
  workorder_time_in: string | null
  workorder_url: string | null
  // Deep links into Lightspeed
  ls_item_url: string | null
  ls_customer_url: string | null
  ls_order_url: string | null
  // Client-only: 'shopify' marks a Shopify-only (Unmatched) pseudo-row in the unified table.
  kind?: 'ls' | 'shopify'
  // Client-only, shopify pseudo-rows: carried over from ShopifyOnlyOrder ("Possible match").
  ambiguous_candidate?: boolean
}

export interface SpecialOrderSummary {
  total_open: number
  by_stage: Record<ProcurementStage, number>
  flagged_by_stage: Record<ProcurementStage, number>
  by_flag: Record<string, number>
  // Flat convenience counts
  overdue: number
  critical: number
  no_eta: number
  ready_not_called: number
}

export interface SpecialOrderDashboard {
  orders: SpecialOrder[]
  summary: SpecialOrderSummary
  shopify_only: ShopifyOnlyOrder[]
  fetched_at?: string
}

// ---------------------------------------------------------------------------
// PO Tracker (placed-but-unreceived POs triaged against expected arrival)
// ---------------------------------------------------------------------------

// Lateness tiers apply only to fully-unreceived POs; any receiving progress
// moves the PO into the single 'receiving' bucket (close-out work).
export type PoWatchTriage = 'critical' | 'very_late' | 'late' | 'due_soon' | 'no_eta' | 'on_track' | 'receiving'
export type PoWatchStatus = 'ordered' | 'receiving'
export type PoWatchFlag =
  | 'no_expected_date'
  | 'implied_expected'
  | 'expected_faster_than_median'
  | 'expected_before_ordered'
  | 'past_median_lead_time'
  | 'fully_received_not_closed'

export interface PoWatchAck {
  acked_by: string | null
  acked_at: string
  note: string | null
  snooze_until: string | null   // ISO date; null = until the LS expected date changes
  active: boolean
}

export interface PoWatchOrder {
  order_id: string
  ref_num: string | null
  vendor_id: string
  vendor_name: string
  shop_id: string
  shop_name: string
  created_by: string | null
  created_date: string | null
  ordered_date: string | null
  expected_date: string | null           // the buyer-entered arrival date in LS
  effective_expected_date: string | null // expected_date, or ordered + median when missing
  expected_source: 'vendor' | 'implied' | null
  status: PoWatchStatus
  line_count: number
  units_ordered: number
  units_received: number
  cost_ordered: number
  cost_received: number
  received_pct: number
  median_lead_time_days: number | null
  lead_time_po_count: number | null
  promised_lead_time_days: number | null
  days_since_ordered: number | null
  days_late: number | null
  days_until_expected: number | null
  triage: PoWatchTriage
  flags: PoWatchFlag[]
  lightspeed_url: string
  ack: PoWatchAck | null
  alertable: boolean
}

export interface PoWatchSummary {
  critical: number
  very_late: number
  late: number
  due_soon: number
  no_eta: number
  on_track: number
  receiving: number
  alertable: number
  acknowledged: number
  expected_faster_than_median: number
}

export interface PoWatchResponse {
  status: string
  orders: PoWatchOrder[]
  summary: PoWatchSummary
  meta: {
    ordered_within_days: number
    ordered_since: string
    order_count: number
    fetched_at: string
    alert_days_late_threshold: number
  }
}

export interface PoWatchLine {
  order_line_id: string
  item_id: string
  sku: string | null
  description: string | null
  quantity: number
  received: number
  unit_cost: number
  total: number
}
