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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
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
  const { orders, summary, meta, isLoading, isRefreshing, error, refetch, revalidate } = usePoWatch(windowDays)

  const [vendorFilter, setVendorFilter] = useState('all')
  const [shopFilter, setShopFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [triageFilter, setTriageFilter] = useState<PoWatchTriage | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const vendors = useMemo(
    () => [...new Set(orders.map((o) => o.vendor_name))].sort((a, b) => a.localeCompare(b)),
    [orders],
  )
  const shops = useMemo(
    () => [...new Set(orders.map((o) => o.shop_name))].sort((a, b) => a.localeCompare(b)),
    [orders],
  )

  const filtered = useMemo(() => orders.filter((o) => {
    if (vendorFilter !== 'all' && o.vendor_name !== vendorFilter) return false
    if (shopFilter !== 'all' && o.shop_name !== shopFilter) return false
    if (statusFilter !== 'all' && o.status !== statusFilter) return false
    if (triageFilter && o.triage !== triageFilter) return false
    return true
  }), [orders, vendorFilter, shopFilter, statusFilter, triageFilter])

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
                {summary ? summary[tier] : '—'}
              </div>
            </button>
          )
        })}
      </div>

      {summary && meta && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Timer className="h-3.5 w-3.5" />
          {summary.alertable} PO(s) are ≥{meta.alert_days_late_threshold}d late and unacknowledged (Slack-alertable)
          · {summary.acknowledged} snoozed
          · {summary.expected_faster_than_median} promised faster than the vendor&apos;s median lead time
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
        <ToggleGroup type="single" value={statusFilter} onValueChange={(value) => setStatusFilter(value || 'all')}>
          <ToggleGroupItem value="all" className="h-8 text-xs">All</ToggleGroupItem>
          <ToggleGroupItem value="ordered" className="h-8 text-xs">Ordered</ToggleGroupItem>
          <ToggleGroupItem value="receiving" className="h-8 text-xs">Check-in started</ToggleGroupItem>
        </ToggleGroup>
        {(vendorFilter !== 'all' || shopFilter !== 'all' || statusFilter !== 'all' || triageFilter) && (
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => {
            setVendorFilter('all'); setShopFilter('all'); setStatusFilter('all'); setTriageFilter(null)
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
              <TableHead>PO</TableHead>
              <TableHead>Vendor</TableHead>
              <TableHead>Shop</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Ordered</TableHead>
              <TableHead>Expected</TableHead>
              <TableHead className="text-right">Units</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="w-32">Received</TableHead>
              <TableHead>Lead time</TableHead>
              <TableHead>Status</TableHead>
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
