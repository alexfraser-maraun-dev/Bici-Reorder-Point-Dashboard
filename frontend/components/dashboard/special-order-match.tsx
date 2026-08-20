'use client'

/** Manual Shopify<->Lightspeed linking, and the workorder notes dialog.
 *
 *  All of the human-decision UI lives here: the picker that can reach ANY Shopify order (not
 *  just the unmatched pool), the confirmation step that shows an order's line items before a
 *  link is written, and the unlink / "stop suggesting these" controls.
 *
 *  Note the dialogs are controlled (`open` prop). Radix does not mount DialogContent while
 *  closed, so a per-row dialog costs a state hook and a context provider — not a dialog tree.
 */

import { useState, useEffect, useMemo, useRef } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { lookupShopifyOrders } from '@/lib/hooks'
import type { SpecialOrder, ShopifyOnlyOrder, ShopifyOrderLookup } from '@/lib/types'
import { ShopifyMatchBadge } from './special-order-badges'
import { Field, FieldGroup } from './special-order-fields'
import {
  Link2, Unlink, X, ArrowLeft, Loader2, Search, AlertTriangle, Check, ExternalLink,
  Wrench, Package,
} from 'lucide-react'

// Manual match/unmatch plumbing threaded down from the page: both resolve once the backend
// has persisted the override and rebuilt its cache (the page then revalidates).
export interface MatchActions {
  onMatch: (specialOrderId: string, shopifyOrderId: string) => Promise<void>
  onUnmatch: (specialOrderId: string, shopifyOrderId: string) => Promise<void>
}

// One pickable row inside the match dialog.
interface PickerItem {
  key: string
  title: string
  subtitle?: string | null
  meta?: string | null
  candidate?: boolean // true = one of the ambiguous match's likely candidates (listed first)
}

// How long the picker waits after the last keystroke before asking Shopify. Long enough that
// typing a 6-digit order number is one request, short enough to feel immediate.
const LOOKUP_DEBOUNCE_MS = 400
// Below this, a term is too vague to spend a Shopify search on.
const LOOKUP_MIN_CHARS = 3

export function OrderStateChips({ order }: { order: ShopifyOrderLookup }) {
  const chips: { label: string; tone: 'warn' | 'muted' }[] = []
  if (order.cancelled) chips.push({ label: 'Cancelled', tone: 'warn' })
  if (order.closed) chips.push({ label: 'Closed', tone: 'warn' })
  if (order.test) chips.push({ label: 'Test order', tone: 'warn' })
  if (order.fulfillment_status === 'FULFILLED') chips.push({ label: 'Fulfilled', tone: 'warn' })
  if (order.fulfillment_status && order.fulfillment_status !== 'FULFILLED')
    chips.push({ label: order.fulfillment_status.replace(/_/g, ' ').toLowerCase(), tone: 'muted' })
  if (order.financial_status)
    chips.push({ label: order.financial_status.replace(/_/g, ' ').toLowerCase(), tone: 'muted' })
  if (!chips.length) return null
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c) => (
        <span
          key={c.label}
          className={cn(
            'rounded border px-1.5 py-0.5 text-[10px] font-medium capitalize',
            c.tone === 'warn'
              ? 'border-amber-300 bg-amber-50 text-amber-800'
              : 'border-border bg-muted/50 text-muted-foreground'
          )}
        >
          {c.label}
        </span>
      ))}
    </div>
  )
}

