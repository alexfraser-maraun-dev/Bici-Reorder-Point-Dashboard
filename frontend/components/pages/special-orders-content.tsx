'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useSpecialOrders, matchSpecialOrder, unmatchSpecialOrder } from '@/lib/hooks'
import type { SpecialOrder, ShopifyOnlyOrder, TriageStage, SpecialOrderFlag } from '@/lib/types'
import type { TriageTone } from '@/lib/special-order-triage'
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
import { cn } from '@/lib/utils'
import { SoLegend } from '@/components/dashboard/so-legend'
import { SoScoreboard } from '@/components/dashboard/so-scoreboard'
import { SpecialOrdersGrid } from '@/components/dashboard/special-orders-grid'
import {
  Store,
  ListChecks,
  Inbox,
  FileClock,
  ShoppingCart,
  PackageCheck,
  RefreshCw,
  Search,
  AlertTriangle,
} from 'lucide-react'

// "Live SOs": hide special orders created more than this many days ago (likely abandoned).
const LIVE_SO_MAX_DAYS = 365

const seg = (group: string, subKey: string) => `${group}::${subKey}`

type Sub = { key: string; label: string; tone: TriageTone; pred: (o: SpecialOrder) => boolean }
type Tile = { group: string; label: string; icon: typeof Store; color: string; bgColor: string; subs: Sub[] }

const isLs = (o: SpecialOrder) => o.kind !== 'shopify'
// One definition of "needs a human". The old age-based flag system was removed 2026-08-20: it
// disagreed with the SLA verdict on 48 of 219 live rows, and drove a second filter, a second
// sort and its own tiles — two controls answering the same question differently, with no way to
// tell which was right.
const needsAction = (o: SpecialOrder) => isLs(o) && o.actionable

// POSITIONAL tiles: where every special order sits in the pipeline. Deliberately not "action"
// tiles — position is a fact about the order, while what to do about it belongs to the tabs.
// Each splits only into needs-action vs on-track, so the tile never restates the SLA taxonomy.
const STAGE_TILES: { stage: SpecialOrder['procurement_stage']; label: string; icon: typeof Store; color: string; bgColor: string }[] = [
  { stage: 'open_pool', label: 'Awaiting a PO', icon: Inbox, color: 'text-foreground', bgColor: 'bg-secondary' },
  { stage: 'unordered_po', label: 'On an unsent PO', icon: FileClock, color: 'text-orange-600', bgColor: 'bg-orange-50' },
  { stage: 'ordered', label: 'Placed, in transit', icon: ShoppingCart, color: 'text-blue-600', bgColor: 'bg-blue-50' },
  { stage: 'received', label: 'Arrived', icon: PackageCheck, color: 'text-emerald-600', bgColor: 'bg-emerald-50' },
]

// SOURCE tile: where the special order came from. 'Unattributed' is a real order type — raised
// straight into Lightspeed at the counter or by phone — not a data defect: of 57 such live
// orders, 56 verifiably have no Shopify order behind them.
const SOURCE_SUBS: { key: SpecialOrder['source']; label: string; tone: TriageTone }[] = [
  { key: 'shopify', label: 'Shopify', tone: 'ok' },
  { key: 'workorder', label: 'Workorder', tone: 'ok' },
  { key: 'neither', label: 'Unattributed', tone: 'warn' },
]

const buildTiles = (): Tile[] => [
  {
    group: 'source', label: 'Source', icon: Store, color: 'text-violet-600', bgColor: 'bg-violet-50',
    subs: SOURCE_SUBS.map((s) => ({
      key: String(s.key), label: s.label, tone: s.tone,
      pred: (o: SpecialOrder) => (o.source ?? 'neither') === s.key,
    })),
  },
  ...STAGE_TILES.map((t) => ({
    group: t.stage, label: t.label, icon: t.icon, color: t.color, bgColor: t.bgColor,
    subs: [
      { key: 'needs_action', label: 'Needs action', tone: 'danger' as TriageTone,
        pred: (o: SpecialOrder) => isLs(o) && o.procurement_stage === t.stage && needsAction(o) },
      { key: 'on_track', label: 'On track', tone: 'ok' as TriageTone,
        pred: (o: SpecialOrder) => isLs(o) && o.procurement_stage === t.stage && !needsAction(o) },
    ],
  })),
]

