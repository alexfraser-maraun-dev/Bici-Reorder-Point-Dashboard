'use client'

import { useMemo, useState } from 'react'
import { useSpecialOrders } from '@/lib/hooks'
import type { SpecialOrder, ProcurementStage } from '@/lib/types'
import { STAGE_SUBTRIAGES, subKeyForOrder, type TriageTone } from '@/lib/special-order-triage'
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
import { SpecialOrdersTable } from '@/components/dashboard/special-orders-table'
import { SpecialOrderDetailSheet } from '@/components/dashboard/special-orders-detail-sheet'
import {
  Inbox,
  FileClock,
  ShoppingCart,
  PackageCheck,
  RefreshCw,
  Search,
} from 'lucide-react'

// "Live SOs": hide special orders created more than this many days ago (likely abandoned).
const LIVE_SO_MAX_DAYS = 365

// The 4 procurement-flow stages, in order — the primary triage axis.
const stageTiles: {
  key: ProcurementStage
  label: string
  icon: typeof Inbox
  color: string
  bgColor: string
}[] = [
  { key: 'open_pool', label: 'Open Order Pool', icon: Inbox, color: 'text-foreground', bgColor: 'bg-secondary' },
  { key: 'unordered_po', label: 'Unordered PO', icon: FileClock, color: 'text-orange-600', bgColor: 'bg-orange-50' },
  { key: 'ordered', label: 'Ordered', icon: ShoppingCart, color: 'text-blue-600', bgColor: 'bg-blue-50' },
  { key: 'received', label: 'Received', icon: PackageCheck, color: 'text-emerald-600', bgColor: 'bg-emerald-50' },
]

const toneDot: Record<TriageTone, string> = {
  danger: 'bg-red-500',
  warn: 'bg-amber-500',
  ok: 'bg-emerald-500',
}

// Selection is at the sub-triage "segment" level: `${stage}::${subKey}`. A stage tile
// toggles all of its segments at once; a sub-triage toggles just one.
const seg = (stage: ProcurementStage, subKey: string) => `${stage}::${subKey}`

export function SpecialOrdersContent() {
  const { orders, isLoading, refetch, fetchedAt } = useSpecialOrders()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [liveOnly, setLiveOnly] = useState(true)
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [selectedOrder, setSelectedOrder] = useState<SpecialOrder | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)

  // Derive unique store names from loaded orders
  const stores = useMemo(() => {
    const names = new Set<string>()
    orders.forEach((o) => { if (o.store) names.add(o.store) })
    return Array.from(names).sort()
  }, [orders])

  // Base set: everything EXCEPT the stage/sub selection. The tiles read their counts from
  // this, so they react live to "Flagged only" / "Live SOs" / search / store.
  const base = useMemo(() => {
    const term = search.trim().toLowerCase()
    return orders.filter((o) => {
      if (flaggedOnly && o.flag === 'none') return false
      if (storeFilter !== 'all' && o.store !== storeFilter) return false
      if (liveOnly && o.days_since_creation !== null && o.days_since_creation > LIVE_SO_MAX_DAYS) return false
      if (!term) return true
      return [o.customer_name, o.description, o.system_sku, o.vendor_name, o.order_id, o.special_order_id]
        .some((v) => v && String(v).toLowerCase().includes(term))
    })
  }, [orders, flaggedOnly, storeFilter, liveOnly, search])

  // The table additionally applies the stage/sub selection.
  const filtered = useMemo(
    () => (selected.size === 0
      ? base
      : base.filter((o) => selected.has(seg(o.procurement_stage, subKeyForOrder(o))))),
    [base, selected]
  )

  // Counts over the base set, grouped by stage and sub-triage segment.
  const { stageTotals, segCounts } = useMemo(() => {
    const stageTotals: Record<string, number> = {}
    const segCounts: Record<string, number> = {}
    for (const o of base) {
      stageTotals[o.procurement_stage] = (stageTotals[o.procurement_stage] ?? 0) + 1
      const id = seg(o.procurement_stage, subKeyForOrder(o))
      segCounts[id] = (segCounts[id] ?? 0) + 1
    }
    return { stageTotals, segCounts }
  }, [base])

  const toggleSeg = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleStage = (stage: ProcurementStage) => {
    const ids = STAGE_SUBTRIAGES[stage].map((s) => seg(stage, s.key))
    const allOn = ids.every((id) => selected.has(id))
    setSelected((prev) => {
      const next = new Set(prev)
      ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)))
      return next
    })
  }

  const openOrder = (o: SpecialOrder) => {
    setSelectedOrder(o)
    setSheetOpen(true)
  }

  const filtersActive = selected.size > 0 || storeFilter !== 'all' || search || flaggedOnly || !liveOnly

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Special Orders</h1>
          <p className="text-muted-foreground text-sm">
            Live triage — special orders by procurement stage, flagging what needs action in each.
            {fetchedAt && (
              <span className="ml-1 text-xs">Updated {new Date(fetchedAt).toLocaleTimeString()}.</span>
            )}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} className="gap-2">
          <RefreshCw className="h-4 w-4" />
          Sync
        </Button>
      </div>

      {/* Stage tiles (procurement flow) with sub-triage KPIs beneath. Click a stage to
          select all of it, or a sub-triage to drill in. Multiple selectable at once. */}
      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {stageTiles.map((t) => (
            <Card key={t.key} className="py-3">
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
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {stageTiles.map((t) => {
            const Icon = t.icon
            const ids = STAGE_SUBTRIAGES[t.key].map((s) => seg(t.key, s.key))
            const stageActive = ids.length > 0 && ids.every((id) => selected.has(id))
            const total = stageTotals[t.key] ?? 0
            return (
              <Card
                key={t.key}
                className={cn('py-3 transition-colors', stageActive && 'ring-primary ring-2')}
              >
                <CardContent className="px-4 py-0">
                  {/* Stage header — toggles the whole stage */}
                  <button
                    type="button"
                    onClick={() => toggleStage(t.key)}
                    className="flex w-full items-center gap-2 text-left"
                  >
                    <div className={cn('rounded-md p-1.5', t.bgColor)}>
                      <Icon className={cn('h-3.5 w-3.5', t.color)} />
                    </div>
                    <span className="text-muted-foreground text-xs font-medium">{t.label}</span>
                    <span className="ml-auto text-2xl font-semibold tabular-nums">{total.toLocaleString()}</span>
                  </button>

                  {/* Sub-triages — each toggles its own segment */}
                  <div className="mt-2.5 flex flex-col gap-1 border-t pt-2">
                    {STAGE_SUBTRIAGES[t.key].map((s) => {
                      const id = seg(t.key, s.key)
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
            placeholder="Search customer, product, SKU, vendor, PO…"
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
        <label className="flex items-center gap-2 whitespace-nowrap text-sm text-muted-foreground">
          <Checkbox checked={flaggedOnly} onCheckedChange={(v) => setFlaggedOnly(v === true)} />
          Flagged only
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
            onClick={() => { setSelected(new Set()); setStoreFilter('all'); setSearch(''); setFlaggedOnly(false); setLiveOnly(true) }}
          >
            Clear filters
          </Button>
        )}
      </div>

      <SpecialOrdersTable orders={filtered} isLoading={isLoading} onRowClick={openOrder} />

      <SpecialOrderDetailSheet order={selectedOrder} open={sheetOpen} onOpenChange={setSheetOpen} />
    </div>
  )
}