// The confirmation step for a looked-up Shopify order: everything on the order, line items
// included, so the user verifies they picked the right one before the link is written.
export function LookupConfirmation({
  order,
  busy,
  onBack,
  onConfirm,
}: {
  order: ShopifyOrderLookup
  busy: boolean
  onBack: () => void
  onConfirm: () => void
}) {
  const risky = order.cancelled || order.closed || order.test
  return (
    <div className="flex flex-col gap-3">
      <button
        type="button"
        onClick={onBack}
        disabled={busy}
        className="text-muted-foreground hover:text-foreground flex items-center gap-1 self-start text-xs disabled:opacity-50"
      >
        <ArrowLeft className="h-3 w-3" />
        Back to results
      </button>

      <div className="flex flex-col gap-1.5 rounded-md border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">{order.order_name ?? `Order ${order.order_id}`}</span>
          {order.shopify_order_url && (
            <a
              href={order.shopify_order_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
            >
              Open in Shopify
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
        <div className="text-muted-foreground text-xs">
          {[order.customer_name, order.customer_email, order.customer_phone].filter(Boolean).join(' · ') || 'No customer details'}
        </div>
        <div className="text-muted-foreground text-xs">
          {[
            order.created_at ? `Placed ${order.created_at.slice(0, 10)}` : null,
            order.shopify_expected_date ? `ETA ${order.shopify_expected_date}` : 'No ETA set',
          ]
            .filter(Boolean)
            .join(' · ')}
        </div>
        <OrderStateChips order={order} />
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
          Line items ({order.line_items.length})
        </span>
        <div className="flex max-h-56 flex-col gap-1 overflow-y-auto pr-1">
          {order.line_items.length === 0 && (
            <div className="text-muted-foreground py-3 text-center text-xs">
              This order has no line items.
            </div>
          )}
          {order.line_items.map((li, idx) => (
            <div key={`${li.sku ?? 'nosku'}-${idx}`} className="flex items-start gap-2 rounded border px-2.5 py-1.5">
              <span className="text-muted-foreground shrink-0 pt-0.5 text-xs tabular-nums">{li.quantity ?? 1}×</span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{li.title ?? 'Untitled item'}</div>
                <div className="text-muted-foreground truncate font-mono text-[11px]">
                  {[li.sku ?? 'no SKU', li.variant_title].filter(Boolean).join(' · ')}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {risky && (
        <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            This order is {[order.cancelled && 'cancelled', order.closed && 'closed', order.test && 'a test order']
              .filter(Boolean)
              .join(', ')}
            . Link it only if that is genuinely the order this special order fulfils.
          </span>
        </div>
      )}

      <Button size="sm" disabled={busy} onClick={onConfirm} className="gap-2">
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
        Link {order.order_name ?? `order ${order.order_id}`}
      </Button>
    </div>
  )
}

// The manual-match picker: a searchable list of link targets. Likely candidates (from an
// ambiguous match) are grouped on top. `onPick` persists the link; the dialog closes on
// success and stays open (with a toast from the caller) on failure.
//
// With `lookupShopify`, the same box also searches ALL of Shopify (any tag, any fulfillment
// state, any age) for whatever is typed — that's how an SO reaches an order the automatic
// population never offered. Those hits are NOT one-click: picking one opens a confirmation
// panel listing the order's line items, and only the explicit confirm writes the link.
export function MatchPickerDialog({
  open,
  onOpenChange,
  title,
  description,
  items,
  onPick,
  footerAction,
  lookupShopify = false,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  items: PickerItem[]
  onPick: (key: string) => Promise<void>
  footerAction?: { label: string; onClick: () => Promise<void> }
  lookupShopify?: boolean
}) {
  const [term, setTerm] = useState('')
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [results, setResults] = useState<ShopifyOrderLookup[]>([])
  const [searching, setSearching] = useState(false)
  const [confirming, setConfirming] = useState<ShopifyOrderLookup | null>(null)

  const filtered = useMemo(() => {
    const t = term.trim().toLowerCase()
    const hits = t
      ? items.filter((i) => [i.title, i.subtitle, i.meta].some((v) => v && v.toLowerCase().includes(t)))
      : items
    // Candidates first, then the rest, both in given order.
    return [...hits.filter((i) => i.candidate), ...hits.filter((i) => !i.candidate)]
  }, [items, term])

  // Debounced Shopify-wide lookup. `cancelled` guards against an earlier, slower response
  // overwriting a later one (and against setting state after the dialog closes).
  useEffect(() => {
    if (!lookupShopify) return
    const t = term.trim()
    if (t.length < LOOKUP_MIN_CHARS) {
      setResults([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    const timer = setTimeout(async () => {
      try {
        const found = await lookupShopifyOrders(t)
        if (!cancelled) setResults(found)
      } catch {
        if (!cancelled) setResults([])
      } finally {
        if (!cancelled) setSearching(false)
      }
    }, LOOKUP_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [term, lookupShopify])

  // Reset per-opening state so a reopened dialog never shows the last search or confirmation.
  useEffect(() => {
    if (!open) {
      setTerm('')
      setResults([])
      setConfirming(null)
      setSearching(false)
    }
  }, [open])

  // Orders already offered in the local list don't need a duplicate "from Shopify" row.
  const localKeys = useMemo(() => new Set(items.map((i) => i.key)), [items])
  const extraResults = useMemo(
    () => results.filter((r) => !localKeys.has(r.order_id)),
    [results, localKeys]
  )

  const pick = async (key: string) => {
    setBusyKey(key)
    try {
      await onPick(key)
      onOpenChange(false)
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!busyKey) onOpenChange(o) }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {confirming ? (
          <LookupConfirmation
            order={confirming}
            busy={busyKey !== null}
            onBack={() => setConfirming(null)}
            onConfirm={() => void pick(confirming.order_id)}
          />
        ) : (
          <>
            <Input
              placeholder={
                lookupShopify
                  ? 'Type any Shopify order # (or customer, SKU, email)…'
                  : 'Filter by order #, customer, SKU…'
              }
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              className="h-8"
            />
            <div className="flex max-h-72 flex-col gap-1 overflow-y-auto pr-1">
              {filtered.length === 0 && extraResults.length === 0 && !searching && (
                <div className="text-muted-foreground py-6 text-center text-sm">
                  {lookupShopify && term.trim().length < LOOKUP_MIN_CHARS
                    ? 'No matches. Type an order number to search all of Shopify.'
                    : 'No matches.'}
                </div>
              )}
              {filtered.map((i) => (
                <button
                  key={i.key}
                  type="button"
                  disabled={busyKey !== null}
                  onClick={() => void pick(i.key)}
                  className={cn(
                    'flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors hover:bg-muted/60 disabled:opacity-50',
                    i.candidate && 'border-amber-300 bg-amber-50/60'
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{i.title}</span>
                      {i.candidate && (
                        <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                          Likely candidate
                        </span>
                      )}
                    </div>
                    {i.subtitle && <div className="text-muted-foreground truncate text-xs">{i.subtitle}</div>}
                    {i.meta && <div className="text-muted-foreground truncate text-xs">{i.meta}</div>}
                  </div>
                  <Link2 className={cn('h-4 w-4 shrink-0 opacity-60', busyKey === i.key && 'animate-pulse')} />
                </button>
              ))}

              {lookupShopify && (searching || extraResults.length > 0) && (
                <div className="mt-2 flex items-center gap-2 border-t pt-2">
                  <span className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
                    Anywhere in Shopify
                  </span>
                  {searching && <Loader2 className="text-muted-foreground h-3 w-3 animate-spin" />}
                </div>
              )}
              {lookupShopify &&
                extraResults.map((r) => (
                  <button
                    key={`lookup-${r.order_id}`}
                    type="button"
                    disabled={busyKey !== null}
                    onClick={() => setConfirming(r)}
                    className="flex items-center gap-3 rounded-md border border-dashed px-3 py-2 text-left transition-colors hover:bg-muted/60 disabled:opacity-50"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{r.order_name ?? `Order ${r.order_id}`}</div>
                      <div className="text-muted-foreground truncate text-xs">
                        {[r.customer_name, r.customer_email].filter(Boolean).join(' · ') || 'No customer details'}
                      </div>
                      <div className="text-muted-foreground truncate text-xs">
                        {[
                          r.created_at ? r.created_at.slice(0, 10) : null,
                          `${r.line_items.length} line item${r.line_items.length === 1 ? '' : 's'}`,
                          r.shopify_expected_date ? `ETA ${r.shopify_expected_date}` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    </div>
                    <Search className="h-4 w-4 shrink-0 opacity-60" />
                  </button>
                ))}
            </div>
            {footerAction && (
              <Button
                variant="outline"
                size="sm"
                disabled={busyKey !== null}
                onClick={async () => {
                  setBusyKey('__footer__')
                  try {
                    await footerAction.onClick()
                    onOpenChange(false)
                  } finally {
                    setBusyKey(null)
                  }
                }}
              >
                {footerAction.label}
              </Button>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

// Header-line match controls for an LS row: unlink when matched, resolve when ambiguous,
// link when unmatched. All three funnel through the same persisted-override endpoints.
export function LsMatchControls({
  order,
  unmatchedShopify,
  actions,
}: {
  order: SpecialOrder
  unmatchedShopify: ShopifyOnlyOrder[]
  actions: MatchActions
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const soId = String(order.special_order_id)

  if (order.shopify_match === 'matched' && order.shopify_order_id) {
    return (
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        title={`Unlink Shopify order ${order.shopify_order_name ?? ''} from this SO`}
        className="h-6 shrink-0 gap-1 px-1.5 text-[11px] text-muted-foreground"
        onClick={async () => {
          setBusy(true)
          try {
            await actions.onUnmatch(soId, order.shopify_order_id!)
          } finally {
            setBusy(false)
          }
        }}
      >
        <Unlink className="h-3 w-3" />
        Unlink
      </Button>
    )
  }

  // Ambiguous: resolve among candidates (plus any other unmatched order). Unmatched ('none'):
  // free link against the unmatched population. (`?? []` tolerates a payload from a backend
  // that predates the candidates field, e.g. mid-deploy.)
  const shopifyCandidates = order.shopify_candidates ?? []
  const candidateIds = new Set(shopifyCandidates.map((c) => c.order_id))
  const items: PickerItem[] = [
    ...shopifyCandidates.map((c) => ({
      key: c.order_id,
      title: c.order_name ?? `Order ${c.order_id}`,
      subtitle: c.customer_email,
      meta: c.shopify_expected_date ? `ETA ${c.shopify_expected_date}` : null,
      candidate: true,
    })),
    ...unmatchedShopify
      .filter((u) => !candidateIds.has(u.order_id))
      .map((u) => ({
        key: u.order_id,
        title: u.order_name ?? `Order ${u.order_id}`,
        subtitle: [u.customer_email, u.skus.join(', ')].filter(Boolean).join(' · ') || null,
        meta: u.shopify_expected_date ? `ETA ${u.shopify_expected_date}` : null,
      })),
  ]
  // No `items.length === 0` bail-out: the dialog can now search all of Shopify, so linking
  // stays available even when the unmatched population offers nothing.

  const ambiguous = order.shopify_match === 'ambiguous'
  return (
    <>
      <Button
        variant={ambiguous ? 'outline' : 'ghost'}
        size="sm"
        className={cn('h-6 shrink-0 gap-1 px-1.5 text-[11px]', !ambiguous && 'text-muted-foreground')}
        onClick={() => setOpen(true)}
      >
        <Link2 className="h-3 w-3" />
        {ambiguous ? 'Resolve…' : 'Link Shopify…'}
      </Button>
      <MatchPickerDialog
        open={open}
        onOpenChange={setOpen}
        title={`Link SO #${soId} to a Shopify order`}
        description={
          ambiguous
            ? 'Several Shopify orders could be this special order — pick the right one, or search Shopify for another. The link is remembered.'
            : 'Pick the Shopify order this special order fulfils, or type any order number to search all of Shopify. The link is remembered.'
        }
        items={items}
        lookupShopify
        onPick={(shopifyOrderId) => actions.onMatch(soId, shopifyOrderId)}
        footerAction={
          ambiguous
            ? {
                label: 'None of these — stop suggesting them',
                onClick: async () => {
                  for (const c of shopifyCandidates) {
                    await actions.onUnmatch(soId, c.order_id)
                  }
                },
              }
            : undefined
        }
      />
    </>
  )
}

// The service bench's notes on the attached workorder, opened from the Workorder column.
// These are the only place the reason a part is on order is usually written down, so the
// buyer sees them without switching to Lightspeed.
export function WorkorderNotesDialog({
  order,
  open,
  onOpenChange,
}: {
  order: SpecialOrder
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const notes: { label: string; value: string | null }[] = [
    { label: 'Workorder note', value: order.workorder_note },
    { label: 'Internal note', value: order.workorder_internal_note },
    { label: 'Hook-in tag', value: order.workorder_hook_in },
  ]
  const hasNotes = notes.some((n) => n.value)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-cyan-600" />
            Workorder #{order.workorder_id}
          </DialogTitle>
          <DialogDescription>
            {[
              order.workorder_status,
              order.workorder_time_in ? `In ${order.workorder_time_in.slice(0, 10)}` : null,
              order.workorder_eta_out ? `ETA out ${order.workorder_eta_out.slice(0, 10)}` : null,
            ]
              .filter(Boolean)
              .join(' · ') || 'Service workorder attached to this special order.'}
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-80 flex-col gap-3 overflow-y-auto pr-1">
          {!hasNotes && (
            <div className="text-muted-foreground py-6 text-center text-sm">
              This workorder has no notes.
            </div>
          )}
          {notes
            .filter((n) => n.value)
            .map((n) => (
              <div key={n.label} className="flex flex-col gap-1">
                <span className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
                  {n.label}
                </span>
                <p className="whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2 text-sm">{n.value}</p>
              </div>
            ))}
        </div>

        {order.workorder_url && (
          <Button variant="outline" size="sm" className="gap-2" asChild>
            <a href={order.workorder_url} target="_blank" rel="noopener noreferrer">
              <Wrench className="h-3.5 w-3.5" />
              Open workorder in Lightspeed
              <ExternalLink className="ml-auto h-3 w-3 opacity-60" />
            </a>
          </Button>
        )}
      </DialogContent>
    </Dialog>
  )
}

// The Workorder column, present on every SO tile so "no bench job" reads as clearly as an
// attached one. The WO number opens the bench's notes; a • marks that there are some.

// The states an order can be in that make it a surprising thing to link — surfaced as chips in
// the confirmation panel so nobody links a cancelled or already-fulfilled order by accident.
export function WorkorderFields({ order }: { order: SpecialOrder }) {
  const [notesOpen, setNotesOpen] = useState(false)
  const hasNotes = Boolean(order.workorder_note || order.workorder_internal_note || order.workorder_hook_in)

  if (!order.workorder_id) {
    return (
      <FieldGroup title="Workorder">
        <Field label="WO #" value={null} />
        <Field label="Status" value={null} />
      </FieldGroup>
    )
  }

  return (
    <FieldGroup title="Workorder">
      <Field
        label="WO #"
        value={
          <button
            type="button"
            onClick={() => setNotesOpen(true)}
            title={hasNotes ? 'View workorder notes' : 'No notes on this workorder'}
            className="inline-flex max-w-full items-center gap-1 text-left font-medium text-cyan-700 hover:underline"
          >
            <Wrench className="h-3 w-3 shrink-0" />
            <span className="truncate">#{order.workorder_id}</span>
            {hasNotes && <span className="shrink-0 text-cyan-600">•</span>}
          </button>
        }
      />
      <Field label="Status" value={order.workorder_status} />
      <WorkorderNotesDialog order={order} open={notesOpen} onOpenChange={setNotesOpen} />
    </FieldGroup>
  )
}


// A Shopify-only ("Unmatched" / "Possible match") pseudo-row — full-width horizontal row.
