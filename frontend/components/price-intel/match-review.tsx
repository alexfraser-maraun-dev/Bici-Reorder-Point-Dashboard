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
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { itemIdentity, lightspeedItemUrl } from '@/lib/price-intel/format'
import {
  apiPost, useCompetitors, usePriceIntelSummary, useProductLinks,
} from '@/lib/price-intel/hooks'
import type { ProductLink } from '@/lib/price-intel/types'
import { Check, CheckCheck, ExternalLink, Layers, Link2, X } from 'lucide-react'

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
  serp: 'SERP found',
  attr: 'color+size match',
}

export function MatchReview() {
  const [statusFilter, setStatusFilter] = useState<'pending' | 'confirmed' | 'rejected'>('pending')
  const { links, isLoading, mutate } = useProductLinks(statusFilter)
  const { competitors } = useCompetitors()
  const { mutate: mutateSummary } = usePriceIntelSummary()
  const [deciding, setDeciding] = useState<Set<string>>(new Set())
  const [fixTarget, setFixTarget] = useState<ProductLink | null>(null)
  const [fixUrl, setFixUrl] = useState('')
  const [savingFix, setSavingFix] = useState(false)

  const competitorById = useMemo(
    () => new Map(competitors.map((c) => [c.competitor_id, c.name])),
    [competitors]
  )

  // "This match is wrong — here's the right URL": records the pasted URL as
  // the permanent truth for this item at that store and tombstones the
  // conflicting auto-matches (including this one).
  const saveCorrectUrl = async () => {
    if (!fixTarget?.item_id) return
    const url = fixUrl.trim()
    if (!/^https?:\/\//.test(url)) {
      toast.error('Enter a full product URL (https://…)')
      return
    }
    setSavingFix(true)
    try {
      await apiPost('/api/price-intel/urls', {
        url,
        item_id: fixTarget.item_id,
        competitor_id: fixTarget.competitor_id,
        label: fixTarget.item_title,
      })
      toast.success('Correct URL locked in — rejecting the wrong match and fetching the price')
      setFixTarget(null)
      setFixUrl('')
      await Promise.all([mutate(), mutateSummary()])
      // rejection + first fetch happen in a background task server-side
      setTimeout(() => { void mutate(); void mutateSummary() }, 6000)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save URL')
    } finally {
      setSavingFix(false)
    }
  }

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
                <TableHead className="w-28 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {links.map((link: ProductLink) => {
                const busy = deciding.has(link.link_id)
                const itemAttributes = [link.item_attribute_1, link.item_attribute_2, link.item_attribute_3]
                  .filter((a): a is string => !!a && a.trim() !== '')
                return (
                  <TableRow key={link.link_id} className={cn(busy && 'opacity-50')}>
                    <TableCell className="max-w-80 align-top">
                      {link.item_id ? (
                        <a href={lightspeedItemUrl(link.item_id)} target="_blank"
                           rel="noopener noreferrer" title="Open in Lightspeed"
                           className="block whitespace-normal break-words text-sm font-medium leading-snug hover:underline">
                          {link.item_title ?? 'Untracked item'}
                          {itemAttributes.length > 0 ? (
                            <span className="font-normal text-muted-foreground"> — {itemAttributes.join(' / ')}</span>
                          ) : null}
                        </a>
                      ) : (
                        <p className="whitespace-normal break-words text-sm font-medium leading-snug">
                          {link.item_title ?? 'Untracked item'}
                        </p>
                      )}
                      <p className="whitespace-normal break-words text-xs text-muted-foreground">
                        {itemIdentity({
                          brand: link.item_brand,
                          upc: link.item_upc,
                          systemSku: link.item_system_sku,
                        })}
                      </p>
                    </TableCell>
                    <TableCell className="max-w-80 align-top">
                      <div className="flex items-start gap-1.5">
                        <span className="whitespace-normal break-words text-sm leading-snug">
                          {link.competitor_title
                            ?? link.competitor_url?.replace(/^https?:\/\//, '')
                            ?? 'competitor listing'}
                        </span>
                        {link.competitor_url && (
                          <a href={link.competitor_url} target="_blank" rel="noopener noreferrer"
                             className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </div>
                      <p className="whitespace-normal break-words text-xs text-muted-foreground">
                        {link.competitor_id
                          ? competitorById.get(link.competitor_id) ?? 'competitor'
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
                    <TableCell>
                      <div className="flex items-center justify-end gap-0.5">
                        {statusFilter === 'pending' && (
                          <>
                            <Button variant="ghost" size="sm" title="Confirm match" disabled={busy}
                                    onClick={() => decide([link.link_id], 'confirmed')}>
                              <Check className="h-4 w-4 text-emerald-600" />
                            </Button>
                            <Button variant="ghost" size="sm" title="Reject (never suggest again)"
                                    disabled={busy}
                                    onClick={() => decide([link.link_id], 'rejected')}>
                              <X className="h-4 w-4 text-rose-600" />
                            </Button>
                          </>
                        )}
                        {link.item_id && statusFilter !== 'rejected' && (
                          <Button variant="ghost" size="sm" disabled={busy}
                                  title="Wrong match? Paste the correct competitor URL"
                                  onClick={() => setFixTarget(link)}>
                            <Link2 className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={fixTarget !== null} onOpenChange={(open) => !open && setFixTarget(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Paste the correct competitor URL</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            The right product page for{' '}
            <span className="font-medium text-foreground">{fixTarget?.item_title ?? 'this item'}</span>
            {fixTarget?.competitor_id
              ? <> at <span className="font-medium text-foreground">{competitorById.get(fixTarget.competitor_id)}</span></>
              : null}
            . It becomes the permanent match — this suggestion and any other
            auto-matches at that store are rejected.
          </p>
          <div className="flex items-center gap-2">
            <Input placeholder="https://store.example.com/products/…" value={fixUrl}
                   onChange={(e) => setFixUrl(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && saveCorrectUrl()} />
            <Button size="sm" onClick={saveCorrectUrl} disabled={savingFix}>
              <Link2 className="h-4 w-4" /> Lock in
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
