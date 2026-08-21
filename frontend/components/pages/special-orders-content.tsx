'use client'

import { Suspense, useMemo, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import {
  useSpecialOrders,
  matchSpecialOrder,
  saveSpecialOrderMatchDecisions,
  unmatchSpecialOrder,
} from '@/lib/hooks'
import type {
  ShopifyOnlyOrder,
  SpecialOrder,
  SpecialOrderSourceStatus,
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

const ACTION_QUEUE_STATES = new Set(['intake', 'needs_ordering', 'vendor_followup', 'promise_needed', 'closeout'])

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
    pred: (order) => order.queue_states.some((state) => ACTION_QUEUE_STATES.has(state)) &&
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
    pred: (order) => order.queue_states.includes('promise_needed') || Boolean(order.link_broken) || order.shopify_match === 'ambiguous',
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
    stage_sla_days: null,
    days_over_stage_sla: null,
    missing_promise: !order.shopify_expected_date,
    promise_owner: order.shopify_expected_date ? null : 'cs',
    ack: null,
    ack_active: false,
    escalation_level: 0,
    actionable: true,
    checkback_due: false,
    work_state: 'intake',
    queue_states: order.shopify_expected_date ? ['intake'] : ['intake', 'promise_needed'],
    next_action: 'Create the Lightspeed special order',
    action_owner: 'retail',
    action_due_date: order.created_at?.slice(0, 10) ?? null,
    closeout_state: null,
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
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const view = validView(searchParams.get('queue'))
  const search = searchParams.get('q') ?? ''
  const storeFilter = searchParams.get('store') ?? 'all'
  const sourceFilter = searchParams.get('source') ?? 'all'
  const orderTypeFilter = searchParams.get('type') ?? 'all'
  const liveOnly = searchParams.get('archive') !== '1'
  const [insightsOpen, setInsightsOpen] = useState(false)

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

  const replaceParams = (updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString())
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === '') params.delete(key)
      else params.set(key, value)
    })
    const query = params.toString()
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false })
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
  }, [allRows, orderTypeFilter, search, sourceFilter, storeFilter])

  const activeView = VIEWS.find((item) => item.key === view) ?? VIEWS[0]
  const filtered = useMemo(
    () => filteredPopulation.filter(activeView.pred),
    [activeView, filteredPopulation],
  )
  const viewCounts = useMemo(() => {
    const counts: Record<ViewKey, number> = {
      action: 0,
      needs_ordering: 0,
      in_transit: 0,
      ready_to_close: 0,
      data_issues: 0,
      all: 0,
    }
    VIEWS.forEach((item) => { counts[item.key] = filteredPopulation.filter(item.pred).length })
    return counts
  }, [filteredPopulation])
  const pipelineCounts = useMemo(() => {
    const counts: Record<TriageStage, number> = {
      shopify: 0,
      open_pool: 0,
      unordered_po: 0,
      ordered: 0,
      received: 0,
    }
    PIPELINE.forEach((stage) => { counts[stage.key] = filteredPopulation.filter(stage.pred).length })
    return counts
  }, [filteredPopulation])
  const legendCounts = useMemo<SoLegendCounts>(() => {
    const stages: NonNullable<SoLegendCounts['stages']> = {}
    const sources: NonNullable<SoLegendCounts['sources']> = {}
    const severities: NonNullable<SoLegendCounts['severities']> = {}
    filteredPopulation.forEach((order) => {
      if (order.kind === 'shopify') {
        stages.shopify = (stages.shopify ?? 0) + 1
      } else {
        stages[order.procurement_stage] = (stages[order.procurement_stage] ?? 0) + 1
      }
      sources[order.source] = (sources[order.source] ?? 0) + 1
      severities[order.sla_severity] = (severities[order.sla_severity] ?? 0) + 1
    })
    return { stages, sources, severities }
  }, [filteredPopulation])

  const filtersActive = search !== '' || storeFilter !== 'all' || sourceFilter !== 'all' ||
    orderTypeFilter !== 'all' || !liveOnly
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
        <Skeleton className="h-[140px] w-full rounded-lg" />
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
              return (
                <li key={stage.key} className="flex items-center gap-3 px-4 first:pl-0 last:pr-0">
                  <span className={cn('rounded-md border p-2', stage.tone)} aria-hidden="true">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span>
                    <span className="block text-xl font-semibold tabular-nums">{pipelineCounts[stage.key]}</span>
                    <span className="block text-xs text-muted-foreground">{stage.label}</span>
                  </span>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      <Tabs
        value={view}
        onValueChange={(value) => replaceParams({ queue: value === 'action' ? null : value })}
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
              onChange={(event) => replaceParams({ q: event.target.value || null })}
              className="h-8 bg-background pl-8"
            />
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="special-order-store" className="text-xs font-medium text-muted-foreground">Store</label>
            <Select value={storeFilter} onValueChange={(value) => replaceParams({ store: value === 'all' ? null : value })}>
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
            <Select value={sourceFilter} onValueChange={(value) => replaceParams({ source: value === 'all' ? null : value })}>
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
            <Select value={orderTypeFilter} onValueChange={(value) => replaceParams({ type: value === 'all' ? null : value })}>
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

          <label className="ml-auto flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
            <Checkbox
              checked={!liveOnly}
              onCheckedChange={(checked) => replaceParams({ archive: checked === true ? '1' : null })}
            />
            Include archive
          </label>
        </div>

        {filtersActive && (
          <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
            <span className="text-xs font-medium text-muted-foreground">Active filters</span>
            {search && <FilterChip label={`Search: ${search}`} onRemove={() => replaceParams({ q: null })} />}
            {storeFilter !== 'all' && <FilterChip label={`Store: ${storeFilter}`} onRemove={() => replaceParams({ store: null })} />}
            {sourceFilter !== 'all' && <FilterChip label={`Source: ${SOURCE_LABEL[sourceFilter] ?? sourceFilter}`} onRemove={() => replaceParams({ source: null })} />}
            {orderTypeFilter !== 'all' && <FilterChip label={`Type: ${orderTypeFilter === 'none' ? 'No PO yet' : orderTypeFilter}`} onRemove={() => replaceParams({ type: null })} />}
            {!liveOnly && <FilterChip label="Archive included" onRemove={() => replaceParams({ archive: null })} />}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => replaceParams({ q: null, store: null, source: null, type: null, archive: null })}
            >
              Clear all
            </Button>
          </div>
        )}

        {VIEWS.map((item) => (
          <TabsContent key={item.key} value={item.key} className="mt-0 space-y-3">
            {item.key === view && (
              <>
                <p className="text-sm text-muted-foreground">{activeView.description}</p>
                <SpecialOrdersGrid
                  key={`${item.key}:${search}:${storeFilter}:${sourceFilter}:${orderTypeFilter}:${liveOnly}`}
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
