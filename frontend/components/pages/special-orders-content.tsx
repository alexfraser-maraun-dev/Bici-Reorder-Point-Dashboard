'use client'

import { useMemo, useState } from 'react'
import { useSpecialOrders } from '@/lib/hooks'
import type { SpecialOrder } from '@/lib/types'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
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
  AlertTriangle,
  ShieldAlert,
  CircleHelp,
  PackageCheck,
  Layers,
  Clock,
  RefreshCw,
  Search,
} from 'lucide-react'

type QuickFilter = 'all' | 'overdue' | 'critical' | 'no_eta' | 'ready_not_called' | 'unordered_too_long'

const tiles: {
  key: QuickFilter
  label: string
  icon: typeof AlertTriangle
  color: string
  bgColor: string
  summaryKey: 'total_open' | 'overdue' | 'critical' | 'no_eta' | 'ready_not_called' | 'unordered_too_long'
}[] = [
  { key: 'all', label: 'Total Open', icon: Layers, color: 'text-foreground', bgColor: 'bg-secondary', summaryKey: 'total_open' },
  { key: 'overdue', label: 'Overdue', icon: AlertTriangle, color: 'text-red-600', bgColor: 'bg-red-50', summaryKey: 'overdue' },
  { key: 'critical', label: 'Critical (8d+)', icon: ShieldAlert, color: 'text-red-700', bgColor: 'bg-red-50', summaryKey: 'critical' },
  { key: 'no_eta', label: 'No ETA', icon: CircleHelp, color: 'text-amber-600', bgColor: 'bg-amber-50', summaryKey: 'no_eta' },
  { key: 'ready_not_called', label: 'Ready · Not Called', icon: PackageCheck, color: 'text-emerald-600', bgColor: 'bg-emerald-50', summaryKey: 'ready_not_called' },
  { key: 'unordered_too_long', label: 'Stale · Not Ordered', icon: Clock, color: 'text-orange-600', bgColor: 'bg-orange-50', summaryKey: 'unordered_too_long' },
]

function matchesFilter(o: SpecialOrder, filter: QuickFilter): boolean {
  switch (filter) {
    case 'overdue':      return o.is_overdue
    case 'critical':     return o.aging_bucket === 'critical' || o.aging_bucket === 'stale'
    case 'no_eta':       return o.no_eta
    case 'ready_not_called': return o.ready_not_called
    case 'unordered_too_long': return o.unordered_too_long
    default:             return true
  }
}

export function SpecialOrdersContent() {
  const { orders, summary, isLoading, refetch, fetchedAt } = useSpecialOrders()
  const [filter, setFilter] = useState<QuickFilter>('all')
  const [search, setSearch] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
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
      if (!matchesFilter(o, filter)) return false
      if (storeFilter !== 'all' && o.store !== storeFilter) return false
      if (!term) return true
      return [o.customer_name, o.description, o.system_sku, o.vendor_name, o.order_id, o.special_order_id]
        .some((v) => v && String(v).toLowerCase().includes(term))
    })
  }, [orders, filter, search, storeFilter])

  const openOrder = (o: SpecialOrder) => {
    setSelected(o)
    setSheetOpen(true)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Special Orders</h1>
          <p className="text-muted-foreground text-sm">
            Live execution queue — flagging special orders whose purchase order is overdue.
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

      {/* KPI tiles — click to filter the queue */}
      {isLoading || !summary ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {tiles.map((t) => (
            <Card key={t.key} className="py-3">
              <CardContent className="px-4 py-0">
                <Skeleton className="mb-2 h-4 w-20" />
                <Skeleton className="h-7 w-12" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {tiles.map((t) => {
            const Icon = t.icon
            const active = filter === t.key
            return (
              <Card
                key={t.key}
                onClick={() => setFilter(active && t.key !== 'all' ? 'all' : t.key)}
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
                  <p className="mt-1.5 text-2xl font-semibold tabular-nums">
                    {summary[t.summaryKey].toLocaleString()}
                  </p>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Search + store filter */}
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
        {(filter !== 'all' || storeFilter !== 'all' || search) && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => { setFilter('all'); setStoreFilter('all'); setSearch('') }}
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
