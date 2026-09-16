'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  apiPost, useChangeFeed, useChangeGroups, useCompetitors, usePriceIntelSummary,
  useTrackedProducts,
} from '@/lib/price-intel/hooks'
import type {
  ChangeDirection, ChangeEvent, ChangeEventType, ChangeGroup,
} from '@/lib/price-intel/types'
import {
  ArrowDownRight,
  ArrowUpDown,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Eye,
  Layers,
  PackageCheck,
  PackageX,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'

const EVENT_META: Record<ChangeEventType, { label: string; icon: typeof Check; tone: string }> = {
  price_drop: { label: 'Price drop', icon: ArrowDownRight, tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  price_increase: { label: 'Price increase', icon: ArrowUpRight, tone: 'bg-rose-50 text-rose-700 border-rose-200' },
  back_in_stock: { label: 'Back in stock', icon: PackageCheck, tone: 'bg-sky-50 text-sky-700 border-sky-200' },
  out_of_stock: { label: 'Out of stock', icon: PackageX, tone: 'bg-slate-100 text-slate-600 border-slate-200' },
  new_match: { label: 'New match', icon: Sparkles, tone: 'bg-violet-50 text-violet-700 border-violet-200' },
  first_observation: { label: 'First observation', icon: Eye, tone: 'bg-slate-100 text-slate-600 border-slate-200' },
  map_violation: { label: 'MAP violation', icon: ShieldAlert, tone: 'bg-amber-50 text-amber-800 border-amber-300' },
  undercut: { label: 'Undercut', icon: ArrowDownRight, tone: 'bg-orange-50 text-orange-700 border-orange-300' },
}

// A grouped row whose variants moved both ways in the window.
const MIXED_META = { label: 'Mixed', icon: ArrowUpDown, tone: 'bg-slate-100 text-slate-600 border-slate-200' }

const DIRECTION_LABEL: Record<ChangeDirection, string> = {
  both: 'Drops & increases',
  drops: 'Drops only',
  increases: 'Increases only',
}

const fmtPrice = (v: number | null) => (v == null ? '—' : `$${v.toFixed(2)}`)

// "$349.00–$389.00" across a group's variants, or the single value.
const fmtPriceRange = (lo: number | null, hi: number | null) => {
  if (lo == null && hi == null) return '—'
  if (lo == null || hi == null || Math.abs(lo - hi) < 0.005) return fmtPrice(lo ?? hi)
  return `${fmtPrice(lo)}–${fmtPrice(hi)}`
}

const fmtPct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`

const fmtPctRange = (lo: number | null, hi: number | null): string | null => {
  if (lo == null && hi == null) return null
  if (lo == null || hi == null || Math.abs(lo - hi) < 0.05) return fmtPct((hi ?? lo) as number)
  if ((lo < 0) === (hi < 0)) {
    const [a, b] = [Math.abs(lo), Math.abs(hi)].sort((x, y) => x - y)
    return `${lo < 0 ? '-' : '+'}${a.toFixed(1)}–${b.toFixed(1)}%`
  }
  return `${fmtPct(lo)} to ${fmtPct(hi)}`
}

const pctTone = (v: number | null) => (v != null && v < 0 ? 'text-emerald-600' : 'text-rose-600')

// Our price vs this competitor's (new) price — the relation to our catalog.
function PositionPill({ ours, theirs }: { ours: number | null; theirs: number | null }) {
  if (ours == null || theirs == null) return null
  const delta = ours - theirs
  if (Math.abs(delta) <= 0.01) {
    return <Badge variant="outline" className="border-sky-200 bg-sky-50 px-1.5 py-0 text-[11px] text-sky-700">at parity</Badge>
  }
  if (delta < 0) {
    return <Badge variant="outline" className="border-emerald-200 bg-emerald-50 px-1.5 py-0 text-[11px] text-emerald-700">{fmtPrice(Math.abs(delta))} cheaper</Badge>
  }
  return <Badge variant="outline" className="border-rose-200 bg-rose-50 px-1.5 py-0 text-[11px] text-rose-700">{fmtPrice(delta)} pricier</Badge>
}

function EventRow({ event, ourPrice, onAck }: {
  event: ChangeEvent; ourPrice: number | null; onAck: (id: string) => void
}) {
  const meta = EVENT_META[event.event_type] ?? EVENT_META.first_observation
  const Icon = meta.icon
  return (
    <div className={cn(
      'flex items-center gap-3 rounded-lg border px-3 py-2',
      event.event_type === 'map_violation' && !event.acknowledged
        && 'border-amber-300 bg-amber-50 ring-1 ring-amber-200',
      event.acknowledged ? 'opacity-60' : event.event_type !== 'map_violation' && 'bg-card'
    )}>
      <Badge variant="outline" className={cn('shrink-0 gap-1', meta.tone)}>
        <Icon className="h-3 w-3" />
        {meta.label}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {event.item_title || 'Unmatched product'}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {event.competitor_name || 'Unknown source'}
          {' · '}
          {new Date(event.occurred_at).toLocaleString()}
        </p>
      </div>
      <div className="shrink-0 text-right text-sm tabular-nums">
        <div>
          <span className="mr-1 text-[11px] text-muted-foreground">them</span>
          {event.old_price != null && event.old_price !== event.new_price && (
            <span className="text-muted-foreground line-through">{fmtPrice(event.old_price)}</span>
          )}{' '}
          <span className="font-semibold">{fmtPrice(event.new_price)}</span>
          {event.pct_change != null && (
            <span className={cn('ml-1 text-xs', pctTone(event.pct_change))}>
              {fmtPct(event.pct_change)}
            </span>
          )}
        </div>
        {event.item_id && (
          <div className="flex items-center justify-end gap-1.5">
            <span className="text-[11px] text-muted-foreground">us</span>
            <span className="font-medium">{fmtPrice(ourPrice)}</span>
            <PositionPill ours={ourPrice} theirs={event.new_price} />
          </div>
        )}
      </div>
      {event.url && (
        <a href={event.url} target="_blank" rel="noopener noreferrer"
           className="shrink-0 text-muted-foreground hover:text-foreground" title="Open competitor page">
          <ExternalLink className="h-4 w-4" />
        </a>
      )}
      {!event.acknowledged && (
        <Button variant="ghost" size="sm" className="shrink-0" title="Mark as read"
                onClick={() => onAck(event.event_id)}>
          <Check className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}

// One product (matrix or item) at one store — the same rollup the Slack
// price-changes message posts. Expands to the per-variant events behind it.
function GroupRow({ group, expanded, onToggle, onAck }: {
  group: ChangeGroup; expanded: boolean; onToggle: () => void; onAck: (ids: string[]) => void
}) {
  const meta = group.direction === 'drop' ? EVENT_META.price_drop
    : group.direction === 'increase' ? EVENT_META.price_increase
    : MIXED_META
  const Icon = meta.icon
  const variants = group.product_kind !== 'matrix' ? null
    : group.variants_total == null || group.variants_changed >= group.variants_total
      ? `${group.variants_changed} variant${group.variants_changed === 1 ? '' : 's'}`
      : `${group.variants_changed} of ${group.variants_total} variants`
  const singleNew = group.new_price_min != null && group.new_price_max != null
    && Math.abs(group.new_price_min - group.new_price_max) < 0.005
  const singleOurs = group.our_price_min != null && group.our_price_max != null
    && Math.abs(group.our_price_min - group.our_price_max) < 0.005
  const pct = fmtPctRange(group.pct_min, group.pct_max)
  const hasOld = group.old_price_min != null
    && (group.old_price_min !== group.new_price_min || group.old_price_max !== group.new_price_max)
  return (
    <div className={cn('rounded-lg border', group.acknowledged ? 'opacity-60' : 'bg-card')}>
      <div className="flex items-center gap-3 px-3 py-2">
        <button onClick={onToggle} className="shrink-0 text-muted-foreground hover:text-foreground"
                title={expanded ? 'Hide variants' : 'Show variants'}>
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <Badge variant="outline" className={cn('shrink-0 gap-1', meta.tone)}>
          <Icon className="h-3 w-3" />
          {meta.label}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            {group.product_kind === 'matrix' && (
              <Layers className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="truncate">{group.title}</span>
            {variants && (
              <Badge variant="outline" className="shrink-0 px-1.5 py-0 text-[11px] font-normal">
                {variants}
              </Badge>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {group.competitor_name || 'Unknown source'}
            {' · '}
            {new Date(group.last_occurred_at).toLocaleString()}
            {group.events.length > 1 && ` · ${group.events.length} changes`}
            {group.unread_count > 0 && group.unread_count < group.events.length
              && ` (${group.unread_count} unread)`}
          </p>
        </div>
        <div className="shrink-0 text-right text-sm tabular-nums">
          <div>
            <span className="mr-1 text-[11px] text-muted-foreground">them</span>
            {hasOld && (
              <span className="text-muted-foreground line-through">
                {fmtPriceRange(group.old_price_min, group.old_price_max)}
              </span>
            )}{' '}
            <span className="font-semibold">{fmtPriceRange(group.new_price_min, group.new_price_max)}</span>
            {pct && <span className={cn('ml-1 text-xs', pctTone(group.pct_max))}>{pct}</span>}
          </div>
          {group.our_price_min != null && (
            <div className="flex items-center justify-end gap-1.5">
              <span className="text-[11px] text-muted-foreground">us</span>
              <span className="font-medium">{fmtPriceRange(group.our_price_min, group.our_price_max)}</span>
              {singleNew && singleOurs && (
                <PositionPill ours={group.our_price_min} theirs={group.new_price_min} />
              )}
            </div>
          )}
        </div>
        {group.url && (
          <a href={group.url} target="_blank" rel="noopener noreferrer"
             className="shrink-0 text-muted-foreground hover:text-foreground" title="Open competitor page">
            <ExternalLink className="h-4 w-4" />
          </a>
        )}
        {!group.acknowledged && (
          <Button variant="ghost" size="sm" className="shrink-0"
                  title={group.events.length > 1 ? 'Mark all variants as read' : 'Mark as read'}
                  onClick={() => onAck(group.event_ids)}>
            <Check className="h-4 w-4" />
          </Button>
        )}
      </div>
      {expanded && (
        <div className="space-y-1 border-t px-3 py-2">
          {group.events.map((ev) => (
            <div key={ev.event_id}
                 className={cn('flex items-center gap-3 text-xs', ev.acknowledged && 'opacity-60')}>
              <span className="w-28 shrink-0 truncate" title={ev.variant ?? undefined}>
                {ev.variant ?? (group.product_kind === 'matrix' ? '—' : group.title)}
              </span>
              <span className="truncate text-muted-foreground">
                {new Date(ev.occurred_at).toLocaleString()}
              </span>
              <span className="ml-auto shrink-0 tabular-nums">
                {ev.old_price != null && ev.old_price !== ev.new_price && (
                  <span className="text-muted-foreground line-through">{fmtPrice(ev.old_price)}</span>
                )}{' '}
                <span className="font-medium">{fmtPrice(ev.new_price)}</span>
                {ev.pct_change != null && (
                  <span className={cn('ml-1', pctTone(ev.pct_change))}>{fmtPct(ev.pct_change)}</span>
                )}
              </span>
              {ev.url && ev.url !== group.url && (
                <a href={ev.url} target="_blank" rel="noopener noreferrer"
                   className="shrink-0 text-muted-foreground hover:text-foreground" title="Open listing">
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function ChangeFeed() {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [competitorId, setCompetitorId] = useState<string>('all')
  const [brand, setBrand] = useState<string>('all')
  const [activeTypes, setActiveTypes] = useState<Set<ChangeEventType>>(new Set())
  const [minPct, setMinPct] = useState<string>('')
  // Grouped = price changes rolled up per product × store (what Slack posts).
  const [grouped, setGrouped] = useState(false)
  const [direction, setDirection] = useState<ChangeDirection>('both')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const { competitors } = useCompetitors()
  const { products } = useTrackedProducts()
  const sharedFilters = {
    days: 14,
    unacknowledgedOnly: unreadOnly,
    competitorId: competitorId === 'all' ? null : competitorId,
    brand: brand === 'all' ? null : brand,
    minPct: minPct.trim() === '' ? null : Number(minPct) || null,
  }
  const { events, isLoading, mutate } = useChangeFeed({
    ...sharedFilters,
    eventTypes: activeTypes.size > 0 ? [...activeTypes] : undefined,
  })
  const {
    groups, truncated, isLoading: groupsLoading, mutate: mutateGroups,
  } = useChangeGroups({ ...sharedFilters, direction }, grouped)
  const { mutate: mutateSummary } = usePriceIntelSummary()

  const brands = useMemo(
    () => [...new Set(products.map((p) => p.brand).filter((b): b is string => !!b))].sort(),
    [products]
  )

  // our current retail per item, so each change reads against our catalog
  const ourPriceById = useMemo(
    () => new Map(products.map((p) => [p.item_id, p.current_retail])),
    [products]
  )

  const toggleType = (type: ChangeEventType) => {
    setActiveTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const toggleExpanded = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const ack = async (ids: string[]) => {
    try {
      await apiPost('/api/price-intel/changes/ack', { event_ids: ids })
      await Promise.all([mutate(), mutateGroups(), mutateSummary()])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to acknowledge')
    }
  }

  const unreadIds = grouped
    ? groups.filter((g) => g.unread_count > 0).flatMap((g) => g.event_ids)
    : events.filter((e) => !e.acknowledged).map((e) => e.event_id)
  const loading = grouped ? groupsLoading : isLoading

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Change feed</h3>
            <span className="text-xs text-muted-foreground">last 14 days</span>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch checked={grouped} onCheckedChange={setGrouped} />
              Group by product
            </label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch checked={unreadOnly} onCheckedChange={setUnreadOnly} />
              Unread only
            </label>
            {unreadIds.length > 0 && (
              <Button variant="outline" size="sm" onClick={() => ack(unreadIds)}>
                <CheckCheck className="h-4 w-4" />
                Mark all read
              </Button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={competitorId} onValueChange={setCompetitorId}>
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue placeholder="Competitor" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All competitors</SelectItem>
              {competitors.filter((c) => c.enabled).map((c) => (
                <SelectItem key={c.competitor_id} value={c.competitor_id}>{c.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={brand} onValueChange={setBrand}>
            <SelectTrigger className="h-8 w-40 text-xs">
              <SelectValue placeholder="Brand" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All brands</SelectItem>
              {brands.map((b) => (
                <SelectItem key={b} value={b}>{b}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <span>min</span>
            <Input value={minPct} onChange={(e) => setMinPct(e.target.value)}
                   placeholder="0" inputMode="decimal" className="h-8 w-14 text-xs" />
            <span>% change</span>
          </div>
          {grouped ? (
            <>
              <Select value={direction} onValueChange={(v) => setDirection(v as ChangeDirection)}>
                <SelectTrigger className="h-8 w-40 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(DIRECTION_LABEL) as ChangeDirection[]).map((d) => (
                    <SelectItem key={d} value={d}>{DIRECTION_LABEL[d]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">
                Price changes only, one row per product × store — the same rollup Slack posts.
              </span>
            </>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              {(Object.keys(EVENT_META) as ChangeEventType[]).map((type) => {
                const meta = EVENT_META[type]
                const active = activeTypes.has(type)
                return (
                  <button key={type} onClick={() => toggleType(type)}
                          title={active ? 'Click to remove filter' : `Only show ${meta.label.toLowerCase()}`}>
                    <Badge variant="outline"
                           className={cn('cursor-pointer', active ? meta.tone : 'text-muted-foreground opacity-60')}>
                      {meta.label}
                    </Badge>
                  </button>
                )
              })}
            </div>
          )}
        </div>
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
          </div>
        ) : grouped ? (
          groups.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No price changes match — they appear here after a scrape detects a competitor price move.
            </p>
          ) : (
            <div className="space-y-2">
              {truncated && (
                <p className="text-xs text-muted-foreground">
                  Showing groups from the most recent 3,000 changes — narrow the filters to see everything.
                </p>
              )}
              {groups.map((group) => (
                <GroupRow key={group.group_key} group={group}
                          expanded={expanded.has(group.group_key)}
                          onToggle={() => toggleExpanded(group.group_key)}
                          onAck={(ids) => ack(ids)} />
              ))}
            </div>
          )
        ) : events.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No changes yet — they appear here after a scrape detects a price or stock move.
          </p>
        ) : (
          <div className="space-y-2">
            {events.map((event) => (
              <EventRow key={event.event_id} event={event}
                        ourPrice={event.item_id ? ourPriceById.get(event.item_id) ?? null : null}
                        onAck={(id) => ack([id])} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