// The tabs. Each is a slice of the pipeline plus a plain statement of what removes a row from
// it — the question people actually have when looking at a list of problems.
type ViewKey = 'overview' | 'needs_ordering' | 'in_transit' | 'arrived' | 'problems'

// A row has a "problem" when something about its DATA blocks procurement, as opposed to the
// work simply not being done yet.
const hasProblem = (o: SpecialOrder) =>
  !isLs(o) || Boolean(o.link_broken) || o.shopify_match === 'ambiguous' ||
  (o.missing_promise && o.procurement_stage !== 'received')

const VIEWS: { key: ViewKey; label: string; blurb: string; pred: (o: SpecialOrder) => boolean }[] = [
  {
    key: 'overview', label: 'Overview',
    blurb: 'Every live special order and where it sits. Use the tiles to slice the list; the tabs above narrow it to one kind of work.',
    pred: () => true,
  },
  {
    key: 'needs_ordering', label: 'Needs ordering',
    blurb: 'No purchase order yet, or sitting on a draft that was never sent. A row leaves this list once its PO is placed with the vendor — or sooner, if the item turns out to be in stock or already inbound.',
    pred: (o) => isLs(o) && (o.procurement_stage === 'open_pool' || o.procurement_stage === 'unordered_po'),
  },
  {
    key: 'in_transit', label: 'In transit',
    blurb: 'Placed with the vendor and on its way. A row leaves this list when the item is received against the purchase order. Chase the ones past their expected date.',
    pred: (o) => isLs(o) && o.procurement_stage === 'ordered',
  },
  {
    key: 'arrived', label: 'Arrived',
    blurb: 'The item is here and the SLA clock has stopped — nothing on this list is a late delivery. A retail order leaves it when its Shopify order is fulfilled; a service order when the workorder is closed. Untick \u201cLive SOs\u201d to include the long close-out backlog.',
    pred: (o) => isLs(o) && o.procurement_stage === 'received',
  },
  {
    key: 'problems', label: 'Problems',
    blurb: 'Data standing in the way of procurement rather than work left undone: Shopify orders with no Lightspeed special order, manual links that no longer resolve, ambiguous matches, and orders nobody ever quoted a date for.',
    pred: hasProblem,
  },
]

const toneDot: Record<TriageTone, string> = {
  danger: 'bg-red-500',
  warn: 'bg-amber-500',
  ok: 'bg-emerald-500',
}

function shopifyRow(o: ShopifyOnlyOrder): SpecialOrder {
  const days = o.created_at ? Math.floor((Date.now() - Date.parse(o.created_at)) / 86_400_000) : null
  return {
    special_order_id: o.order_name ?? o.order_id,
    status: 'Shopify',
    unit_quantity: null,
    shop_id: null,
    store: null,
    timestamp: o.created_at,
    created_date: o.created_at ? o.created_at.slice(0, 10) : null,
    days_since_creation: days,
    contacted: false,
    completed: false,
    customer_id: null,
    customer_name: o.customer_email,
    customer_phone: null,
    customer_email: o.customer_email,
    item_id: null,
    system_sku: o.skus[0] ?? null,
    upc: null,
    brand: null,
    available_vendors: [],
    description: o.skus.join(', ') || null,
    order_id: null,
    vendor_id: null,
    vendor_name: null,
    order_type: null,
    expected_date: null,
    ordered_date: null,
    po_ordered: false,
    po_complete: false,
    received_started: false,
    procurement_stage: 'open_pool', // unused for shopify rows — the table branches on `kind`
    procurement_stage_index: -1,
    // A tagged Shopify order with no Lightspeed SO behind it yet: the intake case. Source is
    // 'shopify' because that is genuinely where it came from — the missing piece is the LS SO,
    // not the derivation.
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
    // No Lightspeed special order exists yet, so there is no route to cost out.
    fastest_landing_date: null,
    fastest_path_tier: null,
    could_have_landed: null,
    days_lost: null,
    // Shopify-only rows have no Lightspeed special order, so the backend computes no SLA
    // verdict for them. They are the intake gap (tagged in Shopify, never created in LS) and
    // are surfaced by the Shopify tile — neutral values here keep them out of the procurement
    // severity counts rather than showing up as fake breaches.
    sla_severity: 'no_promise',
    sla_severity_rank: 5,
    sla_owner: 'cs',
    sla_reason: 'Tagged in Shopify but no Lightspeed special order exists yet.',
    promise_date: o.shopify_expected_date ?? null,
    promise_source: o.shopify_expected_date ? 'shopify_metafield' : null,
    lead_time_days: 0,
    lead_time_source: 'default',
    receiving_buffer_days: 0,
    order_by_date: null,
    slack_days: null,
    stage_sla_days: null,
    days_over_stage_sla: null,
    missing_promise: !o.shopify_expected_date,
    promise_owner: o.shopify_expected_date ? null : 'cs',
    ack: null,
    ack_active: false,
    escalation_level: 0,
    actionable: false,
    checkback_due: false,
    flag: 'none',
    days_overdue: null,
    is_overdue: false,
    shopify_match: 'none',
    shopify_match_basis: null,
    shopify_order_id: o.order_id,
    shopify_order_name: o.order_name,
    shopify_order_url: o.shopify_order_url,
    shopify_expected_date: o.shopify_expected_date,
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
    ambiguous_candidate: o.ambiguous_candidate === true,
  }
}

