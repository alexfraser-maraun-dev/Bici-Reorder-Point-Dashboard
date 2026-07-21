'use client'

import { useEffect, useMemo, useState, type ReactNode } from 'react'
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
  ApiError, apiPost, useCompetitors, useItemCompetitorPrices, useMatrixCoverage,
  useTrackedMatrices, useTrackedProducts,
} from '@/lib/price-intel/hooks'
import type { ItemSearchResult, MatrixCoverage, TrackedMatrix, TrackedProduct,
  VariantCandidate, VariantSelectionRequired } from '@/lib/price-intel/types'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { isMapViolation, mapFloor, itemIdentity, lightspeedItemUrl } from '@/lib/price-intel/format'
import { PricePushDialog } from './price-push-dialog'
import { MatrixPushDialog } from './matrix-push-dialog'
import { ItemSearchPicker } from './item-search-picker'
import { PriceHistoryChart } from './price-history-chart'
import {
  ArrowDown, ArrowUp, ArrowUpDown, Ban, ChevronDown, ChevronRight, DollarSign,
  ExternalLink, EyeOff, Layers, Link2, Pin, PinOff, RefreshCw, Search, ShieldAlert,
  ShieldCheck, Undo2, X,
} from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const MATCH_METHOD_LABEL: Record<string, string> = {
  link: 'confirmed link',
  gtin: 'UPC',
  brand_sku: 'brand+SKU',
  fuzzy_title: 'title match',
  manual_url: 'tracked URL',
  attr_exact: 'color+size',
  attr: 'color+size',
}

