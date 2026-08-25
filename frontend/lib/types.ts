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

// PO-level receiving and individual-SO check-in are deliberately separate. A PO can be partly
// or fully received while one special-order unit remains outstanding (usually a split shipment
// or backorder). Only `so_received` means the customer's item was checked in.
export type SpecialOrderReceivingState =
  | 'not_started'
  | 'po_receiving'
  | 'po_complete_so_unreceived'
  | 'so_received'

// The one thing (if any) that needs attention within a stage. 'none' = nothing to action.
export type SpecialOrderFlag =
  | 'none'
  | 'overdue'          // 1–2 days past the classification date (or sitting in stage)
  | 'overdue_mid'      // 3–7 days
  | 'critical'         // 8+ days
  | 'no_eta'           // ordered, no date to judge against
  | 'ready_not_called' // received but customer not yet contacted

// The SLA verdict on a special order, worst first. `stage_stalled` covers a step that has
// overrun its dwell limit even when the customer promise is comfortable (or absent);
// `closed_out` means the item arrived and the SLA clock has stopped.
export type SlaSeverity =
  | 'promise_missed'
  | 'impossible'
  | 'order_today'
  | 'stage_stalled'
  | 'at_risk'
  | 'no_promise'
  | 'on_track'
  | 'closed_out'

// Operational work is deliberately separate from delivery SLA. A received item can have a
// finished delivery clock and still need customer/service close-out, while an order without a
// recorded promise can still have very concrete procurement work to do.
export type SpecialOrderWorkState =
  | 'intake'
  | 'needs_ordering'
  // The linked Shopify order is finished while the Lightspeed SO is still open — the SO is
  // either not required or needs checking out. Outranks every procurement action, because
  // each of those would be work we should not be doing.
  | 'shopify_fulfilled'
  | 'vendor_followup'
  | 'promise_needed'
  | 'closeout'
  | 'on_track'

export type SpecialOrderQueueState = SpecialOrderWorkState | 'in_transit'

export type SpecialOrderActionOwner =
  | 'procurement'
  | 'service'
  | 'cs'
  | 'receiving'
  | 'retail'

// Why a buyer parked a special order. A code is required on every ack — an un-categorised
// snooze cannot be reported on, and reporting is the point.
export type SoReasonCode =
  | 'vendor_backorder'
  | 'awaiting_vendor_reply'
  | 'customer_contacted'
  | 'item_discontinued'
  | 'waiting_on_cs'
  | 'substitute_offered'
  | 'other'

/** What a human last decided about a row. All three clear it out of "Action required"; they
 *  differ in what brings it back — see so_sla_service.WORK_STATUSES. */
export type SoWorkStatus = 'parked' | 'in_progress' | 'done'

export interface SoAck {
  special_order_id: string
  acked_by: string | null
  /** A park reason only when `work_status` is 'parked'; otherwise it repeats the status. */
  reason_code: SoReasonCode | SoWorkStatus
  note: string | null
  acked_at: string
  checkback_date: string
  pinned_stage: string | null
  pinned_promise: string | null
  pinned_po_eta: string | null
  pinned_work_state: SpecialOrderWorkState | null
  escalation_level: number
  work_status: SoWorkStatus
}

export interface SpecialOrderActivityEvent {
  timestamp: string
  type: string
  label: string
  actor: string | null
  details: Record<string, unknown> | string | null
}

export interface SpecialOrderSummarySla {
  by_severity: Record<SlaSeverity, number>
  by_owner: Record<'procurement' | 'receiving' | 'cs', number>
  missing_promise_by_owner: Record<'service' | 'cs', number>
  by_work_state?: Partial<Record<SpecialOrderWorkState, number>>
  by_queue_state?: Partial<Record<SpecialOrderQueueState, number>>
  by_action_owner?: Partial<Record<SpecialOrderActionOwner, number>>
  actionable: number
  acked: number
  checkback_due: number
  escalated: number
  missing_promise: number
}

// Which kind of option the recommendation engine found, best first. Ordering reflects the real
// pool: ~1,700 placed POs with remaining units against ~64 unsent drafts, so joining something
// already inbound is the common case and adding to a draft is the rarer one.
export type PoRecommendationTier =
  | 'in_stock'    // already sellable at this store — do not order
  | 'transfer'    // sellable at the sister store (Victoria <-> Langford only)
  | 'inbound_po'  // an ordered PO already carries unclaimed units of this item
  | 'draft_po'    // an unsent draft at this store for a qualifying vendor
  | 'new_po'      // nothing suitable; raise one

export interface PoRecommendationCandidate {
  tier: PoRecommendationTier
  order_id?: string
  order_line_id?: string | null
  reference_number?: string | null
  vendor_id?: string | null
  vendor_name?: string | null
  shop_id?: string
  store?: string | null
  sellable?: number
  qoh?: number
  unallocated_units?: number
  eta?: string | null
  eta_overdue?: boolean
  meets_promise?: boolean | null
  cadence_days?: number | null
  next_order_date?: string | null
  lead_time_days?: number | null
  // Whether procurement buys from this vendor continually (a one-off PO is routine) or only
  // when a special order forces it. Effort context — it never shifts the landing date.
  is_routine?: boolean | null
  // When the product would actually be here via this option. The thing being optimised.
  landing_date?: string | null
}

