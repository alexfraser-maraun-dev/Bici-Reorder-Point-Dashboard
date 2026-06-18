'use client'

import { useMemo, useState } from 'react'
import { useSpecialOrders } from '@/lib/hooks'
import type { SpecialOrder, ProcurementStage } from '@/lib/types'
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

type StageFilter = ProcurementStage | 'all'

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

export function SpecialOrdersContent() {
  const { orders, summary, isLoading, refetch, fetchedAt } = useSpecialOrders()
  const [stage, setStage] = useState<StageFilter>('all')
  const [search, setSearch] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [liveOnly, setLiveOnly] = useState(true)
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [selected, setSelected] = useState<SpecialOrder | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)

  // Derive unique store names from loaded orders
  const stores = useMemo(() => {
    const names = new Set<string>()
    orders.forEach((o) => { if (o.store) names.add(o.store) })
    return Array.from(names).sort()
  }, [orders])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return orders.filter((o) => {
      if (stage !== 'all' && o.procurement_stage !== stage) return false
      if (flaggedOnly && o.flag === 'none') return false
      if (storeFilter !== 'all' && o.store !== storeFilter) return false
      // Live SOs: drop year+ old orders, but keep ones with no known created date.
      if (liveOnly && o.days_since_creation !== null && o.days_since_creation > LIVE_SO_MAX_DAYS) return false
      if (!term) return true
      return [o.customer_name, o.description, o.system_sku, o.vendor_name, o.order_id, o.special_order_id]
        .some((v) => v && String(v).toLowerCase().includes(term))
    })
  }, [orders, stage, flaggedOnly, search, storeFilter, liveOnly])

  const openOrder = (o: SpecialOrder) => {
    setSelected(o)
    setSheetOpen(true)
  }

  const filtersActive = stage !== 'all' || storeFilter !== 'all' || search || flaggedOnly || !liveOnly

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

      {/* Stage tiles — the procurement flow; click to filter. Each shows total + flagged. */}
      {isLoading || !summary ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {stageTiles.map((t) => (
            <Card key={t.key} className="py-3">
              <CardContent className="px-4 py-0">
                <Skeleton className="mb-2 h-4 w-24" />
                <Skeleton className="h-7 w-12" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {stageTiles.map((t) => {
            const Icon = t.icon
            const active = stage === t.key
            const total = summary.by_stage?.[t.key] ?? 0
            const flagged = summary.flagged_by_stage?.[t.key] ?? 0
            return (
              <Card
                key={t.key}
                onClick={() => setStage(active ? 'all' : t.key)}
                className={cn(
                  'cursor-pointer py-3 transition-colors hover:bg-muted/50',
                  active && 'ring-primary ring-2'
                )}
              >
                <CardContent className="px-4 py-0">
                  <div className="flex items-center gap-2">
                    <div className={cn('rounded-md p-1.5', t.bgColor)}>
                      <Icon className={cn('h-3.5 w-3.5', t.color)} />
                    </div>
                    <span className="text-muted-foreground text-xs font-medium">{t.label}</span>
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-2">
                    <p className="text-2xl font-semibold tabular-nums">{total.toLocaleString()}</p>
                    {flagged > 0 && (
                      <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">
                        {flagged} flagged
                      </span>
                    )}
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
            onClick={() => { setStage('all'); setStoreFilter('all'); setSearch(''); setFlaggedOnly(false); setLiveOnly(true) }}
          >
            Clear filters
          </Button>
        )}
      </div>

      <SpecialOrdersTable orders={filtered} isLoading={isLoading} onRowClick={openOrder} />

      <SpecialOrderDetailSheet order={selected} open={sheetOpen} onOpenChange={setSheetOpen} />
    </div>
  )
}
