'use client'

import { useState } from 'react'
import { useSWRConfig } from 'swr'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useSoRecommendation } from '@/lib/hooks'
import type { PoRecommendationCandidate, PoRecommendationTier, SpecialOrder } from '@/lib/types'
import { cn } from '@/lib/utils'
import {
  PackageCheck, ArrowLeftRight, Truck, FileClock, FilePlus, ChevronDown, ChevronRight,
  Copy, ExternalLink, RefreshCw,
} from 'lucide-react'

const TIER_META: Record<PoRecommendationTier, { label: string; className: string; icon: typeof Truck }> = {
  in_stock: { label: 'Already in stock', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
  transfer: { label: 'Transfer', className: 'bg-teal-100 text-teal-700 border-teal-200', icon: ArrowLeftRight },
  inbound_po: { label: 'Already inbound', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: Truck },
  draft_po: { label: 'Suitable draft PO', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: FileClock },
  new_po: { label: 'Needs a new PO', className: 'bg-slate-100 text-slate-700 border-slate-200', icon: FilePlus },
}

function CandidateLine({ c }: { c: PoRecommendationCandidate }) {
  const meta = TIER_META[c.tier]
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Badge variant="outline" className={cn('gap-1 text-xs', meta.className)}>
        <meta.icon className="h-3 w-3" />
        {meta.label}
      </Badge>
      {c.reference_number && <span className="font-mono">{c.reference_number}</span>}
      {c.vendor_name && <span className="text-muted-foreground">{c.vendor_name}</span>}
      {c.unallocated_units !== undefined && (
        <span className="text-muted-foreground">{c.unallocated_units} free</span>
      )}
      {c.sellable !== undefined && <span className="text-muted-foreground">{c.sellable} sellable</span>}
      {c.landing_date && <span className="font-mono">lands {c.landing_date}</span>}
      {c.eta && c.eta !== c.landing_date && (
        <span className={cn('font-mono text-muted-foreground', c.eta_overdue && 'text-red-600')}>
          {c.eta_overdue ? `PO ETA ${c.eta} passed` : `PO ETA ${c.eta}`}
        </span>
      )}
      {c.is_routine === false && (
        <span className="text-muted-foreground" title="Procurement only orders from this vendor occasionally, so this PO is a deliberate send rather than one that rides along">
          occasional vendor
        </span>
      )}
      {c.meets_promise === false && <span className="text-red-600">misses promise</span>}
      {c.meets_promise === true && <span className="text-emerald-600">meets promise</span>}
    </div>
  )
}

function handoffText(order: SpecialOrder, candidate: PoRecommendationCandidate, reason: string) {
  const details = [
    `SO #${order.special_order_id}${order.description ? ` — ${order.description}` : ''}`,
    order.system_sku ? `SKU: ${order.system_sku}` : null,
    order.upc ? `UPC: ${order.upc}` : null,
    candidate.reference_number ? `Recommended PO: ${candidate.reference_number}` : null,
    candidate.vendor_name ? `Vendor: ${candidate.vendor_name}` : null,
    candidate.landing_date ? `Expected landing: ${candidate.landing_date}` : null,
    reason ? `Reason: ${reason}` : null,
  ]
  return details.filter(Boolean).join('\n')
}

/** "Where should this go?" — a recommendation and a truthful Lightspeed handoff.
 *
 *  Recommend-only by design: Lightspeed cannot perform the allocation over its API (SpecialOrder
 *  is read-only, and creating the PO line does not link it), so the buyer makes the final click
 *  in Lightspeed and the next sweep confirms it landed. */
