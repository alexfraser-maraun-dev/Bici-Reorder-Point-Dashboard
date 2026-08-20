'use client'

/** Presentational primitives for the special-order rows.
 *
 *  Split out of special-orders-grid.tsx, which had grown to 1,317 lines across 15 components.
 *  Nothing here knows about matching, the SLA, or data fetching — they are pure display parts,
 *  with the one exception of EditableEta, which owns the Shopify ETA write because the commit
 *  semantics (blur/Enter commits, Escape reverts, an emptied box is an abandoned edit rather
 *  than a clear) only make sense next to the input itself.
 */

import { useState, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { updateShopifyEta } from '@/lib/hooks'
import type { SpecialOrder, AvailableVendor } from '@/lib/types'
import { ExternalLink, Copy, Check, X, Package } from 'lucide-react'
import { ShopifyMatchBadge } from './special-order-badges'

export function CopyableUpc({ upc }: { upc: string }) {
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
export function AvailableVendors({ vendors }: { vendors: AvailableVendor[] }) {
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
export function Field({ label, value }: { label: string; value: React.ReactNode }) {
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
export function EditableEta({
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
export function FieldGroup({
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

export function LightspeedLink({ url, label, icon: Icon }: { url: string | null; label: string; icon: typeof Package }) {
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
