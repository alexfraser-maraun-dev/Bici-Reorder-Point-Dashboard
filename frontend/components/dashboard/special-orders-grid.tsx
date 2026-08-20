'use client'

import { useState, useMemo, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { updateShopifyEta, lookupShopifyOrders } from '@/lib/hooks'
import type {
  SpecialOrder,
  ShopifyOnlyOrder,
  AvailableVendor,
  ShopifyOrderLookup,
} from '@/lib/types'
import {
  StageBadge,
  SourceBadge,
  SeverityBadge,
  FlagBadge,
  ShopifyMatchBadge,
} from './special-order-badges'
import { SoAckMenu, EscalationBadge } from './so-ack-menu'
import {
  ExternalLink,
  Package,
  User,
  FileText,
  Store,
  ArrowDownNarrowWide,
  ArrowUpNarrowWide,
  Copy,
  Check,
  Link2,
  Unlink,
  Wrench,
  X,
  ArrowLeft,
  Loader2,
  Search,
  AlertTriangle,
} from 'lucide-react'

// Manual match/unmatch plumbing threaded down from the page: both resolve once the backend
// has persisted the override and rebuilt its cache (the page then revalidates).
export interface MatchActions {
  onMatch: (specialOrderId: string, shopifyOrderId: string) => Promise<void>
  onUnmatch: (specialOrderId: string, shopifyOrderId: string) => Promise<void>
}

type SortKey =
  | 'sla_severity_rank'
  | 'special_order_id'
  | 'customer_name'
  | 'description'
  | 'vendor_name'
  | 'store'
  | 'order_id'
  | 'ordered_date'
  | 'expected_date'
  | 'shopify_expected_date'
  | 'created_date'
  | 'procurement_stage_index'
  | 'flag'

type SortDir = 'asc' | 'desc'

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  // Default: the SLA verdict, worst first. This is the order a buyer should work the queue in.
  { key: 'sla_severity_rank', label: 'SLA severity' },
  { key: 'flag', label: 'Flag / priority' },
  { key: 'created_date', label: 'Created date' },
  { key: 'expected_date', label: 'LS PO ETA' },
  { key: 'shopify_expected_date', label: 'Shopify ETA' },
  { key: 'ordered_date', label: 'Ordered date' },
  { key: 'procurement_stage_index', label: 'Stage' },
  { key: 'customer_name', label: 'Customer' },
  { key: 'description', label: 'Product' },
  { key: 'vendor_name', label: 'Vendor' },
  { key: 'store', label: 'Store' },
  { key: 'order_id', label: 'PO #' },
  { key: 'special_order_id', label: 'SO #' },
]

// Mirrors the backend's _FLAG_RANK so "Flag / priority" sorts by severity, not alphabetically.
const FLAG_SORT_RANK: Record<SpecialOrder['flag'], number> = {
  critical: 6,
  overdue_mid: 5,
  overdue: 4,
  no_eta: 3,
  ready_not_called: 1,
  none: 0,
}

function compare(a: SpecialOrder, b: SpecialOrder, key: SortKey, dir: SortDir): number {
  const av: unknown = key === 'flag' ? FLAG_SORT_RANK[a.flag] : a[key]
  const bv: unknown = key === 'flag' ? FLAG_SORT_RANK[b.flag] : b[key]

  // Rows with no value always sink to the bottom, whichever direction is picked.
  const aNull = av === null || av === undefined || av === ''
  const bNull = bv === null || bv === undefined || bv === ''
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1

  const result =
    typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv))
  return dir === 'asc' ? result : -result
}

// Compact label-over-value cell used across the horizontal field grid.
// The item UPC, rendered as a one-click-copy chip. Users paste it into a vendor B2B site to
// research the product, so the copy affordance is the whole point of surfacing it here.
function CopyableUpc({ upc }: { upc: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(upc)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Could not copy UPC.')
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title="Copy UPC"
      className="inline-flex max-w-full items-center gap-1.5 text-left hover:text-foreground"
    >
      <span className="truncate font-mono text-xs">{upc}</span>
      {copied ? (
        <Check className="size-3 shrink-0 text-green-600" />
      ) : (
        <Copy className="text-muted-foreground size-3 shrink-0" />
      )}
    </button>
  )
}

