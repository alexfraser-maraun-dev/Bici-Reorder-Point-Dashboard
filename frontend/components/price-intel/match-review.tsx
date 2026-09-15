'use client'

// Human review of pending product links: near-miss fuzzy candidates the LLM
// couldn't confidently confirm or reject. Confirming persists the link — the
// listing matches instantly on every future scrape — and sets aside the pair's
// other candidates (status 'superseded': parked, they return if that link is
// later rejected); rejecting is a permanent tombstone (the listing is never
// proposed again, for any variant).

import { Fragment, useCallback, useMemo, useState } from 'react'
import { mutate as globalMutate } from 'swr'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
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
import { itemIdentity, lightspeedItemUrl, listingVariantLabel } from '@/lib/price-intel/format'
import {
  ApiError, apiPost, useCompetitors, usePriceIntelSummary, useProductLinks,
} from '@/lib/price-intel/hooks'
import type {
  LinkDecisionResult, ProductLink, ProductLinkStatus, VariantCandidate,
  VariantSelectionRequired,
} from '@/lib/price-intel/types'
import { Check, CheckCheck, ExternalLink, Layers, Link2, X } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const itemAttributes = (l: ProductLink) =>
  [l.item_attribute_1, l.item_attribute_2, l.item_attribute_3]
    .filter((a): a is string => !!a && a.trim() !== '')

const STATUS_LABEL: Record<ProductLinkStatus, string> = {
  pending: 'Pending',
  confirmed: 'Confirmed',
  rejected: 'Rejected',
  superseded: 'Set aside',
}

const STATUS_HINT: Record<ProductLinkStatus, string> = {
  pending: "competitor listings the matcher couldn't settle — confirmed links match instantly on future scrapes",
  confirmed: 'live matches — each one is re-scraped nightly',
  rejected: 'tombstoned listings — never proposed again, for any variant',
  superseded: 'parked because the item already has a confirmed link at that store — confirm one here to swap it in',
}

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
  // Colour/size resolved, but not exactly on both dimensions — their "Bronco
  // White" against our "Satin White" shares only the word "white". Labelled
  // apart from `attr` because the queue used to badge these "color+size match"
  // over a row whose own note said the colour or size differed.
  attr_partial: 'partial color/size',
  sibling: 'same page, color+size',
}

