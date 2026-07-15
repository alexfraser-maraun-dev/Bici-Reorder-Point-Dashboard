'use client'

import { Fragment, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { usePoWatch, fetchPoWatchLines, ackPoWatchAlert, unackPoWatchAlert } from '@/lib/hooks'
import type { PoWatchLine, PoWatchOrder, PoWatchTriage } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  AlarmClock,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BellOff,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Timer,
} from 'lucide-react'

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 })

const TRIAGE_META: Record<PoWatchTriage, { label: string; badge: string; card: string }> = {
  critical: { label: 'Critical (15d+ late)', badge: 'bg-red-600 text-white', card: 'border-red-500/60' },
  very_late: { label: 'Very late (8–14d)', badge: 'bg-orange-500 text-white', card: 'border-orange-400/60' },
  late: { label: 'Late (1–7d)', badge: 'bg-amber-400 text-black', card: 'border-amber-300/60' },
  due_soon: { label: 'Due within 7d', badge: 'bg-sky-500 text-white', card: 'border-sky-400/50' },
  no_eta: { label: 'No ETA', badge: 'bg-muted text-muted-foreground', card: 'border-muted' },
  on_track: { label: 'On track', badge: 'bg-emerald-600 text-white', card: 'border-emerald-500/40' },
}

const TRIAGE_KEYS: PoWatchTriage[] = ['critical', 'very_late', 'late', 'due_soon', 'no_eta', 'on_track']

const FLAG_META: Record<string, { label: string; hint: string }> = {
  expected_faster_than_median: {
    label: 'Optimistic ETA',
    hint: 'The expected date promises delivery faster than this vendor’s median lead time for this shop.',
  },
  expected_before_ordered: {
    label: 'Stale ETA',
    hint: 'The expected date is earlier than the ordered date — likely stale data entry; fix the ETA in Lightspeed.',
  },
  past_median_lead_time: {
    label: 'Past median lead time',
    hint: 'Nothing received yet and the PO has been out longer than this vendor’s median lead time.',
  },
  fully_received_not_closed: {
    label: 'Fully received',
    hint: 'All units have arrived — this PO just needs to be checked in / closed in Lightspeed.',
  },
  implied_expected: {
    label: 'Implied ETA',
    hint: 'No expected date was entered; lateness is measured against ordered date + median vendor lead time.',
  },
  no_expected_date: {
    label: 'No expected date',
    hint: 'No expected date was entered on this PO in Lightspeed.',
  },
}

function fmtDate(value: string | null | undefined) {
  return value || '—'
}

// Column sorting. 'severity' is the backend's default order (worst first).
type SortKey =
  | 'severity' | 'order_id' | 'vendor' | 'shop' | 'created' | 'ordered'
  | 'expected' | 'units' | 'cost' | 'received' | 'lead_time'

const SORT_VALUE: Record<Exclude<SortKey, 'severity'>, (o: PoWatchOrder) => number | string | null> = {
  order_id: (o) => Number(o.order_id),
  vendor: (o) => o.vendor_name.toLowerCase(),
  shop: (o) => o.shop_name.toLowerCase(),
  created: (o) => o.created_date,
  ordered: (o) => o.ordered_date,
  // Sort the Expected column by urgency: most-late first when descending.
  expected: (o) => (o.days_late != null ? o.days_late : o.days_until_expected != null ? -o.days_until_expected : null),
  units: (o) => o.units_ordered,
  cost: (o) => o.cost_ordered,
  received: (o) => o.received_pct,
  lead_time: (o) => o.median_lead_time_days,
}

function sortOrders(orders: PoWatchOrder[], key: SortKey, dir: 1 | -1): PoWatchOrder[] {
  if (key === 'severity') return dir === 1 ? orders : [...orders].reverse()
  const value = SORT_VALUE[key]
  return [...orders].sort((a, b) => {
    const va = value(a)
    const vb = value(b)
    if (va == null && vb == null) return 0
    if (va == null) return 1 // nulls always last, regardless of direction
    if (vb == null) return -1
    if (va < vb) return -dir
    if (va > vb) return dir
    return 0
  })
}

