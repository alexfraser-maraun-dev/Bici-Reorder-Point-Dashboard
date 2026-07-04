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
  apiPost, useChangeFeed, useCompetitors, usePriceIntelSummary, useTrackedProducts,
} from '@/lib/price-intel/hooks'
import type { ChangeEvent, ChangeEventType } from '@/lib/price-intel/types'
import {
  ArrowDownRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ExternalLink,
  Eye,
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
}

const fmtPrice = (v: number | null) => (v == null ? '—' : `$${v.toFixed(2)}`)

function EventRow({ event, onAck }: { event: ChangeEvent; onAck: (id: string) => void }) {
  const meta = EVENT_META[event.event_type] ?? EVENT_META.first_observation
  const Icon = meta.icon
  return (
    <div className={cn(
      'flex items-center gap-3 rounded-lg border px-3 py-2',
      event.acknowledged ? 'opacity-60' : 'bg-card'
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
        {event.old_price != null && (
          <span className="text-muted-foreground line-through">{fmtPrice(event.old_price)}</span>
        )}{' '}
        <span className="font-semibold">{fmtPrice(event.new_price)}</span>
        {event.pct_change != null && (
          <span className={cn(
            'ml-1 text-xs',
            event.pct_change < 0 ? 'text-emerald-600' : 'text-rose-600'
          )}>
            {event.pct_change > 0 ? '+' : ''}{event.pct_change.toFixed(1)}%
          </span>
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

export function ChangeFeed() {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [competitorId, setCompetitorId] = useState<string>('all')
  const [brand, setBrand] = useState<string>('all')
  const [activeTypes, setActiveTypes] = useState<Set<ChangeEventType>>(new Set())
  const [minPct, setMinPct] = useState<string>('')
  const { competitors } = useCompetitors()
  const { products } = useTrackedProducts()
  const { events, isLoading, mutate } = useChangeFeed({
    days: 14,
    unacknowledgedOnly: unreadOnly,
    competitorId: competitorId === 'all' ? null : competitorId,
    brand: brand === 'all' ? null : brand,
    eventTypes: activeTypes.size > 0 ? [...activeTypes] : undefined,
    minPct: minPct.trim() === '' ? null : Number(minPct) || null,
  })
  const { mutate: mutateSummary } = usePriceIntelSummary()

  const brands = useMemo(
    () => [...new Set(products.map((p) => p.brand).filter((b): b is string => !!b))].sort(),
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

  const ack = async (ids: string[]) => {
    try {
      await apiPost('/api/price-intel/changes/ack', { event_ids: ids })
      await Promise.all([mutate(), mutateSummary()])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to acknowledge')
    }
  }

  const unread = events.filter((e) => !e.acknowledged)

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
              <Switch checked={unreadOnly} onCheckedChange={setUnreadOnly} />
              Unread only
            </label>
            {unread.length > 0 && (
              <Button variant="outline" size="sm" onClick={() => ack(unread.map((e) => e.event_id))}>
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
        </div>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
          </div>
        ) : events.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No changes yet — they appear here after a scrape detects a price or stock move.
          </p>
        ) : (
          <div className="space-y-2">
            {events.map((event) => (
              <EventRow key={event.event_id} event={event} onAck={(id) => ack([id])} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
