'use client'

import { useState, useMemo } from 'react'
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
import { SpecialOrderRow } from './special-order-row'
import type { MatchActions } from './special-order-match'
import { ArrowDownNarrowWide, ArrowUpNarrowWide } from 'lucide-react'

// Re-exported so existing importers of the grid keep working after the file split.
export type { MatchActions }

type SortKey =
  | 'days_lost'
  | 'fastest_landing_date'
  | 'sla_severity_rank'
  | 'special_order_id'
  | 'customer_name'
  | 'description'
  | 'vendor_name'
  | 'store'
  | 'order_id'
  | 'ordered_date'
  | 'expected_date'
  | 'shopify_expected_date'
  | 'created_date'
  | 'procurement_stage_index'

type SortDir = 'asc' | 'desc'

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  // Days lost is the sharpest priority signal — it needs no customer promise, so it works for
  // the majority of special orders that have none.
  { key: 'days_lost', label: 'Days lost' },
  { key: 'fastest_landing_date', label: 'Soonest it can land' },
  { key: 'sla_severity_rank', label: 'SLA severity' },
  { key: 'created_date', label: 'Created date' },
  { key: 'expected_date', label: 'LS PO ETA' },
  { key: 'shopify_expected_date', label: 'Shopify ETA' },
  { key: 'ordered_date', label: 'Ordered date' },
  { key: 'procurement_stage_index', label: 'Stage' },
  { key: 'customer_name', label: 'Customer' },
  { key: 'description', label: 'Product' },
  { key: 'vendor_name', label: 'Vendor' },
  { key: 'store', label: 'Store' },
  { key: 'order_id', label: 'PO #' },
  { key: 'special_order_id', label: 'SO #' },
]

function compare(a: SpecialOrder, b: SpecialOrder, key: SortKey, dir: SortDir): number {
  const av: unknown = a[key]
  const bv: unknown = b[key]

  // Rows with no value always sink to the bottom, whichever direction is picked.
  const aNull = av === null || av === undefined || av === ''
  const bNull = bv === null || bv === undefined || bv === ''
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1

  const result =
    typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv))
  return dir === 'asc' ? result : -result
}

// Compact label-over-value cell used across the horizontal field grid.
// The item UPC, rendered as a one-click-copy chip. Users paste it into a vendor B2B site to
// research the product, so the copy affordance is the whole point of surfacing it here.
interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  // Called after an ETA is written to Shopify, so the parent can refetch the live value.
  onEtaSaved?: () => void | Promise<void>
  // Link targets for the manual match dialogs — the FULL (unfiltered) populations, so a
  // filtered-out row can still be picked as a link target.
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
  // Default to the parent's server-side ordering (flag severity); only re-sort once the user picks.
  const [sortKey, setSortKey] = useState<SortKey | 'default'>('default')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // Sensible default direction per key: worst-first for severity and delay, soonest-first for
  // dates you are waiting on. Picking a sort should not also require picking a direction.
  const DEFAULT_DIR: Partial<Record<SortKey, SortDir>> = {
    days_lost: 'desc',
    sla_severity_rank: 'asc',
    fastest_landing_date: 'asc',
    created_date: 'asc',
    expected_date: 'asc',
    shopify_expected_date: 'asc',
  }

  const sorted = useMemo(() => {
    if (sortKey === 'default') return orders
    return [...orders].sort((a, b) => compare(a, b, sortKey, sortDir))
  }, [orders, sortKey, sortDir])

  // `default` preserves the backend's ordering, which is SLA severity worst-first — the order a
  // buyer should work the queue in. Any explicit sort overrides it.

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full rounded-xl" />
        ))}
      </div>
    )
  }

  if (orders.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border py-16 text-center text-sm">
        No special orders match the current filters.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Sort control (replaces the table's column-header sorting) */}
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">{sorted.length} orders</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-muted-foreground text-xs">Sort by</span>
          <Select value={sortKey} onValueChange={(v) => {
              const key = v as SortKey | 'default'
              setSortKey(key)
              if (key !== 'default' && DEFAULT_DIR[key]) setSortDir(DEFAULT_DIR[key]!)
            }}>
            <SelectTrigger className="w-[170px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Default (priority)</SelectItem>
              {SORT_OPTIONS.map((o) => (
                <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            disabled={sortKey === 'default'}
            onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
            title={sortDir === 'asc' ? 'Ascending' : 'Descending'}
          >
            {sortDir === 'asc' ? <ArrowUpNarrowWide className="h-4 w-4" /> : <ArrowDownNarrowWide className="h-4 w-4" />}
            {sortDir === 'asc' ? 'Asc' : 'Desc'}
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        {sorted.map((o) => (
          <SpecialOrderRow
            key={`${o.kind ?? 'ls'}-${o.special_order_id}`}
            order={o}
            onEtaSaved={onEtaSaved}
            lsUnmatched={lsUnmatched}
            unmatchedShopify={unmatchedShopify}
            actions={matchActions}
          />
        ))}
      </div>
    </div>
  )
}