function SortableHead({ label, sortKey, sort, onSort, className }: {
  label: string
  sortKey: SortKey
  sort: { key: SortKey; dir: 1 | -1 }
  onSort: (key: SortKey) => void
  className?: string
}) {
  const active = sort.key === sortKey
  const Icon = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 hover:text-foreground ${active ? 'text-foreground' : ''}`}
      >
        {label}
        <Icon className={`h-3 w-3 ${active ? '' : 'opacity-40'}`} />
      </button>
    </TableHead>
  )
}

function LinesPanel({ orderId }: { orderId: string }) {
  const [lines, setLines] = useState<PoWatchLine[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchPoWatchLines(orderId)
      .then((result) => { if (!cancelled) setLines(result) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load PO lines') })
    return () => { cancelled = true }
  }, [orderId])

  if (error) return <div className="p-4 text-sm text-destructive">{error}</div>
  if (!lines) return <div className="space-y-2 p-4"><Skeleton className="h-5 w-full" /><Skeleton className="h-5 w-2/3" /></div>
  if (lines.length === 0) return <div className="p-4 text-sm text-muted-foreground">No line items on this PO.</div>

  return (
    <Table>
      <TableHeader><TableRow className="text-xs">
        <TableHead>SKU</TableHead><TableHead>Item</TableHead>
        <TableHead className="text-right">Ordered</TableHead><TableHead className="text-right">Received</TableHead>
        <TableHead className="text-right">Unit cost</TableHead><TableHead className="text-right">Total</TableHead>
      </TableRow></TableHeader>
      <TableBody>
        {lines.map((line) => (
          <TableRow key={line.order_line_id} className={line.received >= line.quantity ? 'text-muted-foreground' : ''}>
            <TableCell className="font-mono text-xs">{line.sku || line.item_id}</TableCell>
            <TableCell className="max-w-96 truncate text-sm">{line.description || `Item ${line.item_id}`}</TableCell>
            <TableCell className="text-right">{line.quantity}</TableCell>
            <TableCell className="text-right">{line.received}</TableCell>
            <TableCell className="text-right">{money.format(line.unit_cost)}</TableCell>
            <TableCell className="text-right">{money.format(line.total)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function AckMenu({ order, onDone }: { order: PoWatchOrder; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)

  const ack = async (snoozeDays: number | null, label: string) => {
    setBusy(true)
    try {
      await ackPoWatchAlert({
        order_id: order.order_id,
        expected_date: order.expected_date,
        snooze_days: snoozeDays,
        note: label,
      })
      await onDone()
      toast.success(`PO #${order.order_id} acknowledged — ${label}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Acknowledgement failed')
    } finally {
      setBusy(false)
    }
  }

  const unack = async () => {
    setBusy(true)
    try {
      await unackPoWatchAlert(order.order_id)
      await onDone()
      toast.success(`PO #${order.order_id} re-armed for alerts`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to clear acknowledgement')
    } finally {
      setBusy(false)
    }
  }

  if (order.ack?.active) {
    return (
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="sm" variant="ghost" className="h-7 gap-1 text-muted-foreground" disabled={busy} onClick={unack}>
              <BellOff className="h-3.5 w-3.5" />Snoozed
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <div className="max-w-64 text-xs">
              Acknowledged by {order.ack.acked_by || 'someone'} on {order.ack.acked_at?.slice(0, 10)}
              {order.ack.snooze_until ? ` · re-alerts after ${order.ack.snooze_until}` : ' · until the ETA changes'}
              . Click to re-arm now.
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  if (!order.days_late) return null

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 gap-1" disabled={busy}>
          <AlarmClock className="h-3.5 w-3.5" />Ack
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel className="text-xs">Silence Slack alerts for this PO</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => ack(3, 'snoozed 3 days')}>Snooze 3 days</DropdownMenuItem>
        <DropdownMenuItem onClick={() => ack(7, 'snoozed 7 days')}>Snooze 7 days</DropdownMenuItem>
        <DropdownMenuItem onClick={() => ack(14, 'snoozed 14 days')}>Snooze 14 days</DropdownMenuItem>
        <DropdownMenuItem onClick={() => ack(null, 'until ETA changes')}>
          Until the expected date changes
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function PoTracker() {
  const [windowDays, setWindowDays] = useState(300)
  const [windowInput, setWindowInput] = useState('300')
  const { orders, meta, isLoading, isRefreshing, error, refetch, revalidate } = usePoWatch(windowDays)

  const [vendorFilter, setVendorFilter] = useState('all')
  const [shopFilter, setShopFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [hideFullyReceived, setHideFullyReceived] = useState(false)
  const [triageFilter, setTriageFilter] = useState<PoWatchTriage | null>(null)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'severity', dir: 1 })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const toggleSort = (key: SortKey) => {
    setSort((current) => current.key === key
      ? { key, dir: current.dir === 1 ? -1 : 1 }
      : { key, dir: key === 'severity' ? 1 : -1 }) // value columns start descending
  }

  const vendors = useMemo(
    () => [...new Set(orders.map((o) => o.vendor_name))].sort((a, b) => a.localeCompare(b)),
    [orders],
  )
  const shops = useMemo(
    () => [...new Set(orders.map((o) => o.shop_name))].sort((a, b) => a.localeCompare(b)),
    [orders],
  )

  // Rows surviving every filter EXCEPT the triage-card one. KPI counts come from
  // this set, so the cards react to the vendor/shop/status/received filters while
  // clicking one card doesn't zero out the others.
  const baseFiltered = useMemo(() => orders.filter((o) => {
    if (vendorFilter !== 'all' && o.vendor_name !== vendorFilter) return false
    if (shopFilter !== 'all' && o.shop_name !== shopFilter) return false
    if (statusFilter !== 'all' && o.status !== statusFilter) return false
    if (hideFullyReceived && o.received_pct >= 100) return false
    return true
  }), [orders, vendorFilter, shopFilter, statusFilter, hideFullyReceived])

  const counts = useMemo(() => {
    const result = {
      critical: 0, very_late: 0, late: 0, due_soon: 0, no_eta: 0, on_track: 0,
      alertable: 0, acknowledged: 0, expected_faster_than_median: 0,
    }
    for (const o of baseFiltered) {
      result[o.triage] += 1
      if (o.alertable) result.alertable += 1
      if (o.ack?.active) result.acknowledged += 1
      if (o.flags.includes('expected_faster_than_median')) result.expected_faster_than_median += 1
    }
    return result
  }, [baseFiltered])

  const filtered = useMemo(() => {
    const rows = triageFilter ? baseFiltered.filter((o) => o.triage === triageFilter) : baseFiltered
    return sortOrders(rows, sort.key, sort.dir)
  }, [baseFiltered, triageFilter, sort])

  const applyWindow = () => {
    const value = Number(windowInput)
    if (!Number.isFinite(value) || value < 1 || value > 730) {
      toast.error('Window must be between 1 and 730 days')
      return
    }
    setWindowDays(Math.round(value))
    setExpanded(new Set())
  }

  const toggleRow = (orderId: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      next.has(orderId) ? next.delete(orderId) : next.add(orderId)
      return next
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">PO Tracker</h2>
          <p className="text-sm text-muted-foreground">
            Every placed PO that hasn&apos;t fully arrived, escalated as it passes its expected date.
            {meta && ` ${meta.order_count} open POs ordered since ${meta.ordered_since}.`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 text-sm text-muted-foreground">
            Ordered within
            <Input
              className="h-8 w-20 text-center"
              value={windowInput}
              onChange={(event) => setWindowInput(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && applyWindow()}
              inputMode="numeric"
            />
            days
            <Button size="sm" variant="secondary" className="ml-1 h-8" onClick={applyWindow}>Apply</Button>
          </div>
          <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isRefreshing}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            {isRefreshing ? 'Syncing…' : 'Sync'}
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          <AlertTriangle className="mr-2 inline h-4 w-4" />Failed to load the PO tracker. Retry with Sync.
        </div>
      )}

      {/* Triage KPI cards — click to filter */}
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {TRIAGE_KEYS.map((tier) => {
          const meta_ = TRIAGE_META[tier]
          const active = triageFilter === tier
          return (
            <button
              key={tier}
              onClick={() => setTriageFilter(active ? null : tier)}
              className={`rounded-xl border p-3 text-left transition-colors hover:bg-muted/50 ${meta_.card} ${active ? 'bg-muted ring-2 ring-ring' : 'bg-card'}`}
            >
              <div className="text-xs text-muted-foreground">{meta_.label}</div>
              <div className="text-2xl font-semibold">
                {isLoading ? '—' : counts[tier]}
              </div>
            </button>
          )
        })}
      </div>

      {!isLoading && meta && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Timer className="h-3.5 w-3.5" />
          {counts.alertable} PO(s) are ≥{meta.alert_days_late_threshold}d late with nothing received and unacknowledged (Slack-alertable)
          · {counts.acknowledged} snoozed
          · {counts.expected_faster_than_median} promised faster than the vendor&apos;s median lead time
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={vendorFilter} onValueChange={setVendorFilter}>
          <SelectTrigger className="h-8 w-56"><SelectValue placeholder="Vendor" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All vendors</SelectItem>
            {vendors.map((vendor) => <SelectItem key={vendor} value={vendor}>{vendor}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={shopFilter} onValueChange={setShopFilter}>
          <SelectTrigger className="h-8 w-44"><SelectValue placeholder="Shop" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All shops</SelectItem>
            {shops.map((shop) => <SelectItem key={shop} value={shop}>{shop}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="h-8 w-44"><SelectValue placeholder="Status" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="ordered">Ordered</SelectItem>
            <SelectItem value="receiving">Check-in started</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2 rounded-md border px-2.5 py-1.5">
          <Switch id="hide-received" checked={hideFullyReceived} onCheckedChange={setHideFullyReceived} />
          <Label htmlFor="hide-received" className="cursor-pointer text-xs font-normal text-muted-foreground">
            Hide fully received
          </Label>
        </div>
        {(vendorFilter !== 'all' || shopFilter !== 'all' || statusFilter !== 'all' || hideFullyReceived || triageFilter) && (
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => {
            setVendorFilter('all'); setShopFilter('all'); setStatusFilter('all'); setHideFullyReceived(false); setTriageFilter(null)
          }}>Clear filters ({filtered.length} shown)</Button>
        )}
      </div>

      {/* PO table */}
      <div className="rounded-xl border">
        {isLoading ? (
          <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-8" /><Skeleton className="h-8" /></div>
        ) : filtered.length === 0 ? (
          <div className="p-10 text-center text-sm text-muted-foreground">No purchase orders match the current filters.</div>
        ) : (
          <Table>
            <TableHeader><TableRow className="text-xs">
              <TableHead className="w-8" />
              <SortableHead label="PO" sortKey="order_id" sort={sort} onSort={toggleSort} />
              <SortableHead label="Vendor" sortKey="vendor" sort={sort} onSort={toggleSort} />
              <SortableHead label="Shop" sortKey="shop" sort={sort} onSort={toggleSort} />
              <SortableHead label="Created" sortKey="created" sort={sort} onSort={toggleSort} />
              <SortableHead label="Ordered" sortKey="ordered" sort={sort} onSort={toggleSort} />
              <SortableHead label="Expected" sortKey="expected" sort={sort} onSort={toggleSort} />
              <SortableHead label="Units" sortKey="units" sort={sort} onSort={toggleSort} className="text-right" />
              <SortableHead label="Cost" sortKey="cost" sort={sort} onSort={toggleSort} className="text-right" />
              <SortableHead label="Received" sortKey="received" sort={sort} onSort={toggleSort} className="w-32" />
              <SortableHead label="Lead time" sortKey="lead_time" sort={sort} onSort={toggleSort} />
              <SortableHead label="Status" sortKey="severity" sort={sort} onSort={toggleSort} />
              <TableHead className="w-28" />
            </TableRow></TableHeader>
            <TableBody>
              {filtered.map((order) => {
                const isOpen = expanded.has(order.order_id)
                const triage = TRIAGE_META[order.triage]
                return (
                  <Fragment key={order.order_id}>
                    <TableRow className="cursor-pointer" onClick={() => toggleRow(order.order_id)}>
                      <TableCell className="pr-0">
                        {isOpen ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                      </TableCell>
                      <TableCell>
                        <a
                          href={order.lightspeed_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => event.stopPropagation()}
                          className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                        >
                          #{order.order_id}<ExternalLink className="h-3 w-3" />
                        </a>
                        <div className="text-xs text-muted-foreground">{order.created_by || '—'}</div>
                      </TableCell>
                      <TableCell className="max-w-44 truncate text-sm" title={order.vendor_name}>{order.vendor_name}</TableCell>
                      <TableCell className="text-sm">{order.shop_name}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{fmtDate(order.created_date)}</TableCell>
                      <TableCell className="text-sm">{fmtDate(order.ordered_date)}</TableCell>
                      <TableCell className="text-sm">
                        {fmtDate(order.expected_date ?? order.effective_expected_date)}
                        {order.expected_source === 'implied' && <span className="ml-1 text-xs text-muted-foreground">(implied)</span>}
                        {order.days_late != null && <div className="text-xs font-medium text-red-600 dark:text-red-400">{order.days_late}d late</div>}
                        {order.days_until_expected != null && <div className="text-xs text-muted-foreground">in {order.days_until_expected}d</div>}
                      </TableCell>
                      <TableCell className="text-right text-sm">{order.units_ordered}</TableCell>
                      <TableCell className="text-right text-sm">{money.format(order.cost_ordered)}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Progress value={order.received_pct} className="h-1.5 w-16" />
                          <span className="text-xs text-muted-foreground">{order.received_pct.toFixed(0)}%</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {order.median_lead_time_days != null ? (
                          <>
                            median {order.median_lead_time_days}d
                            {order.days_since_ordered != null && <div>out {order.days_since_ordered}d</div>}
                          </>
                        ) : '—'}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          <Badge className={`${triage.badge} border-transparent`}>{triage.label.split(' (')[0]}</Badge>
                          {order.status === 'receiving' && <Badge variant="outline" className="text-xs">check-in</Badge>}
                          <TooltipProvider delayDuration={200}>
                            {order.flags.map((flag) => FLAG_META[flag] && (
                              <Tooltip key={flag}>
                                <TooltipTrigger asChild>
                                  <Badge variant="secondary" className="cursor-help text-xs">{FLAG_META[flag].label}</Badge>
                                </TooltipTrigger>
                                <TooltipContent><div className="max-w-64 text-xs">{FLAG_META[flag].hint}</div></TooltipContent>
                              </Tooltip>
                            ))}
                          </TooltipProvider>
                        </div>
                      </TableCell>
                      <TableCell onClick={(event) => event.stopPropagation()}>
                        <AckMenu order={order} onDone={revalidate} />
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow>
                        <TableCell colSpan={13} className="bg-muted/30 p-0">
                          <LinesPanel orderId={order.order_id} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
