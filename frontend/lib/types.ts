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

// Purchase Orders
export type POStatus = 'draft' | 'submitted' | 'pushed' | 'failed'
export type POReconciliation = 'new_po' | 'append_to_open_po' | 'already_on_po'

export interface PODraftLine {
  draft_id: string
  sku: string
  item_id: string
  location_id: string
  quantity: number
  unit_cost: number | null
  source: string
  recommendation_run_id?: string | null
  reconciliation: POReconciliation
  target_lightspeed_order_id?: string | null
}

export interface PurchaseOrderDraft {
  draft_id: string
  vendor_id: string
  vendor_name?: string | null
  shop_id: string
  status: POStatus
  created_by?: string
  created_at?: string
  updated_at?: string
  lightspeed_order_id?: string | null
  notes?: string | null
  lines?: PODraftLine[]
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

// Whether a live LS SO was matched to a Shopify `SO`-tagged order (by customer email + SKU).
export type ShopifyMatch = 'matched' | 'ambiguous' | 'none'

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
  description: string | null
  // Attached purchase order
  order_id: string | null
  vendor_id: string | null
  vendor_name: string | null
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
  shopify_order_id: string | null
  shopify_order_name: string | null
  shopify_order_url: string | null
  shopify_expected_date: string | null   // the customer-promised ETA from Shopify
  // Deep links into Lightspeed
  ls_item_url: string | null
  ls_customer_url: string | null
  ls_order_url: string | null
  // Client-only: 'shopify' marks a Shopify-only (Unmatched) pseudo-row in the unified table.
  kind?: 'ls' | 'shopify'
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