// Expanded-row panel: latest exact price per store for this item only.
// Each priced row offers "match": set our price to the competitor's, either for
// this one variant or (via the guarded matrix dialog) the whole matrix.
function CompetitorBreakdown({ product, onRejected, onMatchVariant, onMatchMatrix }: {
  product: TrackedProduct
  onRejected?: () => void
  onMatchVariant: (price: number) => void
  onMatchMatrix: (price: number) => void
}) {
  const itemId = product.item_id
  const { prices, isLoading, mutate } = useItemCompetitorPrices(itemId)

  const reject = async (competitorId: string | null, url: string | null, name: string) => {
    if (!window.confirm(`Reject ${name} as a match for this item? It won't re-match on future scrapes.`)) return
    try {
      await apiPost(`/api/price-intel/tracked/${itemId}/reject-competitor`,
        { competitor_id: competitorId, url })
      toast.success(`Rejected ${name} — it won't re-match`)
      await mutate()
      onRejected?.()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to reject match')
    }
  }

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
          {p.price != null && !product.excluded && (
            <span className="flex shrink-0 items-center gap-1">
              <Button variant="outline" size="sm" className="h-6 px-2 text-[11px]"
                      title={`Push ${fmt(p.price)} to Lightspeed for this variant only`}
                      onClick={() => onMatchVariant(p.price!)}>
                Match variant
              </Button>
              {product.item_matrix_id && (
                <Button variant="outline" size="sm" className="h-6 px-2 text-[11px]"
                        title={`Push ${fmt(p.price)} to every variant of ${product.matrix_description ?? 'this matrix'} (opens a review dialog)`}
                        onClick={() => onMatchMatrix(p.price!)}>
                  Match matrix
                </Button>
              )}
            </span>
          )}
          {p.url && (
            <a href={p.url} target="_blank" rel="noopener noreferrer"
               className="shrink-0 text-muted-foreground hover:text-foreground"
               title="Open competitor page">
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          <button
            onClick={() => reject(p.competitor_id, p.url, p.competitor_name ?? 'this listing')}
            className="shrink-0 text-muted-foreground hover:text-rose-600"
            title="Wrong match? Reject this listing so it won't re-match">
            <Ban className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}

type MarketPosition = 'cheaper' | 'parity' | 'pricier' | 'none'

function marketPosition(product: TrackedProduct): MarketPosition {
  const ours = product.current_retail
  const market = product.market_min_in_stock
  if (ours == null || market == null) return 'none'
  const delta = ours - market
  if (Math.abs(delta) <= 0.01) return 'parity'
  return delta < 0 ? 'cheaper' : 'pricier'
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

// Expanded matrix panel: how many of the matrix's variants each competitor carries
// (has an exact observation for), and how many undercut our retail. Read-only over
// existing per-variant observations — nothing is propagated across siblings.
function MatrixCoveragePanel({ matrixId, variantsTotal }: {
  matrixId: string
  variantsTotal: number
}) {
  const { coverage, isLoading } = useMatrixCoverage(matrixId)
  if (isLoading) return <Skeleton className="h-12 rounded-md" />
  if (coverage.length === 0) {
    return (
      <p className="py-1 text-xs text-muted-foreground">
        No competitor carries any variant of this matrix yet.
      </p>
    )
  }
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">Competitor coverage</p>
      <div className="flex flex-wrap gap-1.5">
        {coverage.map((c: MatrixCoverage) => (
          <div key={c.competitor_key}
               className="flex items-center gap-2 rounded-md border bg-muted/30 px-2.5 py-1 text-xs">
            <span className="font-medium">{c.competitor_name ?? 'Unknown store'}</span>
            <span className="tabular-nums text-muted-foreground">
              carries {c.variants_carried}/{variantsTotal}
            </span>
            {c.variants_undercut > 0 && (
              <Badge variant="outline"
                     className="gap-1 border-rose-200 bg-rose-50 px-1.5 py-0 text-[11px] font-semibold text-rose-700">
                <ShieldAlert className="h-3 w-3" /> undercut on {c.variants_undercut}
              </Badge>
            )}
            <span className="tabular-nums text-muted-foreground">
              {fmt(c.price_min)}{c.price_max != null && c.price_max !== c.price_min ? `–${fmt(c.price_max)}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

type SortKey = 'title' | 'rank' | 'our' | 'market' | 'stores'

export function TrackedProductsTable({ quickFilter, onClearQuickFilter }: {
  quickFilter?: string | null
  onClearQuickFilter?: () => void
} = {}) {
  const { products, isLoading, mutate } = useTrackedProducts()
  const [filter, setFilter] = useState('')
  const [competitorFilter, setCompetitorFilter] = useState<string>('all')
  const [brandFilter, setBrandFilter] = useState<string>('all')
  const [positionFilter, setPositionFilter] = useState<string>('all')
  const [mapFilter, setMapFilter] = useState<'all' | 'tagged' | 'violations'>('all')
  const [sortKey, setSortKey] = useState<SortKey>('title')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [showExcluded, setShowExcluded] = useState(false)

  // A KPI tile click drives the table's filters (e.g. "MAP violations" tile).
  useEffect(() => {
    if (!quickFilter) return
    if (quickFilter === 'map') { setMapFilter('tagged'); setPositionFilter('all') }
    else if (quickFilter === 'violations') { setMapFilter('violations'); setPositionFilter('all') }
    else if (['cheaper', 'parity', 'pricier'].includes(quickFilter)) {
      setPositionFilter(quickFilter); setMapFilter('all')
    }
  }, [quickFilter])
  const [pushTarget, setPushTarget] = useState<TrackedProduct | null>(null)
  // Set when the push was opened from a competitor row ("match variant") so the
  // dialog pre-fills that competitor's price instead of the market min.
  const [pushInitialPrice, setPushInitialPrice] = useState<number | null>(null)
  const [matrixTarget, setMatrixTarget] =
    useState<{ product: TrackedProduct; price: number } | null>(null)
  const [reseeding, setReseeding] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [expandedMatrices, setExpandedMatrices] = useState<Set<string>>(new Set())
  const { competitors } = useCompetitors()
  const { matrices, mutate: mutateMatrices } = useTrackedMatrices()
  const matrixById = useMemo(
    () => new Map<string, TrackedMatrix>(matrices.map((m) => [m.item_matrix_id, m])),
    [matrices]
  )
  const [overrideTarget, setOverrideTarget] = useState<TrackedProduct | null>(null)
  const [overrideUrl, setOverrideUrl] = useState('')
  const [overrideCompetitor, setOverrideCompetitor] = useState<string>('none')
  const [savingOverride, setSavingOverride] = useState(false)
  const [overrideCandidates, setOverrideCandidates] = useState<VariantCandidate[]>([])

  const saveOverride = async (candidate?: VariantCandidate) => {
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
        competitor_sku: candidate?.sku,
        competitor_variant_id: candidate?.variant_id,
        competitor_gtin: candidate?.gtin,
        variant_options: candidate?.variant_options,
      })
      toast.success('Match locked in — fetching the competitor price now')
      setOverrideTarget(null)
      setOverrideUrl('')
      setOverrideCompetitor('none')
      setOverrideCandidates([])
      await mutate()
      // the first observation is fetched in a background task server-side —
      // refresh again once it has landed so price/stores update without a reload
      setTimeout(() => { void mutate() }, 12000)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.detail as VariantSelectionRequired
        if (detail?.code === 'variant_selection_required') {
          setOverrideCandidates(detail.candidates)
          return
        }
      }
      toast.error(e instanceof Error ? e.message : 'Failed to save match')
    } finally {
      setSavingOverride(false)
    }
  }

  const brands = useMemo(
    () => [...new Set(products.map((p) => p.brand).filter((b): b is string => !!b))].sort(),
    [products]
  )

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const dir = sortDir === 'asc' ? 1 : -1
    const num = (v: number | null | undefined) => (v == null ? -Infinity : v)
    const cmp = (a: TrackedProduct, b: TrackedProduct): number => {
      switch (sortKey) {
        case 'title': return (a.title ?? '').localeCompare(b.title ?? '')
        case 'rank': return num(a.revenue_rank) - num(b.revenue_rank)
        case 'our': return num(a.current_retail) - num(b.current_retail)
        case 'market': return num(a.market_min_in_stock) - num(b.market_min_in_stock)
        case 'stores': return num(a.competitor_count) - num(b.competitor_count)
      }
    }
    return products
      .filter((p) => (showExcluded ? true : !p.excluded))
      .filter((p) =>
        !q ||
        (p.title ?? '').toLowerCase().includes(q) ||
        (p.brand ?? '').toLowerCase().includes(q) ||
        (p.sku ?? '').toLowerCase().includes(q)
      )
      .filter((p) => competitorFilter === 'all' || (p.competitor_ids ?? []).includes(competitorFilter))
      .filter((p) => brandFilter === 'all' || p.brand === brandFilter)
      .filter((p) => positionFilter === 'all' || marketPosition(p) === positionFilter)
      .filter((p) => mapFilter === 'all'
        || (mapFilter === 'tagged' && p.is_map)
        || (mapFilter === 'violations' && isMapViolation(p)))
      .sort((a, b) => cmp(a, b) * dir)
  }, [products, filter, showExcluded, competitorFilter, brandFilter, positionFilter,
      mapFilter, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir(key === 'title' ? 'asc' : 'desc') }
  }
  const SortHead = ({ k, label, className }: { k: SortKey; label: string; className?: string }) => (
    <button onClick={() => toggleSort(k)}
            className={cn('inline-flex items-center gap-1 hover:text-foreground', className)}>
      {label}
      {sortKey === k
        ? (sortDir === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)
        : <ArrowUpDown className="h-3 w-3 opacity-40" />}
    </button>
  )

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
      const res = await apiPost('/api/price-intel/tracked/track-matrix', {
        item_matrix_id: matrixId, matrix_description: description,
      })
      toast.success(`Tracking ${res.affected ?? 'all'} variants of ${description ?? 'matrix'} — kept in sync each run`)
      await Promise.all([mutate(), mutateMatrices()])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to track matrix')
    }
  }

  const untrackMatrix = async (matrixId: string, description: string | null) => {
    if (!window.confirm(`Stop tracking ${description ?? 'this matrix'}? Its auto-added variants are archived (pinned ones stay).`)) return
    try {
      await apiPost(`/api/price-intel/tracked/track-matrix/${encodeURIComponent(matrixId)}`, undefined, 'DELETE')
      toast.success(`Stopped tracking ${description ?? 'matrix'}`)
      await Promise.all([mutate(), mutateMatrices()])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to untrack matrix')
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

  const toggleMatrixExpanded = (matrixId: string) => {
    setExpandedMatrices((prev) => {
      const next = new Set(prev)
      if (next.has(matrixId)) next.delete(matrixId)
      else next.add(matrixId)
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

  // One tracked item as a row (+ its lazy detail row when expanded). Reused for
  // standalone items and for the variant children of an expanded matrix group.
  // `box` frames the row as part of an open matrix: 'mid' draws the left/right
  // sides, 'last' also closes the bottom (on the detail row when the variant is
  // itself expanded, else on the main row).
  const renderProductRow = (p: TrackedProduct, indent = false,
                            box: 'mid' | 'last' | null = null) => {
    const isOpen = expanded.has(p.item_id)
    const attributes = [p.attribute_1, p.attribute_2, p.attribute_3]
      .filter((a): a is string => !!a && a.trim() !== '')
    const boxSides = box && 'border-x-2 border-sky-400 dark:border-sky-500'
    return [
      <TableRow key={p.item_id}
                className={cn(
                  indent && 'bg-sky-50/80 hover:bg-sky-100/50 dark:bg-sky-950/20 dark:hover:bg-sky-950/30',
                  boxSides,
                  box === 'last' && !isOpen && 'border-b-2 border-sky-400 dark:border-sky-500',
                  p.excluded && 'opacity-50')}>
        <TableCell className={cn('pr-0', indent && 'pl-8')}>
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
            <a href={lightspeedItemUrl(p.item_id)} target="_blank"
               rel="noopener noreferrer" title="Open in Lightspeed"
               className="truncate font-medium hover:underline">
              {indent && attributes.length > 0 ? attributes.join(' / ') : p.title}
            </a>
            {attributes.map((a) => (
              <Badge key={a} variant="outline" className="shrink-0 px-1.5 py-0 text-[11px]">
                {a}
              </Badge>
            ))}
            {isMapViolation(p) && (
              <Badge variant="outline"
                     title={`Competitor at ${fmt(p.market_min_in_stock)} is below our price ${fmt(mapFloor(p))}`}
                     className="shrink-0 gap-1 border-amber-300 bg-amber-50 px-1.5 py-0 text-[11px] font-semibold text-amber-800">
                <ShieldAlert className="h-3 w-3" /> MAP violation
              </Badge>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {itemIdentity({ brand: p.brand, upc: p.upc_normalized, systemSku: p.system_sku })}
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
                    onClick={() => { setPushInitialPrice(null); setPushTarget(p) }}
                    disabled={p.excluded}>
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
        <TableRow key={`${p.item_id}-detail`}
                  className={cn('hover:bg-transparent', boxSides,
                    box === 'last' && 'border-b-2 border-sky-400 dark:border-sky-500')}>
          <TableCell colSpan={8}
                     className={cn('py-3 pl-10 pr-4',
                       indent ? 'bg-sky-100/50 dark:bg-sky-950/30' : 'bg-muted/20')}>
            <div className="space-y-3">
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  Price history — our price vs. tracked competitors
                </p>
                <PriceHistoryChart itemId={p.item_id} />
              </div>
              <CompetitorBreakdown
                product={p}
                onRejected={() => mutate()}
                onMatchVariant={(price) => {
                  setPushInitialPrice(price)
                  setPushTarget(p)
                }}
                onMatchMatrix={(price) => setMatrixTarget({ product: p, price })}
              />
            </div>
          </TableCell>
        </TableRow>
      ) : null,
    ]
  }

  // A matrix rendered as one expandable parent row (rollup) over its variant rows.
  // Rollup numbers come from the backend matrix aggregate when present (authoritative,
  // whole-matrix); children are the filtered variants currently visible.
  const renderMatrixGroup = (matrixId: string, variants: TrackedProduct[]) => {
    const roll = matrixById.get(matrixId)
    const first = variants[0]
    const isOpen = expandedMatrices.has(matrixId)
    const retails = variants.map((v) => v.current_retail).filter((x): x is number => x != null)
    const mins = variants.map((v) => v.market_min_in_stock).filter((x): x is number => x != null)
    const localComps = new Set<string>()
    let localWithMarket = 0
    for (const v of variants) {
      if ((v.competitor_count ?? 0) > 0) localWithMarket += 1
      for (const c of v.competitor_ids ?? []) localComps.add(c)
    }
    const description = roll?.matrix_description ?? first?.matrix_description ?? first?.title ?? 'Matrix'
    const brand = roll?.brand ?? first?.brand ?? null
    const variantsTotal = roll?.variants_total ?? variants.length
    const withMarket = roll?.variants_with_market ?? localWithMarket
    const marketMin = roll?.matrix_market_min_in_stock ?? (mins.length ? Math.min(...mins) : null)
    const retailMin = roll?.current_retail_min ?? (retails.length ? Math.min(...retails) : null)
    const retailMax = roll?.current_retail_max ?? (retails.length ? Math.max(...retails) : null)
    const competitorCount = roll?.competitor_count ?? localComps.size
    const subscribed = roll?.subscribed ?? false
    const retailLabel = retailMin == null ? '—'
      : retailMax != null && retailMax !== retailMin ? `${fmt(retailMin)}–${fmt(retailMax)}` : fmt(retailMin)
    return [
      <TableRow key={`m-${matrixId}`}
                className={cn(isOpen && 'border-x-2 border-t-2 border-sky-400 dark:border-sky-500')}>
        <TableCell className="pr-0">
          <button onClick={() => toggleMatrixExpanded(matrixId)}
                  title="Show matrix variants and competitor coverage"
                  className="text-muted-foreground hover:text-foreground">
            {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </TableCell>
        <TableCell className="text-xs tabular-nums text-muted-foreground">
          {roll?.revenue_rank ?? first?.revenue_rank ?? '—'}
        </TableCell>
        <TableCell className="max-w-72">
          <div className="flex items-center gap-1.5">
            <Layers className={cn('h-3.5 w-3.5 shrink-0',
              isOpen ? 'text-sky-600 dark:text-sky-400' : 'text-muted-foreground')} />
            <span className={cn('truncate', isOpen ? 'font-bold' : 'font-semibold')}>{description}</span>
            <Badge variant="outline" className="shrink-0 px-1.5 py-0 text-[11px]">
              {variantsTotal} variant{variantsTotal === 1 ? '' : 's'}
            </Badge>
            {subscribed && (
              <Badge variant="outline"
                     title="Tracked as a matrix — variants kept in sync each run"
                     className="shrink-0 gap-1 border-sky-200 bg-sky-50 px-1.5 py-0 text-[11px] text-sky-700">
                <Layers className="h-3 w-3" /> tracked
              </Badge>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {[brand, `${withMarket}/${variantsTotal} matched`].filter(Boolean).join(' · ')}
          </p>
        </TableCell>
        <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{retailLabel}</TableCell>
        <TableCell className="text-right tabular-nums">{fmt(marketMin)}</TableCell>
        <TableCell />
        <TableCell className="text-right tabular-nums">{competitorCount}</TableCell>
        <TableCell>
          <div className="flex items-center justify-end gap-0.5">
            {subscribed && (
              <Button variant="ghost" size="sm" title="Stop tracking this matrix"
                      onClick={() => untrackMatrix(matrixId, description)}>
                <X className="h-4 w-4 text-muted-foreground" />
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>,
      isOpen ? (
        <TableRow key={`m-${matrixId}-cov`}
                  className="hover:bg-transparent border-x-2 border-sky-400 dark:border-sky-500">
          <TableCell colSpan={8} className="bg-sky-50/80 dark:bg-sky-950/20 py-3 pl-10 pr-4">
            <MatrixCoveragePanel matrixId={matrixId} variantsTotal={variantsTotal} />
          </TableCell>
        </TableRow>
      ) : null,
      ...(isOpen
        ? variants.flatMap((v, i) =>
            renderProductRow(v, true, i === variants.length - 1 ? 'last' : 'mid'))
        : []),
    ]
  }

  // Order-preserving grouping: matrix variants collapse under one parent at the
  // position of the matrix's first visible variant; standalone items stay inline.
  const renderRows = () => {
    const byMatrix = new Map<string, TrackedProduct[]>()
    for (const p of visible) {
      if (p.item_matrix_id) {
        const list = byMatrix.get(p.item_matrix_id) ?? []
        list.push(p)
        byMatrix.set(p.item_matrix_id, list)
      }
    }
    const seen = new Set<string>()
    const out: ReactNode[] = []
    for (const p of visible) {
      if (p.item_matrix_id) {
        if (seen.has(p.item_matrix_id)) continue
        seen.add(p.item_matrix_id)
        out.push(...renderMatrixGroup(p.item_matrix_id, byMatrix.get(p.item_matrix_id)!))
      } else {
        out.push(...renderProductRow(p))
      }
    }
    return out
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

          <div className="flex flex-wrap items-center gap-2">
            <Select value={competitorFilter} onValueChange={setCompetitorFilter}>
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
            <Select value={brandFilter} onValueChange={setBrandFilter}>
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
            <Select value={positionFilter} onValueChange={setPositionFilter}>
              <SelectTrigger className="h-8 w-40 text-xs">
                <SelectValue placeholder="Market position" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any position</SelectItem>
                <SelectItem value="cheaper">Cheaper than market</SelectItem>
                <SelectItem value="parity">At parity</SelectItem>
                <SelectItem value="pricier">Pricier than market</SelectItem>
                <SelectItem value="none">No market data</SelectItem>
              </SelectContent>
            </Select>
            <Select value={mapFilter} onValueChange={(v) => setMapFilter(v as typeof mapFilter)}>
              <SelectTrigger className="h-8 w-40 text-xs">
                <SelectValue placeholder="MAP" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All items</SelectItem>
                <SelectItem value="tagged">MAP-tagged</SelectItem>
                <SelectItem value="violations">MAP violations</SelectItem>
              </SelectContent>
            </Select>
            {(competitorFilter !== 'all' || brandFilter !== 'all' || positionFilter !== 'all' || mapFilter !== 'all') && (
              <Button variant="ghost" size="sm" className="h-8 text-xs text-muted-foreground"
                      onClick={() => {
                        setCompetitorFilter('all'); setBrandFilter('all')
                        setPositionFilter('all'); setMapFilter('all'); onClearQuickFilter?.()
                      }}>
                Clear filters
              </Button>
            )}
            <span className="text-xs text-muted-foreground">
              {visible.length} of {products.length}
            </span>
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
                  <TableHead className="w-8"><SortHead k="rank" label="#" /></TableHead>
                  <TableHead><SortHead k="title" label="Product" /></TableHead>
                  <TableHead className="text-right"><SortHead k="our" label="Our price" className="justify-end" /></TableHead>
                  <TableHead className="text-right"><SortHead k="market" label="Market min" className="justify-end" /></TableHead>
                  <TableHead>Position</TableHead>
                  <TableHead className="text-right"><SortHead k="stores" label="Stores" className="justify-end" /></TableHead>
                  <TableHead className="w-32 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {renderRows()}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <PricePushDialog
        product={pushTarget}
        open={pushTarget !== null}
        initialPrice={pushInitialPrice}
        onOpenChange={(open) => {
          if (!open) { setPushTarget(null); setPushInitialPrice(null) }
        }}
        onPushed={() => mutate()}
      />

      <MatrixPushDialog
        product={matrixTarget?.product ?? null}
        price={matrixTarget?.price ?? null}
        open={matrixTarget !== null}
        onOpenChange={(open) => !open && setMatrixTarget(null)}
        onPushed={() => mutate()}
      />

      <Dialog open={overrideTarget !== null}
              onOpenChange={(open) => {
                if (!open) { setOverrideTarget(null); setOverrideCandidates([]) }
              }}>
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
                   onChange={(e) => { setOverrideUrl(e.target.value); setOverrideCandidates([]) }}
                   onKeyDown={(e) => e.key === 'Enter' && void saveOverride()} />
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
              <Button size="sm" onClick={() => void saveOverride()} disabled={savingOverride}>
                <Link2 className="h-4 w-4" /> Lock in match
              </Button>
            </div>
            {overrideCandidates.length > 0 && (
              <div className="space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">Choose the matching variant</p>
                <div className="max-h-64 space-y-1 overflow-y-auto">
                  {overrideCandidates.map((candidate, i) => (
                    <button key={`${candidate.variant_id ?? candidate.sku ?? i}`}
                            type="button" onClick={() => void saveOverride(candidate)}
                            disabled={savingOverride}
                            className="flex w-full items-center justify-between rounded border px-3 py-2 text-left text-sm hover:bg-muted">
                      <span>
                        <span className="block font-medium">
                          {candidate.variant_options.join(' / ') || candidate.title || 'Variant'}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {candidate.sku ? `SKU ${candidate.sku}` : candidate.gtin ? `UPC ${candidate.gtin}` : 'No SKU'}
                          {' · '}{candidate.in_stock === false ? 'out of stock' : 'in stock'}
                        </span>
                      </span>
                      <span className="font-semibold tabular-nums">{fmt(candidate.price)}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