// The brand-level "Available from" vendors, fastest lead time first. The first chip (fastest)
// is highlighted green so the best sourcing option reads at a glance. A "~" prefix marks a lead
// time that's the vendor's cross-store median (no sample at this SO's own store).
function AvailableVendors({ vendors }: { vendors: AvailableVendor[] }) {
  if (!vendors.length) return null
  return (
    <div className="flex flex-wrap gap-1">
      {vendors.map((v, i) => (
        <span
          key={v.vendor_id}
          title={v.lead_time_source === 'vendor_median' ? 'Median lead time across stores' : undefined}
          className={cn(
            'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs',
            i === 0 ? 'border-green-300 bg-green-50 text-green-800' : 'border-border bg-muted/40',
          )}
        >
          <span className="font-medium">{v.vendor_name}</span>
          {v.lead_time_days !== null && (
            <span className="tabular-nums opacity-80">
              {v.lead_time_source === 'vendor_median' ? '~' : ''}
              {v.lead_time_days}d
            </span>
          )}
        </span>
      ))}
    </div>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-muted-foreground text-[10px] font-medium uppercase tracking-wide">{label}</span>
      <span className="truncate text-sm">{value ?? '—'}</span>
    </div>
  )
}

// Sanity bounds for a customer-promised ETA year. A native date input emits *complete*
// intermediate values while the year segment is being typed ("0002-07-13"), so anything
// outside this window is treated as still-in-progress input, never saved.
const ETA_YEAR_MIN = 2020
const ETA_YEAR_MAX = 2040

