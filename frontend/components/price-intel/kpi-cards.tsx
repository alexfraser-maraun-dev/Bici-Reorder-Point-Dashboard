'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { PriceIntelSummary } from '@/lib/price-intel/types'
import { ArrowDown, ArrowUp, Barcode, Bell, Gauge, ShieldAlert, ShieldCheck, Target } from 'lucide-react'

function fmtIndex(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toFixed(2)
}

// filterKey: 'changes' switches to the change-feed tab; the rest drive the
// tracked-products filters (cheaper/pricier/map/violations). undefined = not clickable.
export function PriceIntelKpiCards({ summary, isLoading, mapViolations, onSelect }: {
  summary?: PriceIntelSummary
  isLoading: boolean
  mapViolations?: number
  onSelect?: (filterKey: string) => void
}) {
  if (isLoading || !summary) {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-[86px] rounded-xl" />
        ))}
      </div>
    )
  }

  const index = summary.price_index_vs_market_min
  const violations = mapViolations ?? 0
  const cards = [
    {
      label: 'Tracked products',
      value: `${summary.tracked_count}`,
      sub: `${summary.compared_count} with market data`,
      icon: Target,
      tone: 'text-sky-600 bg-sky-50',
    },
    {
      label: 'Price index vs market min',
      value: fmtIndex(index),
      sub: index == null ? 'no comparisons yet' : index > 1 ? 'we price above market' : 'at/below market',
      icon: Gauge,
      tone: index != null && index > 1.05 ? 'text-amber-600 bg-amber-50' : 'text-emerald-600 bg-emerald-50',
    },
    {
      label: 'Cheaper than market',
      value: `${summary.cheaper}`,
      sub: `${summary.parity} at parity`,
      icon: ArrowDown,
      tone: 'text-emerald-600 bg-emerald-50',
      filterKey: 'cheaper',
    },
    {
      label: 'Pricier than market',
      value: `${summary.pricier}`,
      sub: 'vs lowest in-stock offer',
      icon: ArrowUp,
      tone: 'text-rose-600 bg-rose-50',
      filterKey: 'pricier',
    },
    {
      label: 'Unread changes',
      value: `${summary.unacknowledged_changes}`,
      sub: 'in the change feed',
      icon: Bell,
      tone: summary.unacknowledged_changes > 0 ? 'text-amber-600 bg-amber-50' : 'text-muted-foreground bg-muted',
      filterKey: 'changes',
    },
    {
      label: 'MAP-tagged items',
      value: `${summary.map_tracked_count}`,
      sub: summary.map_tracked_count === 0 ? 'tag items “map” in Lightspeed' : 'monitored for violations',
      icon: ShieldCheck,
      tone: 'text-violet-600 bg-violet-50',
      filterKey: 'map',
    },
    {
      label: 'MAP violations',
      value: `${violations}`,
      sub: violations > 0 ? 'competitor below MAP floor' : 'none below MAP',
      icon: ShieldAlert,
      tone: violations > 0 ? 'text-amber-700 bg-amber-100' : 'text-muted-foreground bg-muted',
      filterKey: 'violations',
    },
    {
      label: 'UPC coverage',
      value: `${summary.upc_tracked_count}/${summary.tracked_count}`,
      sub: 'tracked items with a barcode',
      icon: Barcode,
      tone: 'text-slate-600 bg-slate-100',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      {cards.map((card) => {
        const clickable = !!card.filterKey && !!onSelect
        return (
          <Card key={card.label}
                onClick={clickable ? () => onSelect!(card.filterKey!) : undefined}
                className={cn(clickable && 'cursor-pointer transition-colors hover:border-foreground/30 hover:bg-muted/40')}>
            <CardContent className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-muted-foreground">{card.label}</p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">{card.value}</p>
                  <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{card.sub}</p>
                </div>
                <div className={cn('rounded-md p-1.5', card.tone)}>
                  <card.icon className="h-4 w-4" />
                </div>
              </div>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
