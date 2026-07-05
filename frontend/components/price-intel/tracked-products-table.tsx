'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import {
  apiPost, useCompetitors, useItemCompetitorPrices, useTrackedProducts,
} from '@/lib/price-intel/hooks'
import type { ItemSearchResult, TrackedProduct } from '@/lib/price-intel/types'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { PricePushDialog } from './price-push-dialog'
import { ItemSearchPicker } from './item-search-picker'
import {
  ChevronDown, ChevronRight, DollarSign, ExternalLink, EyeOff, Link2, Pin,
  PinOff, RefreshCw, Search, ShieldCheck, Undo2,
} from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const MATCH_METHOD_LABEL: Record<string, string> = {
  link: 'confirmed link',
  gtin: 'UPC',
  brand_sku: 'brand+SKU',
  fuzzy_title: 'title match',
  manual_url: 'tracked URL',
}

// Expanded-row panel: latest price per store for this item (and its matrix
// siblings — sizes share MSRP). Lazy: only fetches while expanded.
function CompetitorBreakdown({ itemId }: { itemId: string }) {
  const { prices, isLoading } = useItemCompetitorPrices(itemId)
  if (isLoading) return <Skeleton className="h-16 rounded-md" />
  if (prices.length === 0) {
    return (
      <p className="py-2 text-xs text-muted-foreground">
        No competitor observations in the last 45 days.
      </p>
    )
  }
  return (
    <div className="space-y-1 py-1">
      {prices.map((p, i) => (
        <div key={`${p.competitor_id ?? p.url ?? i}`}
             className="flex items-center gap-3 rounded-md border bg-muted/30 px-3 py-1.5 text-sm">
          <span className="w-40 shrink-0 truncate font-medium">
            {p.competitor_name ?? 'Unknown store'}
          </span>
          <span className="tabular-nums font-semibold">{fmt(p.price)}</span>
          {p.compare_at_price != null && p.compare_at_price > (p.price ?? 0) && (
            <span className="text-xs text-muted-foreground line-through tabular-nums">
              {fmt(p.compare_at_price)}
            </span>
          )}
          <Badge variant="outline" className={cn(
            'px-1.5 py-0 text-[11px]',
            p.in_stock
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-slate-200 bg-slate-50 text-slate-500'
          )}>
            {p.in_stock == null ? 'stock unknown' : p.in_stock ? 'in stock' : 'out of stock'}
          </Badge>
          {p.match_method && (
            <Badge variant="outline" className="px-1.5 py-0 text-[11px] text-muted-foreground">
              {MATCH_METHOD_LABEL[p.match_method] ?? p.match_method}
            </Badge>
          )}
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {p.competitor_title}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {new Date(p.observed_at).toLocaleDateString()}
          </span>
          {p.url && (
            <a href={p.url} target="_blank" rel="noopener noreferrer"
               className="shrink-0 text-muted-foreground hover:text-foreground"
               title="Open competitor page">
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

function PositionBadge({ product }: { product: TrackedProduct }) {
  const ours = product.current_retail
  const market = product.market_min_in_stock
  if (ours == null || market == null) {
    return <span className="text-xs text-muted-foreground">no market data</span>
  }
  const delta = ours - market
  if (Math.abs(delta) <= 0.01) {
    return <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">at parity</Badge>
  }
  if (delta < 0) {
    return (
      <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
        {fmt(Math.abs(delta))} cheaper
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="border-rose-200 bg-rose-50 text-rose-700">
      {fmt(delta)} pricier
    </Badge>
  )
}

export function TrackedProductsTable() {
  const { products, isLoading, mutate } = useTrackedProducts()
  const [filter, setFilter] = useState('')
  const [showExcluded, setShowExcluded] = useState(false)
  const [pushTarget, setPushTarget] = useState<TrackedProduct | null>(null)
  const [reseeding, setReseeding] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const { competitors } = useCompetitors()
  const [overrideTarget, setOverrideTarget] = useState<TrackedProduct | null>(null)
  const [overrideUrl, setOverrideUrl] = useState('')
  const [overrideCompetitor, setOverrideCompetitor] = useState<string>('none')
  const [savingOverride, setSavingOverride] = useState(false)

  const saveOverride = async () => {
    if (!overrideTarget) return
    const url = overrideUrl.trim()
    if (!/^https?:\/\//.test(url)) {
      toast.error('Enter a full product URL (https://…)')
      return
    }
    setSavingOverride(true)
    try {
      await apiPost('/api/price-intel/urls', {
        url,
        item_id: overrideTarget.item_id,
        competitor_id: overrideCompetitor === 'none' ? null : overrideCompetitor,
        label: overrideTarget.title,
      })
      toast.success('Match locked in — this URL is now the source of truth for this store')
      setOverrideTarget(null)
      setOverrideUrl('')
      setOverrideCompetitor('none')
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save match')
    } finally {
      setSavingOverride(false)
    }
  }

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return products
      .filter((p) => (showExcluded ? true : !p.excluded))
      .filter((p) =>
        !q ||
        (p.title ?? '').toLowerCase().includes(q) ||
        (p.brand ?? '').toLowerCase().includes(q) ||
        (p.sku ?? '').toLowerCase().includes(q)
      )
  }, [products, filter, showExcluded])

  const patch = async (itemId: string, fields: Record<string, unknown>, label: string) => {
    try {
      await apiPost(`/api/price-intel/tracked/${itemId}`, fields, 'PUT')
      toast.success(label)
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed')
    }
  }

  const pinItem = async (item: ItemSearchResult) => {
    try {
      await apiPost('/api/price-intel/tracked/pin', { item_id: item.item_id })
      toast.success(`Pinned ${item.title}`)
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to pin item')
    }
  }

  const pinMatrix = async (matrixId: string, description: string | null) => {
    try {
      const res = await apiPost('/api/price-intel/tracked/pin-matrix', { item_matrix_id: matrixId })
      toast.success(`Pinned ${res.affected ?? 'all'} variants of ${description ?? 'matrix'}`)
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to pin variants')
    }
  }

  const toggleExpanded = (itemId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }

  const reseed = async () => {
    setReseeding(true)
    try {
      await apiPost('/api/price-intel/tracked/seed')
      toast.success('Re-seeding from top-revenue SKUs — refresh in a minute')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start re-seed')
    } finally {
      setReseeding(false)
    }
  }

  const setMapPrice = async (product: TrackedProduct) => {
    const raw = window.prompt(
      `MAP price for ${product.title} (blank to clear):`,
      product.map_price != null ? String(product.map_price) : ''
    )
    if (raw === null) return
    const value = raw.trim() === '' ? null : parseFloat(raw)
    if (value !== null && !Number.isFinite(value)) {
      toast.error('Enter a number')
      return
    }
    await patch(product.item_id, { map_price: value }, 'MAP price updated')
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold">Tracked products</h3>
              <span className="text-xs text-muted-foreground">
                items tagged &lsquo;track&rsquo; in Lightspeed + pinned items
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input placeholder="Filter…" value={filter}
                       onChange={(e) => setFilter(e.target.value)} className="w-52 pl-8" />
              </div>
              <Button variant="outline" size="sm" onClick={() => setShowExcluded((v) => !v)}>
                <EyeOff className="h-4 w-4" />
                {showExcluded ? 'Hide excluded' : 'Show excluded'}
              </Button>
              <Button variant="outline" size="sm" onClick={reseed} disabled={reseeding}>
                <RefreshCw className={cn('h-4 w-4', reseeding && 'animate-spin')} />
                Re-seed list
              </Button>
            </div>
          </div>

          <ItemSearchPicker
            actionLabel="Pin"
            placeholder="Pin another item (search name / SKU / id)…"
            onSelect={pinItem}
            onSelectMatrix={pinMatrix}
          />

          {isLoading ? (
            <Skeleton className="h-64 rounded-lg" />
          ) : visible.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Nothing tracked yet — tag items &lsquo;track&rsquo; in Lightspeed, then hit “Re-seed list”.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-6" />
                  <TableHead className="w-8">#</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Our price</TableHead>
                  <TableHead className="text-right">Market min</TableHead>
                  <TableHead>Position</TableHead>
                  <TableHead className="text-right">Stores</TableHead>
                  <TableHead className="w-32 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((p) => {
                  const isOpen = expanded.has(p.item_id)
                  const attributes = [p.attribute_1, p.attribute_2, p.attribute_3]
                    .filter((a): a is string => !!a && a.trim() !== '')
                  return [
                    <TableRow key={p.item_id} className={cn(p.excluded && 'opacity-50')}>
                      <TableCell className="pr-0">
                        <button onClick={() => toggleExpanded(p.item_id)}
                                title="Show per-competitor prices"
                                className="text-muted-foreground hover:text-foreground">
                          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">
                        {p.revenue_rank ?? '—'}
                      </TableCell>
                      <TableCell className="max-w-72">
                        <div className="flex items-center gap-1.5">
                          {p.pinned && <Pin className="h-3 w-3 shrink-0 text-sky-600" />}
                          {p.is_map && (
                            <span title={p.map_price != null ? `MAP ${fmt(p.map_price)}` : 'MAP-tagged (no price set)'}>
                              <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-violet-600" />
                            </span>
                          )}
                          <span className="truncate font-medium">{p.title}</span>
                          {attributes.map((a) => (
                            <Badge key={a} variant="outline" className="shrink-0 px-1.5 py-0 text-[11px]">
                              {a}
                            </Badge>
                          ))}
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          {p.brand} · {p.sku ?? p.item_id}
                          {p.upc_normalized ? ' · UPC' : ' · no UPC'}
                        </p>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{fmt(p.current_retail)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmt(p.market_min_in_stock)}</TableCell>
                      <TableCell><PositionBadge product={p} /></TableCell>
                      <TableCell className="text-right tabular-nums">{p.competitor_count ?? 0}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-0.5">
                          <Button variant="ghost" size="sm"
                                  title="Link the competitor URL that truly matches this item"
                                  onClick={() => setOverrideTarget(p)}>
                            <Link2 className="h-4 w-4 text-muted-foreground" />
                          </Button>
                          <Button variant="ghost" size="sm" title="Push price to Lightspeed"
                                  onClick={() => setPushTarget(p)} disabled={p.excluded}>
                            <DollarSign className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="sm" title="Set MAP price"
                                  onClick={() => setMapPrice(p)}>
                            <ShieldCheck className={cn('h-4 w-4', p.map_price != null ? 'text-violet-600' : 'text-muted-foreground')} />
                          </Button>
                          <Button variant="ghost" size="sm"
                                  title={p.pinned ? 'Unpin' : 'Pin (survives re-seeding)'}
                                  onClick={() => patch(p.item_id, { pinned: !p.pinned }, p.pinned ? 'Unpinned' : 'Pinned')}>
                            {p.pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4 text-muted-foreground" />}
                          </Button>
                          <Button variant="ghost" size="sm"
                                  title={p.excluded ? 'Re-include in matching' : 'Exclude from matching'}
                                  onClick={() => patch(p.item_id, { excluded: !p.excluded }, p.excluded ? 'Re-included' : 'Excluded')}>
                            {p.excluded ? <Undo2 className="h-4 w-4" /> : <EyeOff className="h-4 w-4 text-muted-foreground" />}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>,
                    isOpen ? (
                      <TableRow key={`${p.item_id}-detail`} className="hover:bg-transparent">
                        <TableCell colSpan={8} className="bg-muted/20 py-2 pl-10">
                          <CompetitorBreakdown itemId={p.item_id} />
                        </TableCell>
                      </TableRow>
                    ) : null,
                  ]
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <PricePushDialog
        product={pushTarget}
        open={pushTarget !== null}
        onOpenChange={(open) => !open && setPushTarget(null)}
        onPushed={() => mutate()}
      />

      <Dialog open={overrideTarget !== null}
              onOpenChange={(open) => !open && setOverrideTarget(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Link the true competitor match</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Paste the competitor product page that matches{' '}
            <span className="font-medium text-foreground">{overrideTarget?.title}</span>.
            It becomes the permanent source of truth for that store — any wrong
            auto-matches there are rejected, and future scrapes check this URL directly.
          </p>
          <div className="space-y-2">
            <Input placeholder="https://store.example.com/products/…" value={overrideUrl}
                   onChange={(e) => setOverrideUrl(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && saveOverride()} />
            <div className="flex items-center gap-2">
              <Select value={overrideCompetitor} onValueChange={setOverrideCompetitor}>
                <SelectTrigger className="w-56">
                  <SelectValue placeholder="Competitor (optional)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Detect from URL</SelectItem>
                  {competitors.filter((c) => c.enabled).map((c) => (
                    <SelectItem key={c.competitor_id} value={c.competitor_id}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button size="sm" onClick={saveOverride} disabled={savingOverride}>
                <Link2 className="h-4 w-4" /> Lock in match
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
