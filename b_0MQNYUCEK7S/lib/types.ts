// SKU × Location row type
export interface SkuLocationRow {
  id: string
  sku: string
  product: string
  brand: string
  vendor: string
  category: string
  location: string
  trailingUnitsSold: number
  daysOutOfStock: number
  avgDailySales: number
  leadTimeDays: number
  onHand: number
  onOrder: number
  inventoryPosition: number
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
