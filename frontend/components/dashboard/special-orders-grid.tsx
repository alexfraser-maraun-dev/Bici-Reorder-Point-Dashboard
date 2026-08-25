'use client'

import { useMemo, useState } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { SpecialOrder, ShopifyOnlyOrder } from '@/lib/types'
import {
  SPECIAL_ORDER_QUEUE_COLUMNS,
  SpecialOrderRow,
} from './special-order-row'
import { SpecialOrderDetailDrawer } from './special-order-detail-drawer'
import type { MatchActions } from './special-order-match'
import {
  ArrowDownNarrowWide,
  ArrowUpNarrowWide,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'

export type { MatchActions }

const PAGE_SIZE = 25

type SortKey =
  | 'priority_score'
  | 'days_lost'
  | 'fastest_landing_date'
  | 'sla_severity_rank'
  | 'special_order_id'
  | 'customer_name'
  | 'description'
  | 'store'
  | 'expected_date'
  | 'created_date'
  | 'procurement_stage_index'

type SortDir = 'asc' | 'desc'

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'priority_score', label: 'Seriousness' },
  { key: 'days_lost', label: 'Days lost' },
  { key: 'sla_severity_rank', label: 'SLA severity' },
  { key: 'fastest_landing_date', label: 'Soonest landing' },
  { key: 'created_date', label: 'Created date' },
  { key: 'expected_date', label: 'PO expected date' },
  { key: 'procurement_stage_index', label: 'Pipeline stage' },
  { key: 'customer_name', label: 'Customer' },
  { key: 'description', label: 'Product' },
  { key: 'store', label: 'Store' },
  { key: 'special_order_id', label: 'SO number' },
]

const DEFAULT_DIR: Partial<Record<SortKey, SortDir>> = {
  priority_score: 'desc',
  days_lost: 'desc',
  sla_severity_rank: 'asc',
  fastest_landing_date: 'asc',
  created_date: 'asc',
  expected_date: 'asc',
}

function compare(a: SpecialOrder, b: SpecialOrder, key: SortKey, dir: SortDir): number {
  const av: unknown = a[key]
  const bv: unknown = b[key]
  const aNull = av === null || av === undefined || av === ''
  const bNull = bv === null || bv === undefined || bv === ''
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  const result = typeof av === 'number' && typeof bv === 'number'
    ? av - bv
    : String(av).localeCompare(String(bv), undefined, { numeric: true })
  return dir === 'asc' ? result : -result
}

function orderKey(order: SpecialOrder): string {
  return `${order.kind ?? 'ls'}-${order.special_order_id}`
}

interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  onEtaSaved?: () => void | Promise<void>
  lsUnmatched?: SpecialOrder[]
  unmatchedShopify?: ShopifyOnlyOrder[]
  matchActions?: MatchActions
}

export function SpecialOrdersGrid({
  orders,
  isLoading,
  onEtaSaved,
  lsUnmatched = [],
  unmatchedShopify = [],
  matchActions,
}: Props) {
  // Seriousness by default: it is the one column that ranks the WHOLE board on how bad each
  // situation actually is. 'Priority' (the backend queue order) stays available and remains the
  // tiebreak — the sort is stable, so rows on the same score keep their operational ordering.
  const [sortKey, setSortKey] = useState<SortKey | 'default'>('priority_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  // Filtering to a different result set should put you back on page 1. Deriving that from the
  // count during render keeps it in step with the props that caused it, and costs no extra
  // render pass — the parent used to force it by remounting the entire grid on every
  // keystroke, which is exactly what made the filters feel frozen.
  const [lastCount, setLastCount] = useState(orders.length)
  if (lastCount !== orders.length) {
    setLastCount(orders.length)
    setPage(1)
  }

  const sorted = useMemo(() => {
    if (sortKey === 'default') return orders
    return [...orders].sort((a, b) => compare(a, b, sortKey, sortDir))
  }, [orders, sortKey, sortDir])

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const start = (safePage - 1) * PAGE_SIZE
  const visible = sorted.slice(start, start + PAGE_SIZE)
  const selectedOrder = selectedKey
    ? orders.find((order) => orderKey(order) === selectedKey) ?? null
    : null

  if (isLoading) {
    return (
      <div className="space-y-2" aria-label="Loading special orders">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-[140px] w-full rounded-lg" />
        ))}
      </div>
    )
  }

  if (orders.length === 0) {
    return (
      <div className="rounded-lg border border-dashed py-16 text-center">
        <p className="text-sm font-medium">No special orders in this queue</p>
        <p className="mt-1 text-sm text-muted-foreground">Try another queue or clear a filter.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          Showing {start + 1}–{Math.min(start + PAGE_SIZE, sorted.length)} of {sorted.length} orders
        </p>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="special-order-sort">
            Sort by
          </label>
          <Select
            value={sortKey}
            onValueChange={(value) => {
              const key = value as SortKey | 'default'
              setSortKey(key)
              setPage(1)
              if (key !== 'default' && DEFAULT_DIR[key]) setSortDir(DEFAULT_DIR[key]!)
            }}
          >
            <SelectTrigger id="special-order-sort" className="w-[180px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Priority (work queue order)</SelectItem>
              {SORT_OPTIONS.map((option) => (
                <SelectItem key={option.key} value={option.key}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            disabled={sortKey === 'default'}
            onClick={() => {
              setSortDir((current) => current === 'asc' ? 'desc' : 'asc')
              setPage(1)
            }}
            aria-label={sortDir === 'asc' ? 'Sort descending' : 'Sort ascending'}
          >
            {sortDir === 'asc'
              ? <ArrowUpNarrowWide className="h-4 w-4" />
              : <ArrowDownNarrowWide className="h-4 w-4" />}
            {sortDir === 'asc' ? 'Ascending' : 'Descending'}
          </Button>
        </div>
      </div>

      <div
        className={`grid items-center gap-4 px-5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground ${SPECIAL_ORDER_QUEUE_COLUMNS}`}
        aria-hidden="true"
      >
        <span>Order</span>
        <span>Next action</span>
        <span>Status</span>
        <span>Dates</span>
        <span className="text-right">Age</span>
        <span className="sr-only">Review</span>
      </div>

      <div className="space-y-2">
        {visible.map((order) => (
          <SpecialOrderRow
            key={orderKey(order)}
            order={order}
            onReview={(selected) => setSelectedKey(orderKey(selected))}
            onWorkStateChanged={onEtaSaved}
          />
        ))}
      </div>

      {pageCount > 1 && (
        <nav className="flex items-center justify-between border-t pt-3" aria-label="Special orders pagination">
          <p className="text-xs text-muted-foreground">25 orders per page</p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={safePage === 1}
              className="gap-1"
            >
              <ChevronLeft className="h-4 w-4" /> Previous
            </Button>
            <span className="min-w-20 text-center text-sm tabular-nums">
              Page {safePage} of {pageCount}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
              disabled={safePage === pageCount}
              className="gap-1"
            >
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </nav>
      )}

      {selectedOrder && (
        <SpecialOrderDetailDrawer
          order={selectedOrder}
          open={Boolean(selectedOrder)}
          onOpenChange={(open) => { if (!open) setSelectedKey(null) }}
          onEtaSaved={onEtaSaved}
          lsUnmatched={lsUnmatched}
          unmatchedShopify={unmatchedShopify}
          actions={matchActions}
        />
      )}
    </div>
  )
}