// The Shopify ETA, rendered as an always-editable native date box. Edits are committed on
// blur / Enter (NOT on every change — see ETA_YEAR bounds above), written straight back to
// the Shopify order metafield, then onSaved (a dashboard revalidate) pulls the live value.
// Escape reverts; the ✕ clears the ETA (deletes the metafield). When the order has no
// Shopify id to attach the metafield to (an unmatched LS SO) it renders read-only.
function EditableEta({
  orderId,
  value,
  ambiguous,
  onSaved,
}: {
  orderId: string | null
  value: string | null
  ambiguous?: boolean
  onSaved?: () => void | Promise<void>
}) {
  const [val, setVal] = useState<string>(value ?? '')
  const [saving, setSaving] = useState(false)
  // The last value we know is persisted in Shopify, so we only save real changes (and can
  // revert to it if a save fails).
  const savedRef = useRef<string>(value ?? '')

  // Keep the box in sync when the upstream value changes (e.g. after a refetch) while idle.
  useEffect(() => {
    if (!saving) {
      setVal(value ?? '')
      savedRef.current = value ?? ''
    }
  }, [value, saving])

  if (!orderId) {
    return (
      <span className="text-muted-foreground" title="No matched Shopify order to write the ETA to.">
        {value ?? '—'}
      </span>
    )
  }

  // next === null clears the ETA; a string must be a complete, sane date to be written.
  const commit = async (next: string | null) => {
    if (next !== null) {
      if (next.length !== 10) {
        setVal(savedRef.current)
        return
      }
      const year = Number(next.slice(0, 4))
      if (year < ETA_YEAR_MIN || year > ETA_YEAR_MAX) {
        toast.error(`ETA year ${year} looks wrong — not saved.`)
        setVal(savedRef.current)
        return
      }
      if (next === savedRef.current) return
    } else if (savedRef.current === '') {
      return // nothing to clear
    }
    setSaving(true)
    try {
      await updateShopifyEta({ shopify_order_id: orderId, eta: next })
      savedRef.current = next ?? ''
      setVal(next ?? '')
      toast.success(next ? 'Shopify ETA updated.' : 'Shopify ETA cleared.')
      await onSaved?.()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update ETA.')
      setVal(savedRef.current) // revert on failure
    } finally {
      setSaving(false)
    }
  }

  return (
    <span className="flex items-center gap-1.5">
      <Input
        type="date"
        value={val}
        onChange={(e) => setVal(e.target.value)} // draft only — nothing hits the network here
        onBlur={() => {
          // An emptied box on blur is treated as "abandoned edit", not a clear — clearing
          // is the explicit ✕ so a stray keyboard Delete can't silently drop the promise date.
          if (val === '') setVal(savedRef.current)
          else void commit(val)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') setVal(savedRef.current)
        }}
        disabled={saving}
        aria-label="Shopify ETA"
        className="h-7 w-[9.5rem] px-2 text-sm"
      />
      {savedRef.current !== '' && !saving && (
        <button
          type="button"
          title="Clear ETA"
          aria-label="Clear Shopify ETA"
          onClick={() => void commit(null)}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
      {ambiguous && <ShopifyMatchBadge match="ambiguous" />}
    </span>
  )
}

// A captioned cluster of related fields, so the row reads as logical groups rather than a
// flat scatter of cells. `cols` lays the group's fields out internally (1 col by default).
function FieldGroup({
  title,
  cols = 1,
  className,
  children,
}: {
  title: string
  cols?: 1 | 2
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-2', className)}>
      <span className="text-muted-foreground/70 text-[10px] font-semibold uppercase tracking-wider">{title}</span>
      <div className={cn('gap-x-6 gap-y-2.5', cols === 2 ? 'grid grid-cols-2' : 'flex flex-col')}>
        {children}
      </div>
    </div>
  )
}

function LightspeedLink({ url, label, icon: Icon }: { url: string | null; label: string; icon: typeof Package }) {
  if (!url) return null
  return (
    <Button variant="outline" size="sm" className="h-7 w-full justify-start gap-2 px-2" asChild>
      <a href={url} target="_blank" rel="noopener noreferrer">
        <Icon className="h-3.5 w-3.5" />
        <span className="text-xs">{label}</span>
        <ExternalLink className="ml-auto h-3 w-3 opacity-60" />
      </a>
    </Button>
  )
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

// The states an order can be in that make it a surprising thing to link — surfaced as chips in
// the confirmation panel so nobody links a cancelled or already-fulfilled order by accident.
function OrderStateChips({ order }: { order: ShopifyOrderLookup }) {
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
function LookupConfirmation({
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
function MatchPickerDialog({
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
function LsMatchControls({
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
function WorkorderNotesDialog({
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
function WorkorderFields({ order }: { order: SpecialOrder }) {
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

// The flag accent stripe colour (left edge), mirroring the old row background coding.
const ACCENT: Partial<Record<SpecialOrder['flag'], string>> = {
  overdue: 'bg-red-300',
  overdue_mid: 'bg-red-500',
  critical: 'bg-red-600',
}

// A Shopify-only ("Unmatched" / "Possible match") pseudo-row — full-width horizontal row.
function ShopifyOnlyRow({
  order,
  onEtaSaved,
  lsUnmatched,
  actions,
}: {
  order: SpecialOrder
  onEtaSaved?: () => void | Promise<void>
  lsUnmatched: SpecialOrder[]
  actions?: MatchActions
}) {
  const [linkOpen, setLinkOpen] = useState(false)
  const possible = order.ambiguous_candidate === true
  return (
    <Card className="flex-row gap-0 overflow-hidden p-0">
      <div className={cn('w-1 shrink-0 self-stretch', possible ? 'bg-amber-400' : 'bg-violet-400')} />
      <div className="flex min-w-0 flex-1 flex-col gap-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">{order.shopify_order_name ?? order.special_order_id}</span>
          <StageBadge stage="shopify" />
          <ShopifyMatchBadge match="none" possible={possible} />
          {actions && lsUnmatched.length > 0 && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground"
                onClick={() => setLinkOpen(true)}
              >
                <Link2 className="h-3 w-3" />
                Link to SO…
              </Button>
              <MatchPickerDialog
                open={linkOpen}
                onOpenChange={setLinkOpen}
                title={`Link ${order.shopify_order_name ?? 'this Shopify order'} to an LS special order`}
                description="Pick the Lightspeed special order that fulfils this Shopify order. The link is remembered."
                items={lsUnmatched.map((o) => ({
                  key: String(o.special_order_id),
                  title: `SO #${o.special_order_id}${o.description ? ` — ${o.description}` : ''}`,
                  subtitle: [o.customer_name, o.customer_email].filter(Boolean).join(' · ') || null,
                  meta: [o.system_sku, o.store].filter(Boolean).join(' · ') || null,
                }))}
                onPick={(soId) => actions.onMatch(soId, order.shopify_order_id!)}
              />
            </>
          )}
        </div>
        <div className="grid grid-cols-2 gap-x-5 gap-y-2 sm:grid-cols-4">
          <Field label="Customer" value={order.customer_email} />
          <Field
            label="Shopify ETA"
            value={
              <EditableEta
                orderId={order.shopify_order_id}
                value={order.shopify_expected_date}
                onSaved={onEtaSaved}
              />
            }
          />
          <Field
            label="SKU(s)"
            value={order.description ? <span className="font-mono text-xs">{order.description}</span> : null}
          />
          <Field label="Created" value={order.created_date} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col justify-center gap-2 border-l px-3 py-3 sm:w-44">
        <span className="text-muted-foreground text-[10px] font-medium uppercase tracking-wide">Open in Shopify</span>
        {order.shopify_order_url ? (
          <LightspeedLink url={order.shopify_order_url} label={order.shopify_order_name ?? 'Shopify order'} icon={Store} />
        ) : (
          <span className="text-muted-foreground text-sm">{order.shopify_order_name ?? '—'}</span>
        )}
      </div>
    </Card>
  )
}

function SpecialOrderRow({
  order,
  onEtaSaved,
  lsUnmatched,
  unmatchedShopify,
  actions,
}: {
  order: SpecialOrder
  onEtaSaved?: () => void | Promise<void>
  lsUnmatched: SpecialOrder[]
  unmatchedShopify: ShopifyOnlyOrder[]
  actions?: MatchActions
}) {
  if (order.kind === 'shopify')
    return <ShopifyOnlyRow order={order} onEtaSaved={onEtaSaved} lsUnmatched={lsUnmatched} actions={actions} />

  const hasShopify = order.shopify_match === 'matched' || order.shopify_match === 'ambiguous'

  return (
    <Card className="flex-row gap-0 overflow-hidden p-0">
      {/* Flag accent (left edge) */}
      <div className={cn('w-1 shrink-0 self-stretch', ACCENT[order.flag] ?? 'bg-border')} />

      {/* Main content */}
      <div className="flex min-w-0 flex-1 flex-col gap-3 px-4 py-3">
        {/* Header line: identity + product + badges + Shopify indicator. (The workorder now
            has its own always-present column below, so no chip here.) */}
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-sm font-medium">SO #{order.special_order_id}</span>
          <StageBadge stage={order.procurement_stage} />
          {/* Where this SO derives from: workorder, Shopify, or neither. Always shown --
              "Unattributed" is a bucket to chase, not a blank to hide. */}
          <SourceBadge source={order.source} />
          {/* The SLA verdict. Muted while parked, so an acknowledged breach stays visible as
              context without competing with the rows that still need action. */}
          <SeverityBadge severity={order.sla_severity} muted={order.ack_active} />
          <EscalationBadge level={order.escalation_level} />
          <FlagBadge stage={order.procurement_stage} flag={order.flag} daysOverdue={order.days_overdue} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium" title={order.description ?? ''}>
            {order.description ?? 'Special order'}
          </span>
          {hasShopify &&
            (order.shopify_order_url ? (
              <a
                href={order.shopify_order_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex shrink-0 items-center gap-1.5"
                title={`Shopify order ${order.shopify_order_name ?? ''}`}
              >
                <ShopifyMatchBadge match={order.shopify_match} basis={order.shopify_match_basis} />
                {order.shopify_order_name && (
                  <span className="font-mono text-xs text-blue-600 underline">{order.shopify_order_name}</span>
                )}
              </a>
            ) : (
              <span className="shrink-0">
                <ShopifyMatchBadge match={order.shopify_match} basis={order.shopify_match_basis} />
              </span>
            ))}
          {actions && <LsMatchControls order={order} unmatchedShopify={unmatchedShopify} actions={actions} />}
        </div>

        {/* Fields grouped into logical clusters that read left-to-right:
            who/what → sourcing PO → when (all dates together) → how late.
            A full-width grid spreads the groups evenly across the available room. */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-7">
          <FieldGroup title="Customer">
            <Field label="Customer" value={order.customer_name} />
            <Field label="Phone" value={order.customer_phone} />
            <Field label="Store" value={order.store} />
          </FieldGroup>

          <FieldGroup title="Item">
            <Field
              label="SKU"
              value={order.system_sku ? <span className="font-mono text-xs">{order.system_sku}</span> : null}
            />
            <Field label="UPC" value={order.upc ? <CopyableUpc upc={order.upc} /> : null} />
            <Field label="Vendor" value={order.vendor_name} />
            <Field label="Quantity" value={order.unit_quantity} />
          </FieldGroup>

          <FieldGroup title="Purchase order">
            <Field label="PO #" value={order.order_id} />
            <Field
              label="Receiving"
              value={order.po_complete ? 'Complete' : order.received_started ? 'Started' : 'Not started'}
            />
            <Field label="Order type" value={order.order_type} />
          </FieldGroup>

          <WorkorderFields order={order} />

          <FieldGroup title="Dates" cols={2} className="col-span-2">
            <Field label="SO created" value={order.created_date} />
            <Field label="Ordered" value={order.ordered_date} />
            <Field label="Expected (PO)" value={order.expected_date} />
            <Field
              label="Shopify ETA"
              value={
                <EditableEta
                  orderId={order.shopify_order_id}
                  value={order.shopify_expected_date}
                  ambiguous={order.shopify_match === 'ambiguous'}
                  onSaved={onEtaSaved}
                />
              }
            />
          </FieldGroup>

          <FieldGroup title="Aging">
            <Field
              label="Days open"
              value={
                order.days_since_creation !== null ? (
                  <span className={cn(order.is_overdue && 'font-semibold text-red-600')}>
                    {order.days_since_creation}
                  </span>
                ) : null
              }
            />
            <Field
              label="Days overdue"
              value={
                order.days_overdue !== null && order.days_overdue > 0 ? (
                  <span className="font-semibold text-red-600">{order.days_overdue}</span>
                ) : (
                  order.days_overdue ?? '—'
                )
              }
            />
          </FieldGroup>
        </div>

        {/* The SLA line: the backward-schedule arithmetic in plain English, plus the Park
            control. Only rendered when there is something to say — a reason line on every
            healthy row would bury the ~37 that need action among ~230. */}
        {(order.actionable || order.ack_active) && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2.5">
            <span className="text-muted-foreground/70 shrink-0 text-[10px] font-semibold uppercase tracking-wider">
              SLA
            </span>
            <span className="min-w-0 flex-1 text-xs text-muted-foreground">{order.sla_reason}</span>
            {order.order_by_date && (
              <span className="shrink-0 font-mono text-xs" title="Latest date this could be ordered and still meet the promise">
                order by {order.order_by_date}
                {order.slack_days !== null && (
                  <span className={cn('ml-1 font-medium',
                    order.slack_days < 0 ? 'text-red-600' : order.slack_days <= 3 ? 'text-amber-600' : 'text-emerald-600')}>
                    ({order.slack_days >= 0 ? `${order.slack_days}d slack` : `${Math.abs(order.slack_days)}d late`})
                  </span>
                )}
              </span>
            )}
            <SoAckMenu order={order} onDone={() => { void onEtaSaved?.() }} />
          </div>
        )}

        {/* Brand-level sourcing options: which vendors carry this SKU's brand and how fast each
            is to this store. Full-width so the (variable-length) vendor chips have room to wrap. */}
        {order.available_vendors.length > 0 && (
          <div className="flex min-w-0 flex-wrap items-center gap-2 border-t pt-2.5">
            <span className="text-muted-foreground/70 shrink-0 text-[10px] font-semibold uppercase tracking-wider">
              Available from
            </span>
            <AvailableVendors vendors={order.available_vendors} />
          </div>
        )}
      </div>

      {/* Lightspeed deep links (right edge) */}
      <div className="flex shrink-0 flex-col justify-center gap-1.5 border-l px-3 py-3 sm:w-44">
        <span className="text-muted-foreground text-[10px] font-medium uppercase tracking-wide">Open in Lightspeed</span>
        <LightspeedLink url={order.ls_item_url} label="Product" icon={Package} />
        <LightspeedLink url={order.ls_customer_url} label="Customer" icon={User} />
        <LightspeedLink url={order.ls_order_url} label="Purchase order" icon={FileText} />
        <LightspeedLink url={order.workorder_url} label={`Workorder #${order.workorder_id ?? ''}`} icon={Wrench} />
      </div>
    </Card>
  )
}

interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  // Called after an ETA is written to Shopify, so the parent can refetch the live value.
  onEtaSaved?: () => void | Promise<void>
  // Link targets for the manual match dialogs — the FULL (unfiltered) populations, so a
  // filtered-out row can still be picked as a link target.
  lsUnmatched?: SpecialOrder[]
  unmatchedShopify?: ShopifyOnlyOrder[]
  matchActions?: MatchActions
}

export function SpecialOrdersGrid({
  orders,
  isLoading,
  onEtaSaved,
  lsUnmatched = [],
  unmatchedShopify = [],
  matchActions,
}: Props) {
  // Default to the parent's server-side ordering (flag severity); only re-sort once the user picks.
  const [sortKey, setSortKey] = useState<SortKey | 'default'>('default')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sorted = useMemo(() => {
    if (sortKey === 'default') return orders
    return [...orders].sort((a, b) => compare(a, b, sortKey, sortDir))
  }, [orders, sortKey, sortDir])

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full rounded-xl" />
        ))}
      </div>
    )
  }

  if (orders.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border py-16 text-center text-sm">
        No special orders match the current filters.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Sort control (replaces the table's column-header sorting) */}
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">{sorted.length} orders</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-muted-foreground text-xs">Sort by</span>
          <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey | 'default')}>
            <SelectTrigger className="w-[170px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Default (priority)</SelectItem>
              {SORT_OPTIONS.map((o) => (
                <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            disabled={sortKey === 'default'}
            onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
            title={sortDir === 'asc' ? 'Ascending' : 'Descending'}
          >
            {sortDir === 'asc' ? <ArrowUpNarrowWide className="h-4 w-4" /> : <ArrowDownNarrowWide className="h-4 w-4" />}
            {sortDir === 'asc' ? 'Asc' : 'Desc'}
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        {sorted.map((o) => (
          <SpecialOrderRow
            key={`${o.kind ?? 'ls'}-${o.special_order_id}`}
            order={o}
            onEtaSaved={onEtaSaved}
            lsUnmatched={lsUnmatched}
            unmatchedShopify={unmatchedShopify}
            actions={matchActions}
          />
        ))}
      </div>
    </div>
  )
}
