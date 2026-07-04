'use client'

// Human review of pending product links: near-miss fuzzy candidates the LLM
// couldn't confidently confirm or reject. Confirming persists the link — the
// listing matches instantly on every future scrape; rejecting is a permanent
// tombstone (the pair is never proposed again).

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import {
  apiPost, useCompetitors, usePriceIntelSummary, useProductLinks, useTrackedProducts,
} from '@/lib/price-intel/hooks'
import type { ProductLink } from '@/lib/price-intel/types'
import { Check, CheckCheck, ExternalLink, Layers, X } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const VERDICT_TONE: Record<string, string> = {
  same_variant: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  same_model: 'bg-sky-50 text-sky-700 border-sky-200',
  uncertain: 'bg-amber-50 text-amber-800 border-amber-200',
  different: 'bg-rose-50 text-rose-700 border-rose-200',
  error: 'bg-slate-100 text-slate-600 border-slate-200',
}

const SOURCE_LABEL: Record<string, string> = {
  gtin: 'UPC match',
  llm: 'LLM verified',
  human: 'confirmed by you',
  manual_url: 'tracked URL',
}

export function MatchReview() {
  const [statusFilter, setStatusFilter] = useState<'pending' | 'confirmed' | 'rejected'>('pending')
  const { links, isLoading, mutate } = useProductLinks(statusFilter)
  const { products } = useTrackedProducts()
  const { competitors } = useCompetitors()
  const { mutate: mutateSummary } = usePriceIntelSummary()
  const [deciding, setDeciding] = useState<Set<string>>(new Set())

  const productById = useMemo(
    () => new Map(products.map((p) => [p.item_id, p])),
    [products]
  )
  const competitorById = useMemo(
    () => new Map(competitors.map((c) => [c.competitor_id, c.name])),
    [competitors]
  )

  const decide = async (linkIds: string[], status: 'confirmed' | 'rejected') => {
    setDeciding((prev) => new Set([...prev, ...linkIds]))
    try {
      await Promise.all(
        linkIds.map((id) => apiPost(`/api/price-intel/links/${id}/decision`, { status }))
      )
      toast.success(
        linkIds.length === 1
          ? status === 'confirmed' ? 'Match confirmed' : 'Match rejected'
          : `${linkIds.length} matches ${status}`
      )
      await Promise.all([mutate(), mutateSummary()])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save decision')
    } finally {
      setDeciding((prev) => {
        const next = new Set(prev)
        linkIds.forEach((id) => next.delete(id))
        return next
      })
    }
  }

  // "Confirm all high-confidence": pending rows the LLM called same_model but
  // couldn't anchor to one variant, with a strong fuzzy score.
  const highConfidence = links.filter(
    (l) => l.status === 'pending' && l.llm_verdict === 'same_model' && (l.fuzzy_score ?? 0) >= 80
  )

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Match review</h3>
            <span className="text-xs text-muted-foreground">
              competitor listings the matcher couldn&apos;t settle — confirmed links match instantly on future scrapes
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">Pending</SelectItem>
                <SelectItem value="confirmed">Confirmed</SelectItem>
                <SelectItem value="rejected">Rejected</SelectItem>
              </SelectContent>
            </Select>
            {highConfidence.length > 0 && (
              <Button variant="outline" size="sm"
                      onClick={() => decide(highConfidence.map((l) => l.link_id), 'confirmed')}>
                <CheckCheck className="h-4 w-4" />
                Confirm {highConfidence.length} high-confidence
              </Button>
            )}
          </div>
        </div>

        {isLoading ? (
          <Skeleton className="h-48 rounded-lg" />
        ) : links.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {statusFilter === 'pending'
              ? 'Nothing to review — new candidates arrive after each nightly scrape.'
              : `No ${statusFilter} links yet.`}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Our item</TableHead>
                <TableHead>Competitor listing</TableHead>
                <TableHead className="text-right">Prices</TableHead>
                <TableHead>Signal</TableHead>
                {statusFilter === 'pending' && <TableHead className="w-24 text-right">Decide</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {links.map((link: ProductLink) => {
                const item = link.item_id ? productById.get(link.item_id) : undefined
                const busy = deciding.has(link.link_id)
                return (
                  <TableRow key={link.link_id} className={cn(busy && 'opacity-50')}>
                    <TableCell className="max-w-64">
                      <p className="truncate text-sm font-medium">
                        {item?.title ?? link.item_id ?? '—'}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {item?.brand}
                        {item?.matrix_description && item.matrix_description !== item.title
                          ? ` · ${item.matrix_description}` : ''}
                        {item?.attribute_1 ? ` · ${item.attribute_1}` : ''}
                      </p>
                    </TableCell>
                    <TableCell className="max-w-72">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm">{link.competitor_title ?? link.match_key}</span>
                        {link.competitor_url && (
                          <a href={link.competitor_url} target="_blank" rel="noopener noreferrer"
                             className="shrink-0 text-muted-foreground hover:text-foreground">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </div>
                      <p className="truncate text-xs text-muted-foreground">
                        {link.competitor_id
                          ? competitorById.get(link.competitor_id) ?? link.competitor_id
                          : 'tracked URL'}
                        {link.competitor_sku ? ` · ${link.competitor_sku}` : ''}
                      </p>
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      <span className="text-muted-foreground">us </span>{fmt(link.our_price)}
                      <span className="text-muted-foreground"> · them </span>{fmt(link.their_price)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1">
                        {link.level === 'model' && (
                          <Badge variant="outline" className="gap-1 bg-sky-50 text-sky-700 border-sky-200">
                            <Layers className="h-3 w-3" /> model
                          </Badge>
                        )}
                        {link.fuzzy_score != null && (
                          <Badge variant="outline" className="tabular-nums">
                            {Math.round(link.fuzzy_score)}%
                          </Badge>
                        )}
                        {link.llm_verdict && (
                          <Badge variant="outline"
                                 className={VERDICT_TONE[link.llm_verdict] ?? VERDICT_TONE.error}
                                 title={link.llm_reason ?? undefined}>
                            {link.llm_verdict.replace('_', ' ')}
                          </Badge>
                        )}
                        {statusFilter !== 'pending' && (
                          <Badge variant="outline" className="text-muted-foreground">
                            {SOURCE_LABEL[link.source] ?? link.source}
                          </Badge>
                        )}
                      </div>
                      {link.llm_reason && (
                        <p className="mt-0.5 max-w-56 truncate text-xs text-muted-foreground"
                           title={link.llm_reason}>
                          {link.llm_reason}
                        </p>
                      )}
                    </TableCell>
                    {statusFilter === 'pending' && (
                      <TableCell>
                        <div className="flex items-center justify-end gap-0.5">
                          <Button variant="ghost" size="sm" title="Confirm match" disabled={busy}
                                  onClick={() => decide([link.link_id], 'confirmed')}>
                            <Check className="h-4 w-4 text-emerald-600" />
                          </Button>
                          <Button variant="ghost" size="sm" title="Reject (never suggest again)"
                                  disabled={busy}
                                  onClick={() => decide([link.link_id], 'rejected')}>
                            <X className="h-4 w-4 text-rose-600" />
                          </Button>
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
