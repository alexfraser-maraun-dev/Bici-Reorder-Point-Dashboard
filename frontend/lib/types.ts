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
