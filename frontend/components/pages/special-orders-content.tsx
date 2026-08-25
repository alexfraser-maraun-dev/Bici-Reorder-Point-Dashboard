'use client'

import { Suspense, useEffect, useMemo, useState } from 'react'
import { usePathname, useSearchParams, type ReadonlyURLSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import {
  useSpecialOrders,
  matchSpecialOrder,
  saveSpecialOrderMatchDecisions,
  unmatchSpecialOrder,
} from '@/lib/hooks'
import type {
  ShopifyOnlyOrder,
  SoWorkStatus,
  SpecialOrder,
  SpecialOrderQueueState,
  SpecialOrderSourceStatus,
  SpecialOrderWorkState,
  TriageStage,
} from '@/lib/types'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { SoLegend, type SoLegendCounts } from '@/components/dashboard/so-legend'
import { SoScoreboard } from '@/components/dashboard/so-scoreboard'
import { SpecialOrdersGrid } from '@/components/dashboard/special-orders-grid'
import {
  DWELL_BANDS,
  dwellBand,
  emptyDwellCounts,
  stageDwellDays,
  type DwellCounts,
} from '@/lib/special-order-triage'
import {
  AlertTriangle,
  BarChart3,
  FileClock,
  Inbox,
  PackageCheck,
  RefreshCw,
  Search,
  Store,
  Truck,
  X,
} from 'lucide-react'

type ViewKey = 'action' | 'needs_ordering' | 'in_transit' | 'ready_to_close' | 'data_issues' | 'all'
type ActionFilterKey =
  | 'all'
  | SpecialOrderWorkState
  | 'receipt_exception'
  | 'in_progress'
  | 'done'
type ActionFilterOrder = {
  queue_states: readonly SpecialOrderQueueState[]
  work_state: SpecialOrderWorkState
  receiving_state?: string | null
  work_status?: SoWorkStatus | null
}

// 'in_progress' and 'done' are the two statuses that, by design, sit OUTSIDE the active
// queues — they are how a row leaves Action required. Selecting one therefore has to escape
// the queue predicate, or it would always come back empty and read as a broken filter.
const CLEARED_WORK_FILTERS = new Set<ActionFilterKey>(['in_progress', 'done'])

const ACTION_QUEUE_STATES = new Set(['intake', 'needs_ordering', 'shopify_fulfilled', 'vendor_followup', 'promise_needed', 'closeout'])

export const ACTION_FILTERS: readonly { key: ActionFilterKey; label: string }[] = [
  { key: 'all', label: 'All actions' },
  { key: 'intake', label: 'Shopify intake' },
  { key: 'needs_ordering', label: 'Needs ordering' },
  { key: 'shopify_fulfilled', label: 'Shopify already fulfilled' },
  { key: 'vendor_followup', label: 'Vendor follow-up' },
  { key: 'promise_needed', label: 'Promise date needed' },
  { key: 'receipt_exception', label: 'Split shipment / backorder' },
  { key: 'closeout', label: 'Ready to close' },
  { key: 'on_track', label: 'No action needed' },
  { key: 'in_progress', label: 'In progress (started)' },
  { key: 'done', label: 'Done' },
]

export function validActionFilter(value: string | null): ActionFilterKey {
  return ACTION_FILTERS.some((filter) => filter.key === value) ? value as ActionFilterKey : 'all'
}

export function matchesActionFilter(
  order: ActionFilterOrder,
  filter: ActionFilterKey,
): boolean {
  if (filter === 'all') return true
  if (filter === 'in_progress' || filter === 'done') return order.work_status === filter
  if (filter === 'receipt_exception') {
    return order.receiving_state === 'po_receiving' || order.receiving_state === 'po_complete_so_unreceived'
  }
  // Ordered healthy rows remain in the in-transit lifecycle queue, so their queue_states do
  // not also contain on_track. Every actionable filter uses the multi-valued queue membership
  // so parallel work (for example ordering + promise capture) remains discoverable in both.
  if (filter === 'on_track') return order.work_state === 'on_track' && !isReceiptException(order)
  return order.queue_states.includes(filter)
}

function isReceiptException(order: ActionFilterOrder): boolean {
  return matchesActionFilter(order, 'receipt_exception')
}

const VIEWS: {
  key: ViewKey
  label: string
  description: string
  pred: (order: SpecialOrder) => boolean
}[] = [
  {
    key: 'action',
    label: 'Action required',
    description: 'Work that needs an owner now. Parked orders return here when their check-back is due.',
    pred: (order) => (order.queue_states.some((state) => ACTION_QUEUE_STATES.has(state)) || isReceiptException(order)) &&
      (!order.ack_active || order.checkback_due),
  },
  {
    key: 'needs_ordering',
    label: 'Needs ordering',
    description: 'Shopify intake and Lightspeed orders that still need a supply route confirmed in Lightspeed.',
    pred: (order) => order.queue_states.includes('intake') || order.queue_states.includes('needs_ordering'),
  },
  {
    key: 'in_transit',
    label: 'In transit',
    description: 'Orders already placed with a vendor. Prioritize missed dates and vendor follow-up.',
    pred: (order) => order.queue_states.includes('in_transit'),
  },
  {
    key: 'ready_to_close',
    label: 'Ready to close',
    description: 'Items received but still waiting for customer, Shopify, retail, or service close-out.',
    pred: (order) => order.queue_states.includes('closeout'),
  },
  {
    key: 'data_issues',
    label: 'Data issues',
    description: 'Missing promises and Shopify links that need a human decision before the record can be trusted.',
    pred: (order) => order.queue_states.includes('promise_needed') || Boolean(order.link_broken)
      || order.shopify_match === 'ambiguous' || order.queue_states.includes('shopify_fulfilled'),
  },
  {
    key: 'all',
    label: 'All',
    description: 'Every order in the current live or historical scope.',
    pred: () => true,
  },
]

const PIPELINE: {
  key: TriageStage
  label: string
  icon: typeof Store
  tone: string
  pred: (order: SpecialOrder) => boolean
}[] = [
  {
    key: 'shopify',
    label: 'Shopify intake',
    icon: Store,
    tone: 'border-violet-200 bg-violet-50 text-violet-700',
    pred: (order) => order.kind === 'shopify',
  },
  {
    key: 'open_pool',
    label: 'Awaiting PO',
    icon: Inbox,
    tone: 'border-slate-200 bg-slate-50 text-slate-700',
    pred: (order) => order.kind !== 'shopify' && order.procurement_stage === 'open_pool',
  },
  {
    key: 'unordered_po',
    label: 'Draft PO',
    icon: FileClock,
    tone: 'border-orange-200 bg-orange-50 text-orange-700',
    pred: (order) => order.kind !== 'shopify' && order.procurement_stage === 'unordered_po',
  },
  {
    key: 'ordered',
    label: 'In transit',
    icon: Truck,
    tone: 'border-blue-200 bg-blue-50 text-blue-700',
    pred: (order) => order.kind !== 'shopify' && order.procurement_stage === 'ordered',
  },
  {
    key: 'received',
    label: 'Arrived',
    icon: PackageCheck,
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    pred: (order) => order.kind !== 'shopify' && order.procurement_stage === 'received',
  },
]

const SOURCE_LABEL: Record<string, string> = {
  lightspeed: 'Lightspeed',
  shopify: 'Shopify',
  bigquery: 'BigQuery',
  workorders: 'Workorders',
  workorder: 'Workorder',
  workflow: 'Workflow state',
  neither: 'Lightspeed direct',
}

function formatFreshness(value: string): string {
  const ageSeconds = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 1000))
  if (!Number.isFinite(ageSeconds) || ageSeconds < 60) return 'just now'
  const minutes = Math.floor(ageSeconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hr ago`
  const days = Math.floor(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

function validView(value: string | null): ViewKey {
  return VIEWS.some((view) => view.key === value) ? value as ViewKey : 'action'
}

interface FilterState {
  view: ViewKey
  search: string
  store: string
  source: string
  orderType: string
  action: ActionFilterKey
  liveOnly: boolean
}

function readFilters(params: URLSearchParams | ReadonlyURLSearchParams): FilterState {
  return {
    view: validView(params.get('queue')),
    search: params.get('q') ?? '',
    store: params.get('store') ?? 'all',
    source: params.get('source') ?? 'all',
    orderType: params.get('type') ?? 'all',
    action: validActionFilter(params.get('action')),
    liveOnly: params.get('archive') !== '1',
  }
}

/** The shareable-link half of the filter state. Every default is omitted so a clean worklist
 *  keeps a clean URL. */
function filterQuery(filters: FilterState): string {
  const params = new URLSearchParams()
  if (filters.view !== 'action') params.set('queue', filters.view)
  if (filters.search) params.set('q', filters.search)
  if (filters.store !== 'all') params.set('store', filters.store)
  if (filters.source !== 'all') params.set('source', filters.source)
  if (filters.orderType !== 'all') params.set('type', filters.orderType)
  if (filters.action !== 'all') params.set('action', filters.action)
  if (!filters.liveOnly) params.set('archive', '1')
  return params.toString()
}

function shopifyRow(order: ShopifyOnlyOrder): SpecialOrder {
  const days = order.created_at
    ? Math.floor((Date.now() - Date.parse(order.created_at)) / 86_400_000)
    : null
  return {
    special_order_id: order.order_name ?? order.order_id,
    status: 'Shopify',
    unit_quantity: null,
    shop_id: null,
    store: null,
    timestamp: order.created_at,
    created_date: order.created_at?.slice(0, 10) ?? null,
    days_since_creation: days,
    contacted: false,
    completed: false,
    customer_id: null,
    customer_name: order.customer_email,
    customer_phone: null,
    customer_email: order.customer_email,
    item_id: null,
    system_sku: order.skus[0] ?? null,
    upc: null,
    brand: null,
    available_vendors: [],
    description: order.skus.join(', ') || null,
    order_id: null,
    vendor_id: null,
    vendor_name: null,
    order_type: null,
    expected_date: null,
    ordered_date: null,
    po_ordered: false,
    po_complete: false,
    received_started: false,
    procurement_stage: 'open_pool',
    procurement_stage_index: -1,
    source: 'shopify',
    days_in_stage: days,
    // A Shopify-only row IS the Shopify order, so the open clock and the order date are the same
    // thing and there is no intake lag to report.
    demand_started_date: order.created_at?.slice(0, 10) ?? null,
    demand_started_source: 'shopify_order',
    days_open: days,
    intake_lag_days: null,
    po_created_date: null,
    po_received_date: null,
    po_ref_num: null,
    days_po_open: null,
    sale_line_id: null,
    order_line_id: null,
    vendor_lead_time_days: null,
    link_provenance: null,
    link_broken: null,
    matched_via_closed_order: false,
    fastest_landing_date: null,
    fastest_path_tier: null,
    could_have_landed: null,
    days_lost: null,
    sla_severity: 'no_promise',
    sla_severity_rank: 5,
    sla_owner: 'cs',
    sla_reason: 'This Shopify order still needs a Lightspeed special order.',
    promise_date: order.shopify_expected_date,
    promise_source: order.shopify_expected_date ? 'shopify_metafield' : null,
    lead_time_days: 0,
    lead_time_source: 'default',
    receiving_buffer_days: 0,
    order_by_date: null,
    slack_days: null,
    // Nothing can be scheduled until a Lightspeed special order exists, so there is no landing
    // date to quote and no window to score against.
    earliest_ready_date: null,
    earliest_ready_basis: 'lead_time_default',
    scoring_window_date: order.shopify_expected_date,
    scoring_window_source: order.shopify_expected_date ? 'customer_promise' : null,
    window_slack_days: null,
    stage_sla_days: null,
    days_over_stage_sla: null,
    missing_promise: !order.shopify_expected_date,
    promise_owner: order.shopify_expected_date ? null : 'cs',
    // Raising the Lightspeed special order is real work, but an order that arrived this morning
    // is rarely the most serious thing on the board. Backend rows get a computed score.
    priority_score: 3,
    priority_band: 'medium',
    priority_reasons: ['No Lightspeed special order raised yet'],
    ack: null,
    ack_active: false,
    work_status: null,
    escalation_level: 0,
    actionable: true,
    checkback_due: false,
    work_state: 'intake',
    queue_states: order.shopify_expected_date ? ['intake'] : ['intake', 'promise_needed'],
    next_action: 'Create the Lightspeed special order',
    action_owner: 'retail',
    action_due_date: order.created_at?.slice(0, 10) ?? null,
    closeout_state: null,
    shopify_order_closed: null,
    service_promise_date: null,
    service_promise_source: null,
    service_promise_recorded_at: null,
    service_promise_recorded_by: null,
    flag: 'none',
    days_overdue: null,
    is_overdue: false,
    shopify_match: 'none',
    shopify_match_basis: null,
    shopify_order_id: order.order_id,
    shopify_order_name: order.order_name,
    shopify_order_url: order.shopify_order_url,
    shopify_expected_date: order.shopify_expected_date,
    shopify_order_created_at: order.created_at,
    shopify_line_unfulfilled: null,
    shopify_fulfillment_status: order.fulfillment_status,
    shopify_financial_status: order.financial_status,
    shopify_candidates: [],
    workorder_id: null,
    workorder_status: null,
    workorder_note: null,
    workorder_internal_note: null,
    workorder_hook_in: null,
    workorder_eta_out: null,
    workorder_time_in: null,
    workorder_url: null,
    ls_item_url: null,
    ls_customer_url: null,
    ls_order_url: null,
    kind: 'shopify',
    ambiguous_candidate: order.ambiguous_candidate === true,
  }
}

function compareOperationalPriority(a: SpecialOrder, b: SpecialOrder): number {
  const aActive = a.actionable || a.checkback_due
  const bActive = b.actionable || b.checkback_due
  if (aActive !== bActive) return aActive ? -1 : 1
  if (a.sla_severity_rank !== b.sla_severity_rank) {
    return a.sla_severity_rank - b.sla_severity_rank
  }
  const dueCompare = (a.action_due_date ?? '9999-12-31').localeCompare(
    b.action_due_date ?? '9999-12-31',
  )
  if (dueCompare !== 0) return dueCompare
  return (b.days_since_creation ?? 0) - (a.days_since_creation ?? 0)
}

function SourceHealth({ sources }: { sources?: Record<string, SpecialOrderSourceStatus> }) {
  const entries = Object.entries(sources ?? {})
  if (entries.length === 0) return null
  const degraded = entries.filter(([, health]) => health.status !== 'ok')
  if (degraded.length === 0) {
    return (
      <span
        className="ml-2 inline-flex items-center gap-1"
        aria-label="All Special Orders data sources are healthy"
        title={entries.map(([source]) => SOURCE_LABEL[source] ?? source).join(', ')}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
        All systems healthy
      </span>
    )
  }
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-2" aria-label="Data source health">
      {degraded.map(([source, health]) => (
        <span
          key={source}
          className="inline-flex items-center gap-1"
          title={health.message ?? `${SOURCE_LABEL[source] ?? source}: ${health.status}`}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              health.status === 'ok' && 'bg-emerald-500',
              health.status === 'stale' && 'bg-amber-500',
              health.status === 'unavailable' && 'bg-red-500',
            )}
            aria-hidden="true"
          />
          {SOURCE_LABEL[source] ?? source}
          <span> {health.status}</span>
        </span>
      ))}
    </span>
  )
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      className="h-7 gap-1.5 rounded-full px-2.5 text-xs"
      onClick={onRemove}
      aria-label={`Remove filter: ${label}`}
    >
      {label}
      <X className="h-3.5 w-3.5" />
    </Button>
  )
}

function SpecialOrdersFallback() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-8 w-52" />
          <Skeleton className="h-4 w-96" />
        </div>
        <Skeleton className="h-8 w-24" />
      </div>
      <Skeleton className="h-24 w-full rounded-lg" />
      <Skeleton className="h-10 w-full" />
      {Array.from({ length: 6 }).map((_, index) => (
        <Skeleton key={index} className="h-[140px] w-full rounded-lg" />
      ))}
    </div>
  )
}

export function SpecialOrdersContent() {
  return (
    <Suspense fallback={<SpecialOrdersFallback />}>
      <SpecialOrdersContentInner />
    </Suspense>
  )
}

function SpecialOrdersContentInner() {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  // Filters are ordinary React state, seeded once from the URL on mount.
  //
  // They used to live IN the URL, written through router.replace(). That made every keystroke
  // and every dropdown a Next.js navigation: an RSC round-trip to the server before the
  // control could show its own new value, and — because router.replace defers the update —
  // a controlled <Input> that reverted characters mid-word. Two filters changed inside one
  // pending navigation also clobbered each other, since both rebuilt their params from the
  // same stale searchParams snapshot. The result read exactly as "the filters are frozen".
  //
  // Local state updates synchronously; the URL is mirrored below for shareable links.
  const [filters, setFilters] = useState<FilterState>(() => readFilters(searchParams))
  const { view, search, store: storeFilter, source: sourceFilter,
          orderType: orderTypeFilter, action: actionFilter, liveOnly } = filters
  const [insightsOpen, setInsightsOpen] = useState(false)

  // window.history.replaceState is the documented Next.js shallow-routing escape hatch: it
  // updates the address bar and stays in sync with useSearchParams without re-running the
  // route. Guarded on the current URL so it is a no-op when nothing actually moved.
  useEffect(() => {
    const query = filterQuery(filters)
    const next = query ? `${pathname}?${query}` : pathname
    if (`${window.location.pathname}${window.location.search}` !== next) {
      window.history.replaceState(null, '', next)
    }
  }, [filters, pathname])

  const {
    orders,
    shopifyOnly,
    isLoading,
    isRefreshing,
    refetch,
    revalidate,
    fetchedAt,
    sourceHealth,
    error,
  } = useSpecialOrders({ liveOnly })

  const updateFilters = (updates: Partial<FilterState>) => {
    setFilters((current) => ({ ...current, ...updates }))
  }
  const CLEARED_FILTERS: Omit<FilterState, 'view'> = {
    search: '',
    store: 'all',
    source: 'all',
    orderType: 'all',
    action: 'all',
    liveOnly: true,
  }

  const handleSync = async () => {
    try {
      await refetch()
      toast.success('Special Orders data refreshed.')
    } catch {
      toast.error('Refresh failed. The current cached worklist remains visible.')
    }
  }

  const refreshAfterSavedMutation = async () => {
    try {
      await revalidate()
    } catch {
      toast.warning('The change was saved, but the worklist could not refresh. Use Refresh data to try again.')
    }
  }

  const matchActions = {
    onMatch: async (specialOrderId: string, shopifyOrderId: string) => {
      try {
        await matchSpecialOrder({ special_order_id: specialOrderId, shopify_order_id: shopifyOrderId })
        toast.success(`Linked SO #${specialOrderId} to the Shopify order.`)
      } catch (matchError) {
        toast.error(matchError instanceof Error ? matchError.message : 'Failed to link the orders.')
        throw matchError
      }
      await refreshAfterSavedMutation()
    },
    onUnmatch: async (specialOrderId: string, shopifyOrderId: string) => {
      try {
        await unmatchSpecialOrder({ special_order_id: specialOrderId, shopify_order_id: shopifyOrderId })
        toast.success(`Unlinked SO #${specialOrderId} from the Shopify order.`)
      } catch (unmatchError) {
        toast.error(unmatchError instanceof Error ? unmatchError.message : 'Failed to unlink the orders.')
        throw unmatchError
      }
      await refreshAfterSavedMutation()
    },
    onBatchUnmatch: async (specialOrderId: string, shopifyOrderIds: string[]) => {
      try {
        await saveSpecialOrderMatchDecisions(shopifyOrderIds.map((shopifyOrderId) => ({
          special_order_id: specialOrderId,
          shopify_order_id: shopifyOrderId,
          action: 'unlink',
        })))
        toast.success(`Excluded ${shopifyOrderIds.length} Shopify candidate${shopifyOrderIds.length === 1 ? '' : 's'} from SO #${specialOrderId}.`)
      } catch (batchError) {
        toast.error(batchError instanceof Error ? batchError.message : 'Failed to exclude the candidates.')
        throw batchError
      }
      await refreshAfterSavedMutation()
    },
  }

  const allRows = useMemo(
    () => [...orders, ...shopifyOnly.map(shopifyRow)].sort(compareOperationalPriority),
    [orders, shopifyOnly],
  )
  const lsUnmatched = useMemo(
    () => orders.filter((order) => order.shopify_match !== 'matched'),
    [orders],
  )
  const stores = useMemo(() => (
    Array.from(new Set(orders.map((order) => order.store).filter((store): store is string => Boolean(store)))).sort()
  ), [orders])
  const orderTypes = useMemo(() => (
    Array.from(new Set(orders.map((order) => order.order_type).filter((type): type is string => Boolean(type)))).sort()
  ), [orders])

  const filteredPopulation = useMemo(() => {
    const term = search.trim().toLowerCase()
    return allRows.filter((order) => {
      if (storeFilter !== 'all' && order.store !== storeFilter) return false
      if (sourceFilter !== 'all' && (order.source ?? 'neither') !== sourceFilter) return false
      if (orderTypeFilter === 'none' && order.order_type) return false
      if (orderTypeFilter !== 'all' && orderTypeFilter !== 'none' && order.order_type !== orderTypeFilter) return false
      if (!matchesActionFilter(order, actionFilter)) return false
      if (!term) return true
      return [
        order.customer_name,
        order.customer_email,
        order.description,
        order.system_sku,
        order.upc,
        order.brand,
        order.vendor_name,
        order.order_id,
        order.special_order_id,
        order.shopify_order_name,
        order.workorder_id,
        order.next_action,
        ...order.available_vendors.map((vendor) => vendor.vendor_name),
      ].some((value) => value && String(value).toLowerCase().includes(term))
    })
  }, [actionFilter, allRows, orderTypeFilter, search, sourceFilter, storeFilter])

  const activeView = VIEWS.find((item) => item.key === view) ?? VIEWS[0]
  // Started and done rows have already left every active queue, so the queue predicate would
  // filter them all back out. When one of those statuses is what you asked for, it wins.
  const showsClearedWork = CLEARED_WORK_FILTERS.has(actionFilter)
  const filtered = useMemo(
    () => showsClearedWork ? filteredPopulation : filteredPopulation.filter(activeView.pred),
    [activeView, filteredPopulation, showsClearedWork],
  )
  // Every counter the page renders, in ONE pass.
  //
  // These used to be three `useMemo`s running twelve separate `.filter()` sweeps over the same
  // array (five pipeline predicates, six queue predicates, one legend fold). Adding the per-stage
  // dwell split would have made it thirty-two. The predicates stay the source of truth — they are
  // called inside the loop rather than reimplemented — so the strip got richer while the page got
  // cheaper.
  const counts = useMemo(() => {
    const views: Record<ViewKey, number> = {
      action: 0, needs_ordering: 0, in_transit: 0, ready_to_close: 0, data_issues: 0, all: 0,
    }
    const pipeline: Record<TriageStage, number> = {
      shopify: 0, open_pool: 0, unordered_po: 0, ordered: 0, received: 0,
    }
    const dwell: Record<TriageStage, DwellCounts> = {
      shopify: emptyDwellCounts(), open_pool: emptyDwellCounts(), unordered_po: emptyDwellCounts(),
      ordered: emptyDwellCounts(), received: emptyDwellCounts(),
    }
    const stages: NonNullable<SoLegendCounts['stages']> = {}
    const sources: NonNullable<SoLegendCounts['sources']> = {}
    const severities: NonNullable<SoLegendCounts['severities']> = {}

    filteredPopulation.forEach((order) => {
      for (const item of VIEWS) {
        // Started/done rows have already left every active queue, so the predicates would filter
        // them all back out. When one of those statuses is what you asked for, it wins.
        if (showsClearedWork || item.pred(order)) views[item.key] += 1
      }
      for (const stage of PIPELINE) {
        if (!stage.pred(order)) continue
        pipeline[stage.key] += 1
        dwell[stage.key][dwellBand(stageDwellDays(order))] += 1
      }
      if (order.kind === 'shopify') {
        stages.shopify = (stages.shopify ?? 0) + 1
      } else {
        stages[order.procurement_stage] = (stages[order.procurement_stage] ?? 0) + 1
      }
      sources[order.source] = (sources[order.source] ?? 0) + 1
      severities[order.sla_severity] = (severities[order.sla_severity] ?? 0) + 1
    })
    return { views, pipeline, dwell, legend: { stages, sources, severities } as SoLegendCounts }
  }, [filteredPopulation, showsClearedWork])
  const { views: viewCounts, pipeline: pipelineCounts, dwell: dwellCounts } = counts
  const legendCounts = counts.legend

  const filtersActive = search !== '' || storeFilter !== 'all' || sourceFilter !== 'all' ||
    orderTypeFilter !== 'all' || actionFilter !== 'all' || !liveOnly
  const degradedSources = Object.entries(sourceHealth ?? {}).filter(([, health]) => health.status !== 'ok')

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Special Orders</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Work the next action across Shopify, Lightspeed purchasing, receiving, and service.
            {fetchedAt && (
              <time
                className="ml-1 text-xs"
                dateTime={fetchedAt}
                title={new Date(fetchedAt).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
              >
                Updated {formatFreshness(fetchedAt)}.
              </time>
            )}
            <SourceHealth sources={sourceHealth} />
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            aria-haspopup="dialog"
            aria-expanded={insightsOpen}
            aria-controls="special-orders-insights"
            onClick={() => setInsightsOpen(true)}
          >
            <BarChart3 className="h-4 w-4" />
            Insights
          </Button>
          <Button variant="outline" size="sm" onClick={handleSync} disabled={isRefreshing} className="gap-2">
            <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
            {isRefreshing ? 'Refreshing…' : 'Refresh data'}
          </Button>
        </div>
      </header>

      {(error || degradedSources.length > 0) && (
        <Card className="border-amber-300 bg-amber-50 py-3 dark:border-amber-900 dark:bg-amber-950/30">
          <CardContent className="flex items-center gap-3 px-4 py-0">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-700" />
            <p className="min-w-0 flex-1 text-sm text-amber-900 dark:text-amber-200">
              {error
                ? 'The live refresh failed. The last successfully loaded worklist remains visible.'
                : degradedSources.map(([source, health]) => `${SOURCE_LABEL[source] ?? source}: ${health.message ?? health.status}`).join(' · ')}
            </p>
            <Button variant="outline" size="sm" onClick={handleSync} disabled={isRefreshing}>Retry</Button>
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <Skeleton className="h-[220px] w-full rounded-lg" />
      ) : (
        <section className="rounded-lg border bg-card px-4 py-3" aria-labelledby="pipeline-heading">
          <div className="mb-2 flex items-center justify-between">
            <h2 id="pipeline-heading" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {liveOnly ? 'Live pipeline' : 'Live and historical pipeline'}
            </h2>
            <span className="text-sm font-semibold tabular-nums">{filteredPopulation.length} total</span>
          </div>
          <ol className="grid grid-cols-5 divide-x" aria-label="Special order pipeline">
            {PIPELINE.map((stage) => {
              const Icon = stage.icon
              const bands = dwellCounts[stage.key]
              const total = pipelineCounts[stage.key]
              return (
                <li key={stage.key} className="px-4 first:pl-0 last:pr-0">
                  <div className="flex items-center gap-3">
                    <span className={cn('rounded-md border p-2', stage.tone)} aria-hidden="true">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span>
                      <span className="block text-xl font-semibold tabular-nums">{pipelineCounts[stage.key]}</span>
                      <span className="block text-xs text-muted-foreground">{stage.label}</span>
                    </span>
                  </div>
                  {/* How long these have been in THIS step. The bar carries the shape at a
                      glance — a bucket going red is legible before you read a single number —
                      and the grid keeps the four figures aligned across all five stages.
                      Not <li> elements on purpose: the pipeline list is five items and the
                      e2e assertion depends on that. */}
                  <div
                    className="mt-3 flex h-1.5 gap-px overflow-hidden rounded-full bg-muted"
                    aria-hidden="true"
                  >
                    {total > 0 && DWELL_BANDS.map((band) => (
                      bands[band.key] > 0 && (
                        <span
                          key={band.key}
                          className={band.bar}
                          style={{ width: `${(bands[band.key] / total) * 100}%` }}
                        />
                      )
                    ))}
                  </div>
                  <dl
                    className="mt-2 grid grid-cols-4 gap-1 text-center"
                    aria-label={`${stage.label} by time in stage`}
                  >
                    {DWELL_BANDS.map((band) => {
                      const count = bands[band.key]
                      return (
                        <div key={band.key} className="flex flex-col-reverse">
                          <dt className="text-[10px] leading-tight text-muted-foreground">
                            {band.label}
                          </dt>
                          <dd className={cn(
                            'text-sm font-semibold leading-snug tabular-nums',
                            count === 0
                              ? 'text-muted-foreground/40'
                              : band.key === 'stalled' && 'text-red-600',
                          )}>
                            {count}
                          </dd>
                        </div>
                      )
                    })}
                  </dl>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      <Tabs
        value={view}
        onValueChange={(value) => updateFilters({ view: validView(value) })}
        className="gap-3"
      >
        <TabsList className="h-11 w-full justify-start rounded-none border-b bg-transparent p-0" aria-label="Special order queues">
          {VIEWS.map((item) => (
            <TabsTrigger
              key={item.key}
              value={item.key}
              className="h-11 flex-none rounded-none border-x-0 border-t-0 px-3 shadow-none data-[state=active]:border-b-2 data-[state=active]:border-b-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            >
              {item.label}
              <span className={cn(
                'rounded-full bg-muted px-1.5 py-0.5 text-xs tabular-nums',
                item.key === 'action' && viewCounts[item.key] > 0 && 'bg-red-100 text-red-700',
              )}>
                {viewCounts[item.key]}
              </span>
            </TabsTrigger>
          ))}
        </TabsList>

        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/20 px-3 py-2">
          <div className="relative min-w-[280px] flex-1 max-w-[430px]">
            <label htmlFor="special-order-search" className="sr-only">Search special orders</label>
            <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
            <Input
              id="special-order-search"
              type="search"
              placeholder="Search order, customer, product, SKU, PO…"
              value={search}
              onChange={(event) => updateFilters({ search: event.target.value })}
              className="h-8 bg-background pl-8"
            />
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="special-order-store" className="text-xs font-medium text-muted-foreground">Store</label>
            <Select value={storeFilter} onValueChange={(value) => updateFilters({ store: value })}>
              <SelectTrigger id="special-order-store" className="w-[145px] bg-background" size="sm">
                <SelectValue placeholder="All stores" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All stores</SelectItem>
                {stores.map((store) => <SelectItem key={store} value={store}>{store}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="special-order-source" className="text-xs font-medium text-muted-foreground">Source</label>
            <Select value={sourceFilter} onValueChange={(value) => updateFilters({ source: value })}>
              <SelectTrigger id="special-order-source" className="w-[135px] bg-background" size="sm">
                <SelectValue placeholder="All sources" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All sources</SelectItem>
                <SelectItem value="shopify">Shopify</SelectItem>
                <SelectItem value="workorder">Workorder</SelectItem>
                <SelectItem value="neither">Lightspeed direct</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="special-order-type" className="text-xs font-medium text-muted-foreground">Type</label>
            <Select value={orderTypeFilter} onValueChange={(value) => updateFilters({ orderType: value })}>
              <SelectTrigger id="special-order-type" className="w-[150px] bg-background" size="sm">
                <SelectValue placeholder="All PO types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All PO types</SelectItem>
                {orderTypes.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}
                <SelectItem value="none">No PO yet</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="special-order-action" className="text-xs font-medium text-muted-foreground">Action</label>
            <Select value={actionFilter} onValueChange={(value) => updateFilters({ action: validActionFilter(value) })}>
              <SelectTrigger id="special-order-action" className="w-[205px] bg-background" size="sm">
                <SelectValue placeholder="All actions" />
              </SelectTrigger>
              <SelectContent>
                {ACTION_FILTERS.map((filter) => (
                  <SelectItem key={filter.key} value={filter.key}>{filter.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <label className="ml-auto flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
            <Checkbox
              checked={!liveOnly}
              onCheckedChange={(checked) => updateFilters({ liveOnly: checked !== true })}
            />
            Include archive
          </label>
        </div>

        {filtersActive && (
          <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
            <span className="text-xs font-medium text-muted-foreground">Active filters</span>
            {search && <FilterChip label={`Search: ${search}`} onRemove={() => updateFilters({ search: '' })} />}
            {storeFilter !== 'all' && <FilterChip label={`Store: ${storeFilter}`} onRemove={() => updateFilters({ store: 'all' })} />}
            {sourceFilter !== 'all' && <FilterChip label={`Source: ${SOURCE_LABEL[sourceFilter] ?? sourceFilter}`} onRemove={() => updateFilters({ source: 'all' })} />}
            {orderTypeFilter !== 'all' && <FilterChip label={`Type: ${orderTypeFilter === 'none' ? 'No PO yet' : orderTypeFilter}`} onRemove={() => updateFilters({ orderType: 'all' })} />}
            {actionFilter !== 'all' && <FilterChip label={`Action: ${ACTION_FILTERS.find((filter) => filter.key === actionFilter)?.label ?? actionFilter}`} onRemove={() => updateFilters({ action: 'all' })} />}
            {!liveOnly && <FilterChip label="Archive included" onRemove={() => updateFilters({ liveOnly: true })} />}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => updateFilters(CLEARED_FILTERS)}
            >
              Clear all
            </Button>
          </div>
        )}

        {VIEWS.map((item) => (
          <TabsContent key={item.key} value={item.key} className="mt-0 space-y-3">
            {item.key === view && (
              <>
                <p className="text-sm text-muted-foreground">
                  {showsClearedWork
                    ? actionFilter === 'done'
                      ? 'Tasks someone marked done. Reopen one to send it back to Action required.'
                      : 'Tasks someone has started. They return to Action required if they are still open in a few days.'
                    : activeView.description}
                </p>
                <SpecialOrdersGrid
                  orders={filtered}
                  isLoading={isLoading}
                  onEtaSaved={revalidate}
                  lsUnmatched={lsUnmatched}
                  unmatchedShopify={shopifyOnly}
                  matchActions={matchActions}
                />
              </>
            )}
          </TabsContent>
        ))}
      </Tabs>

      <Sheet open={insightsOpen} onOpenChange={setInsightsOpen}>
        <SheetContent id="special-orders-insights" className="w-[760px] gap-0 p-0 sm:max-w-[760px]">
          <SheetHeader className="border-b px-6 py-5 pr-12">
            <SheetTitle>Special Orders insights</SheetTitle>
            <SheetDescription>Queue logic, delivery performance, dwell time, and ownership.</SheetDescription>
          </SheetHeader>
          {insightsOpen && (
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
              <SoScoreboard />
              <SoLegend counts={legendCounts} />
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