export interface PoRecommendation {
  special_order_id: string
  tier: PoRecommendationTier
  recommendation: PoRecommendationCandidate
  alternatives: PoRecommendationCandidate[]
  reason: string
  promise_date: string | null
  // False when the Lightspeed PO snapshot was cold, so the draft-PO tier could not be
  // evaluated — "no suitable PO" may just mean "could not see the drafts".
  draft_pos_available: boolean
  // Soonest the product can be here by any route.
  fastest_landing_date: string | null
  // When it could have been here had it been ordered the day the special order appeared.
  could_have_landed: string | null
  // The gap between those two: delay we caused. Needs no customer promise, which is why it
  // works for the ~160 special orders that have none.
  days_lost: number | null
}

export interface DwellStat { n: number; median: number; p75: number; max: number }

export interface SoScoreboard {
  as_of: string
  population: {
    live: number
    open: number
    received_awaiting_closeout: number
    stale_beyond_live_window: number
  }
  dwell_days: {
    by_stage: Record<string, DwellStat | null>
    by_store: Record<string, DwellStat | null>
    by_source: Record<string, DwellStat | null>
  }
  promise: {
    with_promise: number
    settled: number
    met: number
    missed: number
    // Denominator is DELIVERED orders only. Counting orders still inside their window as
    // "on time" would flatter the number — most of the population simply has not had the
    // chance to fail yet.
    on_time_pct_vs_original: number | null
    breached_outstanding: number
    undetermined: number
    received_date_unknown?: number
    revised_at_least_once: number
    missing_promise: number
    missing_promise_by_owner: Record<'service' | 'cs', number>
  }
  queue: {
    actionable: number
    by_severity: Record<string, number>
    by_owner: Record<string, number>
    acked: number
    escalated: number
    checkback_due: number
    top_blocking_reasons: [string, number][]
  }
  history: {
    lookback_months: number
    stores: {
      store: string
      n: number
      create_to_place: { p25: number; median: number; p75: number } | null
      place_to_receive: { p25: number; median: number; p75: number } | null
      end_to_end: { p25: number; median: number; p75: number } | null
    }[]
  } | null
}

