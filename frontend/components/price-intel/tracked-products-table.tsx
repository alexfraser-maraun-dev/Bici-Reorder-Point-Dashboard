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
import { apiPost, searchItems, useTrackedProducts } from '@/lib/price-intel/hooks'
import type { ItemSearchResult, TrackedProduct } from '@/lib/price-intel/types'
import { PricePushDialog } from './price-push-dialog'
import {
  DollarSign, EyeOff, Pin, PinOff, RefreshCw, Search, ShieldCheck, Undo2,
} from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

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
  const [pinQuery, setPinQuery] = useState('')
  const [pinResults, setPinResults] = useState<ItemSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [reseeding, setReseeding] = useState(false)

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

  const runPinSearch = async () => {
    if (pinQuery.trim().length < 2) return
    setSearching(true)
    try {
      setPinResults(await searchItems(pinQuery.trim()))
    } finally {
      setSearching(false)
    }
  }

  const pinItem = async (item: ItemSearchResult) => {
    try {
      await apiPost('/api/price-intel/tracked/pin', { item_id: item.item_id })
      toast.success(`Pinned ${item.title}`)
      setPinResults([])
      setPinQuery('')
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to pin item')
    }
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
                top-revenue auto-seed + pinned items
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

          <div className="flex flex-wrap items-center gap-2">
            <Input placeholder="Pin another item (search name / SKU / id)…" value={pinQuery}
                   onChange={(e) => setPinQuery(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && runPinSearch()}
                   className="w-80" />
            <Button variant="outline" size="sm" onClick={runPinSearch} disabled={searching}>
              <Search className="h-4 w-4" /> Search
            </Button>
            {pinResults.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setPinResults([])}>Clear</Button>
            )}
          </div>
          {pinResults.length > 0 && (
            <div className="space-y-1 rounded-md border p-2">
              {pinResults.map((r) => (
                <button key={r.item_id} onClick={() => pinItem(r)}
                        className="flex w-full items-center justify-between gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-muted">
                  <span className="truncate">
                    <span className="font-medium">{r.title}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{r.brand} · {r.manufacturer_sku ?? r.item_id}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    {fmt(r.current_retail)} <Pin className="h-3 w-3" />
                  </span>
                </button>
              ))}
            </div>
          )}

          {isLoading ? (
            <Skeleton className="h-64 rounded-lg" />
          ) : visible.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Nothing tracked yet — hit “Re-seed list” to pull the top-revenue SKUs.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">#</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead className="text-right">Our price</TableHead>
                  <TableHead className="text-right">Market min</TableHead>
                  <TableHead>Position</TableHead>
                  <TableHead className="text-right">Stores</TableHead>
                  <TableHead>Match</TableHead>
                  <TableHead className="w-32 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((p) => (
                  <TableRow key={p.item_id} className={cn(p.excluded && 'opacity-50')}>
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
                      </div>
                      <p className="truncate text-xs text-muted-foreground">
                        {p.brand} · {p.sku ?? p.item_id}
                        {p.upc_normalized ? '' : ' · no UPC'}
                      </p>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(p.current_retail)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(p.market_min_in_stock)}</TableCell>
                    <TableCell><PositionBadge product={p} /></TableCell>
                    <TableCell className="text-right tabular-nums">{p.competitor_count ?? 0}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {p.upc_normalized ? 'UPC' : 'brand/SKU/title'}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-0.5">
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
                  </TableRow>
                ))}
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
    </div>
  )
}