export function PoRecommendationPanel({ order }: { order: SpecialOrder }) {
  const [open, setOpen] = useState(false)
  const [copying, setCopying] = useState(false)
  const { mutate } = useSWRConfig()
  const recommendationKey = `/backend/api/special-orders/${order.special_order_id}/po-recommendation`
  const { recommendation, isLoading, error } = useSoRecommendation(open ? order.special_order_id : null)

  const copyHandoff = async () => {
    if (!recommendation) return
    setCopying(true)
    try {
      await navigator.clipboard.writeText(
        handoffText(order, recommendation.recommendation, recommendation.reason)
      )
      toast.success('Recommendation copied for Lightspeed handoff.')
    } catch {
      toast.error('Could not copy the recommendation.')
    } finally {
      setCopying(false)
    }
  }

  return (
    <div className="border-t pt-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={`so-po-recommendation-${order.special_order_id}`}
        className="flex min-h-9 items-center gap-1.5 rounded px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:bg-muted/50 hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Where to order
      </button>

      {open && (
        <div id={`so-po-recommendation-${order.special_order_id}`} className="mt-2 space-y-2">
          {isLoading && (
            <div aria-live="polite" aria-busy="true" className="space-y-2">
              <span className="sr-only">Loading sourcing recommendation</span>
              <Skeleton className="h-12 w-full" />
            </div>
          )}

          {error && !isLoading && (
            <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              <span className="min-w-0 flex-1">The sourcing recommendation could not be loaded.</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-9 gap-1.5 bg-background"
                onClick={() => void mutate(recommendationKey)}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </Button>
            </div>
          )}

          {!isLoading && !error && !recommendation && (
            <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              No sourcing recommendation is available for this order.
            </p>
          )}

          {recommendation && (
            <>
              {/* The headline is the landing date, not a promise. Most special orders have no
                  quoted date at all, so "how fast can this be here" is the only question that
                  can always be answered — and the only one procurement controls. */}
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Fastest route
                </span>
                {recommendation.fastest_landing_date && (
                  <span className="font-mono text-sm font-semibold">
                    lands {recommendation.fastest_landing_date}
                  </span>
                )}
                {!!recommendation.days_lost && (
                  <span className="text-xs text-red-600" title="Time already lost: it could have been here by the date shown, had it been ordered when the special order appeared.">
                    {recommendation.days_lost}d lost
                    {recommendation.could_have_landed && (
                      <span className="text-muted-foreground">
                        {' '}(could have landed {recommendation.could_have_landed})
                      </span>
                    )}
                  </span>
                )}
              </div>

              <div className="rounded-md bg-muted/50 px-3 py-2">
                <CandidateLine c={recommendation.recommendation} />
                <p className="mt-1 text-xs text-muted-foreground">{recommendation.reason}</p>
              </div>

              {recommendation.alternatives.length > 0 && (
                <div className="space-y-1 pl-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Alternatives
                  </span>
                  {recommendation.alternatives.map((a, i) => <CandidateLine key={i} c={a} />)}
                </div>
              )}

              {/* A cold PO snapshot means the draft tier could not be evaluated. Say so, rather
                  than letting "needs a new PO" look like a finding. */}
              {!recommendation.draft_pos_available && (
                <p className="text-xs text-amber-700">
                  Draft purchase orders could not be checked just now — “needs a new PO” may be
                  incomplete. Reload in a moment.
                </p>
              )}

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-h-9 gap-1.5 text-xs"
                  disabled={copying}
                  onClick={() => void copyHandoff()}
                >
                  <Copy className="h-3.5 w-3.5" />
                  {copying ? 'Copying…' : 'Copy handoff details'}
                </Button>
                {order.ls_item_url && (
                  <Button variant="outline" size="sm" className="min-h-9 gap-1.5 text-xs" asChild>
                    <a href={order.ls_item_url} target="_blank" rel="noopener noreferrer">
                      Open product in Lightspeed
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </Button>
                )}
                {order.ls_order_url && (
                  <Button variant="outline" size="sm" className="min-h-9 gap-1.5 text-xs" asChild>
                    <a href={order.ls_order_url} target="_blank" rel="noopener noreferrer">
                      Open assigned PO
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Complete the allocation in Lightspeed. Its API cannot attach a special order to a
                purchase order; refresh this dashboard afterward to confirm the assignment.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