// Where the special order derives from, for the Source badge/filter. A workorder wins over a
// Shopify match: the service bench is where the request actually originated. 'neither' covers
// SOs raised directly in Lightspeed with no Shopify order and no workorder behind them.
export type SpecialOrderSource = 'workorder' | 'shopify' | 'neither'

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
// 'shopify' is the inbound pseudo-stage for orders with no Lightspeed special order yet.
export type TriageStage = 'shopify' | ProcurementStage

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
  // Explicit individual-special-order receipt state. Optional while cached/Shopify-only rows
  // transition to the new backend contract; `procurement_stage` remains the legacy fallback.
  so_received?: boolean
  so_received_date?: string | null
  receiving_state?: SpecialOrderReceivingState
  // Triage: procurement stage + within-stage attention flag
  procurement_stage: ProcurementStage
  procurement_stage_index: number   // 0=open_pool, 1=unordered_po, 2=ordered, 3=received
  source: SpecialOrderSource
  // Two clocks, deliberately separate. days_since_creation = total elapsed since the customer
  // asked ("will we miss the promise?"); days_in_stage = dwell in the CURRENT step ("is this
  // step stalling?"). An SO can be 92 days old on a PO drafted 2 days ago, or 3 days old on a
  // 48-day-old draft — neither number alone catches both.
  days_in_stage: number | null
  // A THIRD clock, for display and prioritisation only. `days_open` runs from the earlier of the
  // Shopify order date and the Lightspeed SO date, because a customer's wait starts when they
  // ordered, not when we noticed. `days_since_creation` deliberately keeps driving SLA severity,
  // dwell stats, the archive window and `days_lost` so nothing measured against history moves.
  demand_started_date: string | null
  demand_started_source: 'shopify_order' | 'ls_so' | null
  days_open: number | null
  // Days the Shopify order pre-dates the Lightspeed SO — the late-tagging discrepancy. Null when
  // there is no gap or no Shopify order.
  intake_lag_days: number | null
  po_created_date: string | null
  po_received_date: string | null
  po_ref_num: string | null
  days_po_open: number | null
  sale_line_id: string | null
  order_line_id: string | null
  // Manual-link audit. `link_broken` holds the Shopify order id of a hand-made link that no
  // longer resolves — previously this lapsed silently back to auto-matching.
  link_provenance: { shopify_order_id: string; linked_by: string | null; linked_at: string | null } | null
  link_broken: string | null
  // Matched against a fulfilled/archived Shopify order via the late-match fallback. Such an
  // order is deliberately absent from the unmatched list, so say so rather than confuse.
  matched_via_closed_order: boolean
  vendor_lead_time_days: number | null
  // --- SLA verdict (from /api/special-orders/escalations) ---
  sla_severity: SlaSeverity
  sla_severity_rank: number
  sla_owner: 'procurement' | 'receiving' | 'cs'
  sla_reason: string
  promise_date: string | null
  promise_source: 'shopify_metafield' | 'service_manual' | null
  lead_time_days: number
  lead_time_source: 'po_vendor' | 'fastest_qualifying_vendor' | 'default'
  receiving_buffer_days: number
  // Backward-scheduled: promise − lead time − receiving buffer. Negative slack means the
  // last date it could have been ordered has passed.
  order_by_date: string | null
  slack_days: number | null
  // Soonest the CUSTOMER could collect: lead time (or the PO's own ETA) plus the receiving
  // buffer. Distinct from `expected_date`, which is when the box lands at the store.
  earliest_ready_date: string | null
  earliest_ready_basis: 'received' | 'po_eta_plus_buffer' | 'fastest_route' | 'lead_time_default'
  // The date this order must land by, quoted or inferred, and the headroom left against it.
  // Scoring inputs — see `priority_score`. Inferred when nobody ever quoted the customer, which
  // is roughly a third of the board.
  scoring_window_date: string | null
  scoring_window_source: 'customer_promise' | 'po_eta' | 'inferred' | null
  window_slack_days: number | null
  stage_sla_days: number | null
  days_over_stage_sla: number | null
  missing_promise: boolean
  promise_owner: 'service' | 'cs' | null
  // Fastest route and delay cost, computed for every row so they can be sorted on. `days_lost`
  // is the strongest priority signal available: it needs no customer promise, which matters
  // because most special orders have none.
  fastest_landing_date: string | null
  fastest_path_tier: 'in_stock' | 'transfer' | 'inbound_po' | 'new_po' | 'received' | null
  could_have_landed: string | null
  days_lost: number | null
  // Seriousness, 1-10. NOT a restatement of `sla_severity` — that enum is a label with no
  // resolution (`promise_missed` covers a one-day slip and a forty-day one). 7-10 means a real
  // customer promise is already broken, scaled by how late; 1-6 is how much room is left before
  // it lands late; received orders run 1-4 on close-out age. Intrinsic: parking an order mutes
  // the badge but never lowers the number.
  priority_score: number
  priority_band: 'low' | 'medium' | 'high' | 'critical'
  priority_reasons: string[]
  ack: SoAck | null
  ack_active: boolean
  /** The active status, or null when nothing is currently silencing this row. Distinct from
   *  `ack.work_status`, which survives on a record whose re-arm trigger has already fired. */
  work_status: SoWorkStatus | null
  escalation_level: number
  actionable: boolean
  checkback_due: boolean
  work_state: SpecialOrderWorkState
  queue_states: SpecialOrderQueueState[]
  next_action: string | null
  action_owner: SpecialOrderActionOwner | null
  action_due_date: string | null
  /** 'customer_stranded' means the item is recorded as received but the customer's Shopify line
   *  is still unfulfilled — paid for, supposedly here, and they have not got it. */
  closeout_state: string | null
  /** Set when the linked Shopify order is finished ('fulfilled' | 'restocked'). Never set on
   *  received rows — close-out already routes those correctly. */
  shopify_order_closed: string | null
  service_promise_date: string | null
  service_promise_source: 'service_manual' | null
  service_promise_recorded_at: string | null
  service_promise_recorded_by: string | null
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
  // When the customer actually placed the Shopify order. Often earlier than `created_date`: the
  // `SO` tag gets added late and the Lightspeed special order is only raised then.
  shopify_order_created_at: string | null
  /** Units of THIS special order's SKU the customer is still owed on the linked Shopify order.
   *  Null when unknown (no link, or a cached payload from before per-line quantities existed) —
   *  absence of evidence is never treated as evidence. */
  shopify_line_unfulfilled: number | null
  shopify_fulfillment_status: string | null
  shopify_financial_status: string | null
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

export interface SpecialOrderSourceStatus {
  status: 'ok' | 'stale' | 'unavailable'
  fetched_at?: string | null
  checked_at?: string | null
  record_count?: number
  message?: string | null
}

export interface SpecialOrderMeta {
  live_only_days?: number | null
  total_before_window?: number
  total_after_window?: number
  historical_scope?: boolean
  thresholds?: Record<string, unknown>
  sources?: Record<string, SpecialOrderSourceStatus>
  data_freshness?: {
    fetched_at?: string | null
    cache_age_seconds: number
    cache_ttl_seconds: number
  }
}

export interface SpecialOrderWorklistResponse {
  orders: SpecialOrder[]
  summary: SpecialOrderSummarySla
  shopify_only: ShopifyOnlyOrder[]
  fetched_at?: string
  reason_codes?: string[]
  meta?: SpecialOrderMeta
}

// Compatibility name for older imports while the base, non-SLA endpoint is retired.
export type SpecialOrderDashboard = SpecialOrderWorklistResponse

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