export function MatchReview() {
  const [statusFilter, setStatusFilter] = useState<ProductLinkStatus>('pending')
  const { links, isLoading, mutate } = useProductLinks(statusFilter)
  const { competitors } = useCompetitors()
  const { mutate: mutateSummary } = usePriceIntelSummary()
  const [deciding, setDeciding] = useState<Set<string>>(new Set())
  const [fixTarget, setFixTarget] = useState<ProductLink | null>(null)
  const [fixUrl, setFixUrl] = useState('')
  const [savingFix, setSavingFix] = useState(false)
  // Populated when the pasted URL is a multi-variant page the backend can't
  // resolve on its own (409 variant_selection_required) — the user picks the
  // matching variant from this list.
  const [fixCandidates, setFixCandidates] = useState<VariantCandidate[]>([])
  // Bulk selection (pending tab only): link_ids the user has ticked for a batch
  // confirm/reject. Cleared whenever the status filter changes so a stale selection
  // can't leak across tabs.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const changeStatusFilter = (next: ProductLinkStatus) => {
    setSelected(new Set())
    setStatusFilter(next)
  }

  const competitorById = useMemo(
    () => new Map(competitors.map((c) => [c.competitor_id, c.name])),
    [competitors]
  )

  const selectable = statusFilter === 'pending'
  const toggleRow = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const allSelected = selectable && links.length > 0 && links.every((l) => selected.has(l.link_id))
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(links.map((l) => l.link_id)))

  // The pending queue is reviewed item by item: order by our item, then store,
  // then evidence, and put a header above each item so "2 candidates at The
  // Bike Zone" reads as two labelled rows under one heading rather than two
  // lookalike rows scattered by score. The other views stay flat.
  const grouped = statusFilter === 'pending'
  const storeName = useCallback(
    (l: ProductLink) =>
      l.competitor_id ? competitorById.get(l.competitor_id) ?? 'competitor' : 'tracked URL',
    [competitorById]
  )
  const orderedLinks = useMemo(() => {
    if (!grouped) return links
    const evidence = (l: ProductLink) => l.confidence ?? (l.fuzzy_score ?? 0) / 100
    const attrs = (l: ProductLink) => itemAttributes(l).join(' / ')
    return [...links].sort((a, b) =>
      (a.item_title ?? '').localeCompare(b.item_title ?? '')
      || attrs(a).localeCompare(attrs(b))
      || (a.item_id ?? '').localeCompare(b.item_id ?? '')
      || storeName(a).localeCompare(storeName(b))
      || evidence(b) - evidence(a))
  }, [links, grouped, storeName])
  // item_id -> "The Bike Zone ×2 · Racer Sportif ×1" for the group headers.
  const storeCounts = useMemo(() => {
    const counts = new Map<string, Map<string, number>>()
    if (!grouped) return counts
    for (const l of links) {
      const key = l.item_id ?? ''
      const stores = counts.get(key) ?? new Map<string, number>()
      const store = storeName(l)
      stores.set(store, (stores.get(store) ?? 0) + 1)
      counts.set(key, stores)
    }
    return counts
  }, [links, grouped, storeName])
  const columnCount = (selectable ? 1 : 0) + (grouped ? 0 : 1) + 4

  // "This match is wrong — here's the right URL": records the pasted URL as
  // the permanent truth for this item at that store and tombstones the
  // conflicting auto-matches (including this one).
  const saveCorrectUrl = async (candidate?: VariantCandidate) => {
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
        competitor_sku: candidate?.sku,
        competitor_variant_id: candidate?.variant_id,
        competitor_gtin: candidate?.gtin,
        variant_options: candidate?.variant_options,
      })
      toast.success('Correct URL locked in — rejecting the wrong match and fetching the price')
      setFixTarget(null)
      setFixUrl('')
      setFixCandidates([])
      await Promise.all([mutate(), mutateSummary()])
      // rejection + first fetch happen in a background task server-side
      setTimeout(() => { void mutate(); void mutateSummary() }, 6000)
    } catch (e) {
      // Multi-variant page: the backend can't tell which variant is ours —
      // surface its candidate list so the user picks one (mirrors the
      // tracked-products override dialog).
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.detail as VariantSelectionRequired
        if (detail?.code === 'variant_selection_required') {
          setFixCandidates(detail.candidates ?? [])
          if (!detail.candidates?.length) {
            toast.error('No variants could be read from that page — paste the variant-specific URL instead')
          }
          return
        }
      }
      toast.error(e instanceof Error ? e.message : 'Failed to save URL')
    } finally {
      setSavingFix(false)
    }
  }

  const decide = async (rawLinkIds: string[], status: 'confirmed' | 'rejected') => {
    // Drop ids already in flight so a double-click can't fire a second
    // identical batch POST (and double toast) before the first settles.
    const linkIds = rawLinkIds.filter((id) => !deciding.has(id))
    if (linkIds.length === 0) return
    setDeciding((prev) => new Set([...prev, ...linkIds]))
    try {
      // The confirm endpoint is guarded: it may reject (color/size mismatch) or
      // skip (item already linked at that store), so tally the real outcomes.
      // A confirm also sets aside the pair's other pending candidates — the
      // backend reports how many, so the toast can say so.
      let results: LinkDecisionResult[]
      let setAside = 0
      if (linkIds.length > 1) {
        const batch = await apiPost('/api/price-intel/links/decisions', {
          link_ids: linkIds,
          status,
        })
        results = Array.isArray(batch.results) ? batch.results : []
        setAside = Number(batch.superseded ?? 0)
      } else {
        results = [await apiPost(`/api/price-intel/links/${linkIds[0]}/decision`, { status })]
      }
      // Single confirm blocked by an existing match at that store → offer to
      // override. A set-aside row confirmed from the "Set aside" view lands here
      // every time: that is how a parked candidate is swapped in.
      if (status === 'confirmed' && linkIds.length === 1 && results[0]?.can_replace
          && (results[0]?.status === 'skipped' || results[0]?.status === 'superseded')) {
        if (window.confirm('This item is already matched to another listing at this store. Reject that one and use this match instead?')) {
          results = [await apiPost(`/api/price-intel/links/${linkIds[0]}/decision`,
            { status, replace: true })]
        }
      }
      if (linkIds.length === 1) setAside = Number(results[0]?.superseded ?? 0)
      if (status === 'rejected') {
        toast.success(linkIds.length === 1 ? 'Match rejected' : `${linkIds.length} matches rejected`)
      } else {
        const tally = results.reduce((acc: Record<string, number>, r) => {
          const s = r?.status ?? 'confirmed'
          acc[s] = (acc[s] ?? 0) + 1
          return acc
        }, {})
        const parts = [
          tally.confirmed && `${tally.confirmed} confirmed`,
          tally.superseded && `${tally.superseded} set aside (already linked)`,
          tally.skipped && `${tally.skipped} skipped (already linked)`,
          tally.rejected && `${tally.rejected} rejected (mismatch)`,
          setAside > 0 && `${setAside} alternative${setAside === 1 ? '' : 's'} set aside`,
        ].filter(Boolean)
        toast.success(parts.join(' · ') || 'Done')
      }
      await Promise.all([mutate(), mutateSummary()])
      // A confirm kicks off an immediate price fetch server-side; refresh the
      // Tracked Products data once it has landed so the new match shows there.
      if (status === 'confirmed') {
        setTimeout(() => {
          void globalMutate((k) => typeof k === 'string' && k.includes('/api/price-intel/tracked'))
        }, 6000)
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save decision')
    } finally {
      setDeciding((prev) => {
        const next = new Set(prev)
        linkIds.forEach((id) => next.delete(id))
        return next
      })
      setSelected((prev) => {
        if (prev.size === 0) return prev
        const next = new Set(prev)
        linkIds.forEach((id) => next.delete(id))
        return next
      })
    }
  }

  // Batch-apply the current tick selection. Reuses the guarded per-row `decide`
  // (array form), which already tallies confirm/skip/reject outcomes and refreshes.
  const decideSelected = (status: 'confirmed' | 'rejected') => {
    const ids = links.filter((l) => selected.has(l.link_id)).map((l) => l.link_id)
    if (ids.length > 0) void decide(ids, status)
  }

  // "Confirm all high-confidence": pending rows the LLM judged the *same variant*
  // (identical color + size) with a strong fuzzy score. same_model rows are a
  // DIFFERENT variant, so they're never bulk-confirmed; the backend also rejects
  // any color/size mismatch and enforces one confirmed link per (item, store).
  // 'sibling' rows qualify without an LLM verdict (they never get one): each is a
  // color+size match against another variant on a page already confirmed to sell
  // this model, which is what makes approving a model's whole variant set one click.
  const highConfidence = links.filter(
    (l) =>
      l.status === 'pending' &&
      (l.source === 'sibling' ||
        (l.llm_verdict === 'same_variant' && (l.fuzzy_score ?? 0) >= 80))
  )

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Match review</h3>
            <span className="text-xs text-muted-foreground">
              {STATUS_HINT[statusFilter]}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Select value={statusFilter} onValueChange={(v) => changeStatusFilter(v as ProductLinkStatus)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_LABEL) as ProductLinkStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {highConfidence.length > 0 && (
              <Button variant="outline" size="sm" disabled={deciding.size > 0}
                      onClick={() => decide(highConfidence.map((l) => l.link_id), 'confirmed')}>
                <CheckCheck className="h-4 w-4" />
                Confirm {highConfidence.length} high-confidence
              </Button>
            )}
          </div>
        </div>

        {selectable && selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 px-3 py-2">
            <span className="text-sm font-medium">{selected.size} selected</span>
            <div className="ml-auto flex items-center gap-2">
              <Button size="sm" disabled={deciding.size > 0}
                      onClick={() => decideSelected('confirmed')}>
                <Check className="h-4 w-4" /> Confirm selected
              </Button>
              <Button variant="outline" size="sm" disabled={deciding.size > 0}
                      onClick={() => decideSelected('rejected')}>
                <X className="h-4 w-4" /> Reject selected
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                Clear
              </Button>
            </div>
          </div>
        )}

        {isLoading ? (
          <Skeleton className="h-48 rounded-lg" />
        ) : links.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {statusFilter === 'pending'
              ? 'Nothing to review — new candidates arrive after each nightly scrape.'
              : statusFilter === 'superseded'
                ? 'Nothing set aside.'
                : `No ${statusFilter} links yet.`}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {selectable && (
                  <TableHead className="w-8">
                    <Checkbox
                      checked={allSelected ? true : selected.size > 0 ? 'indeterminate' : false}
                      onCheckedChange={toggleAll}
                      aria-label="Select all pending matches" />
                  </TableHead>
                )}
                {!grouped && <TableHead>Our item</TableHead>}
                <TableHead>Competitor listing</TableHead>
                <TableHead className="text-right">Prices</TableHead>
                <TableHead>Signal</TableHead>
                <TableHead className="w-28 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orderedLinks.map((link: ProductLink, index: number) => {
                const busy = deciding.has(link.link_id)
                const attributes = itemAttributes(link)
                const variant = listingVariantLabel(link)
                // Shopify/Magento titles already end in " - Colour / Size"; only
                // SmartEtailing's bare model titles need the label appended.
                const appendVariant = variant !== null
                  && !(link.competitor_title ?? '').toLowerCase().includes(variant.toLowerCase())
                const newGroup = grouped
                  && (index === 0 || orderedLinks[index - 1].item_id !== link.item_id)
                const stores = storeCounts.get(link.item_id ?? '')
                const ourItem = (
                  <>
                    {link.item_id ? (
                      <a href={lightspeedItemUrl(link.item_id)} target="_blank"
                         rel="noopener noreferrer" title="Open in Lightspeed"
                         className="block whitespace-normal break-words text-sm font-medium leading-snug hover:underline">
                        {link.item_title ?? 'Untracked item'}
                        {attributes.length > 0 ? (
                          <span className="font-normal text-muted-foreground"> — {attributes.join(' / ')}</span>
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
                  </>
                )
                return (
                  <Fragment key={link.link_id}>
                  {newGroup && (
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableCell colSpan={columnCount} className="py-2">
                        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
                          <div className="min-w-0">{ourItem}</div>
                          {stores && (
                            <span className="text-xs text-muted-foreground">
                              {[...stores.entries()]
                                .map(([store, n]) => `${store} ×${n}`)
                                .join(' · ')}
                            </span>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                  <TableRow className={cn(busy && 'opacity-50')}>
                    {selectable && (
                      <TableCell className="align-top">
                        <Checkbox checked={selected.has(link.link_id)} disabled={busy}
                                  onCheckedChange={() => toggleRow(link.link_id)}
                                  aria-label="Select match" />
                      </TableCell>
                    )}
                    {!grouped && (
                      <TableCell className="max-w-80 align-top">{ourItem}</TableCell>
                    )}
                    <TableCell className="max-w-80 align-top">
                      <div className="flex items-start gap-1.5">
                        <span className="whitespace-normal break-words text-sm leading-snug">
                          {link.competitor_title
                            ?? link.competitor_url?.replace(/^https?:\/\//, '')
                            ?? 'competitor listing'}
                          {/* The variant the listing names. On SmartEtailing
                              stores the title is the bare model name and this
                              is the only place the colour/size shows. */}
                          {appendVariant ? (
                            <span className="text-muted-foreground"> — {variant}</span>
                          ) : null}
                        </span>
                        {link.competitor_url && (
                          <a href={link.competitor_url} target="_blank" rel="noopener noreferrer"
                             className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </div>
                      {!variant && attributes.length > 0 && (
                        <p className="whitespace-normal break-words text-xs text-amber-700">
                          no variant info on this listing — open the link to check which one it is
                        </p>
                      )}
                      <p className="whitespace-normal break-words text-xs text-muted-foreground">
                        {storeName(link)}
                        {link.gtin
                          ? ` · UPC ${link.gtin}`
                          : link.competitor_sku ? ` · SKU ${link.competitor_sku}` : ''}
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
                        <Badge variant="outline" className="text-muted-foreground">
                          {statusFilter === 'pending' && link.source === 'llm'
                            ? 'fuzzy candidate'
                            : SOURCE_LABEL[link.source] ?? link.source}
                        </Badge>
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
                        {(statusFilter === 'pending' || statusFilter === 'superseded') && (
                          <Button variant="ghost" size="sm" disabled={busy}
                                  title={statusFilter === 'superseded'
                                    ? 'Use this match instead of the confirmed one at this store'
                                    : 'Confirm match'}
                                  onClick={() => decide([link.link_id], 'confirmed')}>
                            <Check className="h-4 w-4 text-emerald-600" />
                          </Button>
                        )}
                        {statusFilter === 'pending' && (
                          <Button variant="ghost" size="sm"
                                  title="Reject — never suggest this listing again (for any variant)"
                                  disabled={busy}
                                  onClick={() => decide([link.link_id], 'rejected')}>
                            <X className="h-4 w-4 text-rose-600" />
                          </Button>
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
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={fixTarget !== null}
              onOpenChange={(open) => {
                if (!open) { setFixTarget(null); setFixCandidates([]) }
              }}>
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
                   onChange={(e) => { setFixUrl(e.target.value); setFixCandidates([]) }}
                   onKeyDown={(e) => e.key === 'Enter' && saveCorrectUrl()} />
            <Button size="sm" onClick={() => void saveCorrectUrl()} disabled={savingFix}>
              <Link2 className="h-4 w-4" /> Lock in
            </Button>
          </div>
          {fixCandidates.length > 0 && (
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">Choose the matching variant</p>
              <div className="max-h-64 space-y-1 overflow-y-auto">
                {fixCandidates.map((candidate, i) => (
                  <button key={`${candidate.variant_id ?? candidate.sku ?? i}`}
                          type="button" onClick={() => void saveCorrectUrl(candidate)}
                          disabled={savingFix}
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
        </DialogContent>
      </Dialog>
    </Card>
  )
}