export function SpecialOrdersContent() {
  const { orders, shopifyOnly, sla, isLoading, isRefreshing, refetch, revalidate, fetchedAt, error } =
    useSpecialOrders()
  // Tile labels are derived from the backend's tier boundaries, falling back to the shipped
  // defaults until the first payload lands.
  const TILES = useMemo(() => buildTiles(), [])

  const handleSync = async () => {
    try {
      await refetch()
      toast.success('Special orders synced from Lightspeed.')
    } catch {
      toast.error('Sync failed. Lightspeed may be unavailable — try again.')
    }
  }

  // Manual match/unmatch: persist the override, then pull the rebuilt payload (the backend
  // re-matches over its cached Lightspeed rows — cheap, no full re-walk).
  const matchActions = {
    onMatch: async (specialOrderId: string, shopifyOrderId: string) => {
      try {
        await matchSpecialOrder({ special_order_id: specialOrderId, shopify_order_id: shopifyOrderId })
        toast.success(`Linked SO #${specialOrderId} to the Shopify order.`)
        await revalidate()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to link the orders.')
        throw err
      }
    },
    onUnmatch: async (specialOrderId: string, shopifyOrderId: string) => {
      try {
        await unmatchSpecialOrder({ special_order_id: specialOrderId, shopify_order_id: shopifyOrderId })
        toast.success(`Unlinked SO #${specialOrderId} from the Shopify order.`)
        await revalidate()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to unlink the orders.')
        throw err
      }
    },
  }
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [orderTypeFilter, setOrderTypeFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('all')

  // The active tab. Overview is the landing view; the rest narrow to one kind of work.
  const [view, setView] = useState<ViewKey>('overview')
  const activeView = useMemo(() => VIEWS.find((v) => v.key === view) ?? VIEWS[0], [view])
  // Within a tab, show only rows that still need a human. Replaces both the old 'Flagged only'
  // and 'Needs action' checkboxes, which disagreed with each other.
  const [openWorkOnly, setOpenWorkOnly] = useState(false)
  const [liveOnly, setLiveOnly] = useState(true)


  // Unified rows: live LS SOs + Shopify-only ("Unmatched") pseudo-rows.
  const allRows = useMemo(
    () => [...orders, ...shopifyOnly.map(shopifyRow)],
    [orders, shopifyOnly]
  )

  // Link targets for the manual match dialogs — full populations, ignoring the page filters.
  // Ambiguous SOs are included: linking a "possible match" Shopify order to its ambiguous LS
  // counterpart is the main way an ambiguity gets resolved from the Shopify side.
  const lsUnmatched = useMemo(() => orders.filter((o) => o.shopify_match !== 'matched'), [orders])

  const stores = useMemo(() => {
    const names = new Set<string>()
    orders.forEach((o) => { if (o.store) names.add(o.store) })
    return Array.from(names).sort()
  }, [orders])

  // Order types come from the data rather than a hardcoded Replenishment/Booking pair, so
  // renaming or adding a choice in Lightspeed shows up here without a code change.
  const orderTypes = useMemo(() => {
    const names = new Set<string>()
    orders.forEach((o) => { if (o.order_type) names.add(o.order_type) })
    return Array.from(names).sort()
  }, [orders])

  // Base set: everything EXCEPT the tile selection — so the tile counts react to the toggles.
  const base = useMemo(() => {
    const term = search.trim().toLowerCase()
    return allRows.filter((o) => {
      // The tab is the primary slice. Age is handled solely by the 'Live SOs' toggle below —
      // a second, received-specific age rule here made the Arrived tile and its tab disagree.
      if (!activeView.pred(o)) return false
      if (openWorkOnly && !needsAction(o)) return false
      // 'Needs action' = a real SLA breach nobody has parked. Parked rows stay visible
      // under the other filters so the reason and check-back date remain findable.
      if (storeFilter !== 'all' && o.store !== storeFilter) return false
      // 'none' = the SO has no PO yet, so it has no order type to speak of.
      if (orderTypeFilter === 'none' && o.order_type) return false
      if (orderTypeFilter !== 'all' && orderTypeFilter !== 'none' && o.order_type !== orderTypeFilter) return false
      if (sourceFilter !== 'all' && (o.source ?? 'neither') !== sourceFilter) return false
      if (liveOnly && o.days_since_creation !== null && o.days_since_creation > LIVE_SO_MAX_DAYS) return false
      if (!term) return true
      return [o.customer_name, o.customer_email, o.description, o.system_sku, o.upc, o.brand, o.vendor_name, o.order_id, o.special_order_id, o.shopify_order_name, o.workorder_id,
        ...o.available_vendors.map((v) => v.vendor_name)]
        .some((v) => v && String(v).toLowerCase().includes(term))
    })
  }, [allRows, activeView, view, openWorkOnly, storeFilter, orderTypeFilter, sourceFilter, liveOnly, search])

  // The tiles that currently have ≥1 selected sub-triage. A tile is "active" once you pick any of
  // its buckets; selection combines as AND across tiles, OR within a tile.
  const activeTiles = useMemo(
    () => TILES.filter((t) => t.subs.some((s) => selected.has(seg(t.group, s.key)))),
    [selected]
  )

  // Does row `o` satisfy a tile's selection? (true when the tile has no selection — it's not a
  // constraint yet.) Within the tile it's an OR over the selected buckets' predicates.
  const matchesTile = (o: SpecialOrder, t: Tile) =>
    t.subs.some((s) => selected.has(seg(t.group, s.key)) && s.pred(o))

  // Table = base narrowed to rows passing EVERY active tile (AND across tiles).
  const filtered = useMemo(() => {
    if (activeTiles.length === 0) return base
    return base.filter((o) => activeTiles.every((t) => matchesTile(o, t)))
  }, [base, activeTiles])

  // Faceted counts: each tile's buckets are counted over the rows passing all the OTHER active
  // tiles' selections (a tile ignores its own selection, so you can still see/toggle its buckets).
  const segCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const t of TILES) {
      const others = activeTiles.filter((a) => a.group !== t.group)
      const rows = others.length === 0 ? base : base.filter((o) => others.every((a) => matchesTile(o, a)))
      for (const s of t.subs) counts[seg(t.group, s.key)] = rows.filter(s.pred).length
    }
    return counts
  }, [base, activeTiles])

  const toggleSeg = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleStage = (t: Tile) => {
    const ids = t.subs.map((s) => seg(t.group, s.key))
    const allOn = ids.every((id) => selected.has(id))
    setSelected((prev) => {
      const next = new Set(prev)
      ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)))
      return next
    })
  }

  // Counts per tab over the live population (ignoring the tab itself, so each is stable).
  const viewCounts = useMemo(() => {
    // Same age rule as the list, so a tab badge never disagrees with its tile.
    const inWindow = (o: SpecialOrder) =>
      !liveOnly || o.days_since_creation === null || o.days_since_creation <= LIVE_SO_MAX_DAYS
    const live = allRows
    const out: Record<string, number> = {}
    for (const v of VIEWS) {
      out[v.key] = live.filter((o) => v.pred(o) && inWindow(o)).length
    }
    return out
  }, [allRows, liveOnly])

  const filtersActive =
    selected.size > 0 || storeFilter !== 'all' || orderTypeFilter !== 'all' || sourceFilter !== 'all' || search || openWorkOnly || !liveOnly

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Special Orders</h1>
          <p className="text-muted-foreground text-sm">
            Live triage — Shopify inbound and Lightspeed procurement stages, flagging what needs action.
            {fetchedAt && (
              <span className="ml-1 text-xs">Updated {new Date(fetchedAt).toLocaleTimeString()}.</span>
            )}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleSync}
          disabled={isRefreshing}
          className="gap-2"
        >
          <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
          {isRefreshing ? 'Syncing…' : 'Sync'}
        </Button>
      </div>

      {/* Load failures must never masquerade as "no special orders" — surface them. */}
      {error && (
        <Card className="border-red-300 bg-red-50 py-3 dark:border-red-900 dark:bg-red-950/30">
          <CardContent className="flex flex-wrap items-center gap-3 px-4 py-0">
            <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
            <div className="min-w-0 flex-1 text-sm text-red-800 dark:text-red-300">
              Couldn&apos;t load special orders from the server.
              {orders.length > 0 || shopifyOnly.length > 0
                ? ' Showing the last successfully loaded data.'
                : ' Lightspeed or the backend may be unavailable.'}
            </div>
            <Button variant="outline" size="sm" onClick={handleSync} disabled={isRefreshing} className="gap-2">
              <RefreshCw className={cn('h-3.5 w-3.5', isRefreshing && 'animate-spin')} />
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Triage tiles: Shopify inbound (leftmost) + the four LS procurement stages. */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {TILES.map((t) => (
            <Card key={t.group} className="py-3">
              <CardContent className="px-4 py-0">
                <Skeleton className="mb-2 h-4 w-24" />
                <Skeleton className="h-7 w-12" />
                <Skeleton className="mt-3 h-3 w-full" />
                <Skeleton className="mt-1.5 h-3 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
          {TILES.map((t) => {
            const Icon = t.icon
            const ids = t.subs.map((s) => seg(t.group, s.key))
            const stageActive = ids.every((id) => selected.has(id))
            const total = ids.reduce((sum, id) => sum + (segCounts[id] ?? 0), 0)
            return (
              <Card key={t.group} className={cn('py-3 transition-colors', stageActive && 'ring-primary ring-2')}>
                <CardContent className="px-4 py-0">
                  <button
                    type="button"
                    onClick={() => toggleStage(t)}
                    className="flex w-full items-center gap-2 text-left"
                  >
                    <div className={cn('rounded-md p-1.5', t.bgColor)}>
                      <Icon className={cn('h-3.5 w-3.5', t.color)} />
                    </div>
                    <span className="text-muted-foreground text-xs font-medium">{t.label}</span>
                    <span className="ml-auto text-2xl font-semibold tabular-nums">{total.toLocaleString()}</span>
                  </button>
                  <div className="mt-2.5 flex flex-col gap-1 border-t pt-2">
                    {t.subs.map((s) => {
                      const id = seg(t.group, s.key)
                      const count = segCounts[id] ?? 0
                      const subActive = selected.has(id)
                      return (
                        <button
                          type="button"
                          key={s.key}
                          onClick={() => toggleSeg(id)}
                          className={cn(
                            'flex items-center gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-muted/60',
                            subActive && 'bg-muted ring-1 ring-primary/40',
                            count === 0 && !subActive && 'opacity-50'
                          )}
                        >
                          <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', toneDot[s.tone])} />
                          <span className="text-[11px] text-muted-foreground">{s.label}</span>
                          <span className="ml-auto text-xs font-medium tabular-nums">{count.toLocaleString()}</span>
                        </button>
                      )
                    })}
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Search + filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-sm flex-1">
          <Search className="text-muted-foreground absolute left-2.5 top-2.5 h-4 w-4" />
          <Input
            placeholder="Search customer, product, SKU, vendor, PO, Shopify #…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        <Select value={storeFilter} onValueChange={setStoreFilter}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="All locations" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All locations</SelectItem>
            {stores.map((s) => (
              <SelectItem key={s} value={s}>{s}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="All sources" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            <SelectItem value="workorder">Workorder</SelectItem>
            <SelectItem value="shopify">Shopify</SelectItem>
            {/* Neither a workorder nor a matched Shopify order — raised straight in
                Lightspeed, or a match that was never resolved. Its own bucket by design. */}
            <SelectItem value="neither">Unattributed</SelectItem>
          </SelectContent>
        </Select>
        {orderTypes.length > 0 && (
          <Select value={orderTypeFilter} onValueChange={setOrderTypeFilter}>
            <SelectTrigger className="w-[170px]">
              <SelectValue placeholder="All order types" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All order types</SelectItem>
              {orderTypes.map((t) => (
                <SelectItem key={t} value={t}>{t}</SelectItem>
              ))}
              <SelectItem value="none">No PO yet</SelectItem>
            </SelectContent>
          </Select>
        )}
        <label className="flex items-center gap-2 whitespace-nowrap text-sm text-muted-foreground">
          <Checkbox checked={openWorkOnly} onCheckedChange={(v) => setOpenWorkOnly(v === true)} />
          Needs action
        </label>
        <label className="flex items-center gap-2 whitespace-nowrap text-sm text-muted-foreground">
          <Checkbox checked={liveOnly} onCheckedChange={(v) => setLiveOnly(v === true)} />
          Live SOs
        </label>
        {filtersActive && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => { setSelected(new Set()); setStoreFilter('all'); setOrderTypeFilter('all'); setSourceFilter('all'); setSearch(''); setOpenWorkOnly(false); setLiveOnly(true) }}
          >
            Clear filters
          </Button>
        )}
      </div>

      {/* Tabs are the primary navigation: each is one kind of work, with a plain statement of
          what removes a row from it. Counts are over the live population and do not react to the
          active tab, so switching tabs never makes a backlog look smaller than it is. */}
      <div className="flex flex-wrap items-center gap-1 border-b">
        {VIEWS.map((v) => {
          const count = viewCounts[v.key] ?? 0
          const active = v.key === view
          return (
            <button
              key={v.key}
              type="button"
              onClick={() => { setView(v.key); setSelected(new Set()) }}
              className={cn(
                'flex items-center gap-2 border-b-2 px-3 py-2 text-sm transition-colors',
                active
                  ? 'border-primary font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              {v.label}
              {v.key !== 'overview' && (
                <span className={cn('rounded-full px-1.5 py-0.5 text-[10px] tabular-nums',
                  v.key === 'problems' && count > 0 ? 'bg-red-100 text-red-700'
                    : active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground')}>
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <p className="text-xs text-muted-foreground">{activeView.blurb}</p>

      <SoLegend />

      <SoScoreboard />

      {/* Queue state at a glance. Counts come from the backend over the FULL live population,
          not the current filter, so narrowing the view never makes the backlog look smaller. */}
      {sla && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-md border bg-muted/40 px-3 py-2 text-xs">
          <button
            type="button"
            onClick={() => setOpenWorkOnly((v: boolean) => !v)}
            className={cn('font-medium underline-offset-2 hover:underline',
              sla.actionable > 0 ? 'text-red-600' : 'text-emerald-600')}
          >
            {sla.actionable} need action
          </button>
          {sla.checkback_due > 0 && (
            <span className="text-amber-700">{sla.checkback_due} due for check-back</span>
          )}
          {sla.escalated > 0 && (
            <span className="font-medium text-red-600">{sla.escalated} escalated</span>
          )}
          {sla.acked > 0 && <span className="text-muted-foreground">{sla.acked} parked</span>}
          {sla.missing_promise > 0 && (
            <span className="text-muted-foreground" title="No customer promise recorded, so there is nothing to schedule against">
              {sla.missing_promise} without a promised date
              {sla.missing_promise_by_owner && (
                <span className="opacity-70">
                  {' '}({sla.missing_promise_by_owner.service} service · {sla.missing_promise_by_owner.cs} CS)
                </span>
              )}
            </span>
          )}
        </div>
      )}

      <SpecialOrdersGrid
        orders={filtered}
        isLoading={isLoading}
        onEtaSaved={revalidate}
        lsUnmatched={lsUnmatched}
        unmatchedShopify={shopifyOnly}
        matchActions={matchActions}
      />
    </div>
  )
}
