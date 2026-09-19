'use client'

import { Fragment, useMemo, useState } from 'react'
import {
  AlertTriangle, ChevronDown, ChevronRight, Clock3, ExternalLink,
  PackageCheck, PackageSearch, PackageX, Search,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { useStockHistory, useStockIntelligence } from '@/lib/price-intel/hooks'
import type {
  StockHistoryPoint, StockIntelligenceRow, StockStatus,
} from '@/lib/price-intel/types'

const STATUS_META: Record<StockStatus, { label: string; tone: string; dot: string }> = {
  in_stock: {
    label: 'In stock',
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    dot: 'bg-emerald-500',
  },
  low_stock: {
    label: 'Low stock',
    tone: 'border-amber-200 bg-amber-50 text-amber-700',
    dot: 'bg-amber-500',
  },
  out_of_stock: {
    label: 'Out of stock',
    tone: 'border-rose-200 bg-rose-50 text-rose-700',
    dot: 'bg-rose-500',
  },
  preorder: {
    label: 'Pre-order',
    tone: 'border-violet-200 bg-violet-50 text-violet-700',
    dot: 'bg-violet-500',
  },
  backorder: {
    label: 'Backorder',
    tone: 'border-orange-200 bg-orange-50 text-orange-700',
    dot: 'bg-orange-500',
  },
  unknown: {
    label: 'Unknown',
    tone: 'border-slate-200 bg-slate-50 text-slate-500',
    dot: 'bg-slate-300',
  },
}

function StockBadge({ status, stale }: { status: StockStatus; stale?: boolean }) {
  if (stale) {
    return (
      <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-500">
        Stale
      </Badge>
    )
  }
  const meta = STATUS_META[status] ?? STATUS_META.unknown
  return <Badge variant="outline" className={meta.tone}>{meta.label}</Badge>
}

function variantLabel(row: StockIntelligenceRow): string {
  return [row.attribute_1, row.attribute_2, row.attribute_3]
    .filter((value): value is string => Boolean(value?.trim()))
    .join(' / ')
}

function quantityLabel(row: Pick<StockIntelligenceRow, 'reported_quantity' | 'quantity_kind'>) {
  if (row.reported_quantity == null) return 'Not exposed'
  const prefix = row.quantity_kind === 'upper_bound' ? '≤'
    : row.quantity_kind === 'lower_bound' ? '≥' : ''
  return `${prefix}${row.reported_quantity} reported`
}

function localDate(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function SummaryCard({ label, value, note, icon: Icon, tone }: {
  label: string
  value: number
  note: string
  icon: typeof PackageCheck
  tone: string
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between p-4">
        <div>
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
          <p className="mt-1 text-xs text-muted-foreground">{note}</p>
        </div>
        <div className={cn('rounded-md p-2', tone)}><Icon className="h-4 w-4" /></div>
      </CardContent>
    </Card>
  )
}

function dayKey(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function StockHistoryStrip({ row }: { row: StockIntelligenceRow }) {
  const { history, error, isLoading } = useStockHistory(row.item_id, row.competitor_key, 90)
  const days = useMemo(() => {
    const byDay = new Map((history?.points ?? []).map((point) => [point.observed_day, point]))
    const today = new Date()
    const utcToday = new Date(Date.UTC(
      today.getFullYear(), today.getMonth(), today.getDate(),
    ))
    return Array.from({ length: 90 }, (_, index) => {
      const date = new Date(utcToday)
      date.setUTCDate(date.getUTCDate() - (89 - index))
      const key = dayKey(date)
      return { key, point: byDay.get(key) }
    })
  }, [history])

  if (isLoading) return <Skeleton className="h-20 rounded-md" />
  if (error) return <p className="text-xs text-rose-600">Could not load stock history.</p>

  return (
    <div className="space-y-2 rounded-md border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium">90-day observation history</p>
          <p className="text-[11px] text-muted-foreground">
            Blank days were not observed and do not count against availability.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          {(['in_stock', 'low_stock', 'out_of_stock', 'unknown'] as StockStatus[]).map((status) => (
            <span key={status} className="flex items-center gap-1">
              <span className={cn('h-2 w-2 rounded-sm', STATUS_META[status].dot)} />
              {STATUS_META[status].label}
            </span>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-[repeat(30,minmax(0,1fr))] gap-1">
        {days.map(({ key, point }) => {
          const status = point?.stock_status ?? 'unknown'
          const quantity = point?.reported_quantity == null
            ? ''
            : ` · ${quantityLabel(point as StockHistoryPoint)}`
          return (
            <span
              key={key}
              className={cn(
                'aspect-square min-h-2 rounded-[2px]',
                point ? STATUS_META[status].dot : 'bg-slate-200/60',
              )}
              title={`${localDate(`${key}T12:00:00Z`)} · ${point ? STATUS_META[status].label : 'Not observed'}${quantity}`}
            />
          )
        })}
      </div>
    </div>
  )
}

type StatusFilter = StockStatus | 'all' | 'stale'
type QuantityFilter = 'all' | 'reported' | 'not_exposed'

export function StockIntelligence() {
  const { rows, error, isLoading } = useStockIntelligence()
  const [query, setQuery] = useState('')
  const [competitor, setCompetitor] = useState('all')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [quantity, setQuantity] = useState<QuantityFilter>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const competitors = useMemo(() => Array.from(new Map(
    rows.map((row) => [row.competitor_key, row.competitor_name]),
  ).entries()).sort((a, b) => a[1].localeCompare(b[1])), [rows])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return rows.filter((row) => {
      if (competitor !== 'all' && row.competitor_key !== competitor) return false
      if (status === 'stale' ? !row.stale : status !== 'all' && row.stock_status !== status) return false
      if (quantity === 'reported' && row.reported_quantity == null) return false
      if (quantity === 'not_exposed' && row.reported_quantity != null) return false
      if (!needle) return true
      return [row.brand, row.title, row.sku, row.system_sku, row.competitor_name,
        variantLabel(row)].some((value) => value?.toLowerCase().includes(needle))
    }).sort((a, b) => {
      const aLiveOos = !a.stale && a.stock_status === 'out_of_stock' ? 0 : 1
      const bLiveOos = !b.stale && b.stock_status === 'out_of_stock' ? 0 : 1
      if (aLiveOos !== bLiveOos) return aLiveOos - bLiveOos
      if (a.stale !== b.stale) return a.stale ? 1 : -1
      const aRate = a.availability_rate_30d ?? 2
      const bRate = b.availability_rate_30d ?? 2
      if (aRate !== bRate) return aRate - bRate
      return `${a.brand ?? ''} ${a.title ?? ''}`.localeCompare(`${b.brand ?? ''} ${b.title ?? ''}`)
    })
  }, [rows, query, competitor, status, quantity])

  const summary = useMemo(() => ({
    out: rows.filter((row) => !row.stale && row.stock_status === 'out_of_stock').length,
    low: rows.filter((row) => !row.stale && row.stock_status === 'low_stock').length,
    quantity: rows.filter((row) => !row.stale && row.reported_quantity != null).length,
    uncertain: rows.filter((row) => row.stale || row.stock_status === 'unknown').length,
  }), [rows])

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-28" />)}
        </div>
        <Skeleton className="h-96" />
      </div>
    )
  }

  if (error) {
    return (
      <Card><CardContent className="py-10 text-center text-sm text-rose-600">
        Stock intelligence could not be loaded. Price tracking is unaffected.
      </CardContent></Card>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label="Out of stock" value={summary.out}
                     note="Current, non-stale listings" icon={PackageX}
                     tone="bg-rose-50 text-rose-700" />
        <SummaryCard label="Low stock" value={summary.low}
                     note="Explicit retailer signal" icon={AlertTriangle}
                     tone="bg-amber-50 text-amber-700" />
        <SummaryCard label="Quantity reported" value={summary.quantity}
                     note="Public variant-level count" icon={PackageCheck}
                     tone="bg-emerald-50 text-emerald-700" />
        <SummaryCard label="Stale or unknown" value={summary.uncertain}
                     note="Needs a fresh observation" icon={Clock3}
                     tone="bg-slate-100 text-slate-600" />
      </div>

      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-56 flex-1">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={query} onChange={(event) => setQuery(event.target.value)}
                     placeholder="Search product, variant, SKU, or store"
                     className="pl-8" />
            </div>
            <Select value={competitor} onValueChange={setCompetitor}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Competitor" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All competitors</SelectItem>
                {competitors.map(([key, name]) => <SelectItem key={key} value={key}>{name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={status} onValueChange={(value) => setStatus(value as StatusFilter)}>
              <SelectTrigger className="w-40"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {(Object.keys(STATUS_META) as StockStatus[]).map((key) => (
                  <SelectItem key={key} value={key}>{STATUS_META[key].label}</SelectItem>
                ))}
                <SelectItem value="stale">Stale</SelectItem>
              </SelectContent>
            </Select>
            <Select value={quantity} onValueChange={(value) => setQuantity(value as QuantityFilter)}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Quantity" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any quantity signal</SelectItem>
                <SelectItem value="reported">Quantity reported</SelectItem>
                <SelectItem value="not_exposed">Quantity not exposed</SelectItem>
              </SelectContent>
            </Select>
            <Badge variant="secondary" className="h-7 px-2.5">{filtered.length} listings</Badge>
          </div>

          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>Product / variant</TableHead>
                  <TableHead>Competitor</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Reported quantity</TableHead>
                  <TableHead>30-day availability</TableHead>
                  <TableHead>Out of stock</TableHead>
                  <TableHead className="text-right">Restocks</TableHead>
                  <TableHead>Last checked</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.length === 0 ? (
                  <TableRow><TableCell colSpan={9} className="h-28 text-center text-muted-foreground">
                    No competitor stock observations match these filters.
                  </TableCell></TableRow>
                ) : filtered.map((row) => {
                  const key = `${row.item_id}|${row.competitor_key}`
                  const open = expanded === key
                  const variant = variantLabel(row)
                  return (
                    <Fragment key={key}>
                    <TableRow className="cursor-pointer"
                              onClick={() => setExpanded(open ? null : key)}>
                      <TableCell>
                        <button aria-label={open ? 'Collapse stock history' : 'Expand stock history'}>
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </TableCell>
                      <TableCell className="min-w-64">
                        <p className="font-medium">{row.brand ? `${row.brand} ` : ''}{row.title ?? 'Untitled item'}</p>
                        <p className="text-xs text-muted-foreground">
                          {[variant, row.system_sku ? `System ID ${row.system_sku}` : null]
                            .filter(Boolean).join(' · ') || 'Single product'}
                        </p>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <span>{row.competitor_name}</span>
                          {row.url && (
                            <a href={row.url} target="_blank" rel="noopener noreferrer"
                               title="Open competitor page" onClick={(event) => event.stopPropagation()}
                               className="text-muted-foreground hover:text-foreground">
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          )}
                        </div>
                      </TableCell>
                      <TableCell><StockBadge status={row.stock_status} stale={row.stale} /></TableCell>
                      <TableCell className="tabular-nums">
                        <span title="Retailer-reported public inventory; not audited physical stock">
                          {quantityLabel(row)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {row.availability_rate_30d == null ? '—' : (
                          <div>
                            <p className="font-medium tabular-nums">
                              {Math.round(row.availability_rate_30d * 100)}%
                            </p>
                            <p className="text-[11px] text-muted-foreground">
                              {row.observed_nights_30d}/30 nights observed
                            </p>
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {row.outage_days == null ? '—' : `${row.outage_censored ? '≥' : ''}${row.outage_days} days`}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{row.restocks_90d}</TableCell>
                      <TableCell>
                        <p>{localDate(row.last_observed_at)}</p>
                        {row.stale && <p className="text-[11px] text-muted-foreground">over 48h ago</p>}
                      </TableCell>
                    </TableRow>
                    {open && (
                      <TableRow>
                        <TableCell colSpan={9} className="bg-muted/10 px-12 py-3">
                          <StockHistoryStrip row={row} />
                        </TableCell>
                      </TableRow>
                    )}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>
          </div>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <PackageSearch className="h-3.5 w-3.5" />
            Counts are retailer-reported public availability, not audited physical inventory.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
