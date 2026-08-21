'use client'

/** Presentational primitives for the special-order rows.
 *
 *  Split out of special-orders-grid.tsx, which had grown to 1,317 lines across 15 components.
 *  Nothing here knows about matching, the SLA, or data fetching — they are pure display parts,
 *  with the one exception of EditableEta, which owns the Shopify ETA write because its explicit
 *  save/cancel/clear semantics only make sense next to the input itself.
 */

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import { updateServicePromise, updateShopifyEta } from '@/lib/hooks'
import type { AvailableVendor } from '@/lib/types'
import { Copy, Check, X } from 'lucide-react'
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
      className="inline-flex min-h-8 max-w-full items-center gap-1.5 rounded px-1 text-left hover:bg-muted/50 hover:text-foreground"
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
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="truncate text-sm">{value ?? '—'}</span>
    </div>
  )
}

// Sanity bounds for a customer-promised ETA year. A native date input emits *complete*
// intermediate values while the year segment is being typed ("0002-07-13"), so anything
// outside this window is treated as still-in-progress input, never saved.
const ETA_YEAR_MIN = 2020
const ETA_YEAR_MAX = 2040

// The Shopify ETA, rendered as an always-editable native date box. Typing only changes a local
// draft: Save is the sole commit path, Cancel/Escape reverts, and clearing an existing customer
// promise requires a review dialog. When the order has no Shopify id to attach the metafield to
// (an unmatched LS SO) it renders read-only.
function PromiseDateEditor({
  editorId,
  value,
  inputLabel,
  successLabel,
  clearTitle,
  clearDescription,
  onCommit,
  onSaved,
  trailing,
  auditText,
}: {
  editorId: string
  value: string | null
  inputLabel: string
  successLabel: string
  clearTitle: string
  clearDescription: (savedDate: string) => React.ReactNode
  onCommit: (next: string | null) => Promise<unknown>
  onSaved?: () => void | Promise<void>
  trailing?: React.ReactNode
  auditText?: string | null
}) {
  const [val, setVal] = useState<string>(value ?? '')
  const [saving, setSaving] = useState(false)
  const [clearOpen, setClearOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [savedVal, setSavedVal] = useState<string>(value ?? '')

  const dirty = val !== savedVal

  // next === null clears the ETA; a string must be a complete, sane date to be written.
  // Returns whether the write succeeded so the review dialog stays open after a failure.
  const commit = async (next: string | null): Promise<boolean> => {
    setErrorMessage(null)
    if (next !== null) {
      if (next.length !== 10) {
        setErrorMessage('Enter a complete date before saving.')
        return false
      }
      const year = Number(next.slice(0, 4))
      if (year < ETA_YEAR_MIN || year > ETA_YEAR_MAX) {
        setErrorMessage(`ETA year ${year} looks wrong. Check the date and try again.`)
        return false
      }
      if (next === savedVal) return true
    } else if (savedVal === '') {
      setVal('')
      return true // nothing to clear
    }
    setSaving(true)
    try {
      await onCommit(next)
      setSavedVal(next ?? '')
      setVal(next ?? '')
      toast.success(next ? `${successLabel} updated.` : `${successLabel} cleared.`)
      try {
        await onSaved?.()
      } catch {
        toast.warning(`${successLabel} was saved, but the dashboard could not refresh. Refresh it manually.`)
      }
      return true
    } catch (err) {
      const message = err instanceof Error ? err.message : `Failed to update ${successLabel.toLowerCase()}.`
      setErrorMessage(`${message} Your draft is still here; try again.`)
      toast.error(message)
      return false
    } finally {
      setSaving(false)
    }
  }

  const cancelEdit = () => {
    setVal(savedVal)
    setErrorMessage(null)
  }

  const saveDraft = () => {
    if (!dirty || saving) return
    if (val === '' && savedVal !== '') {
      setClearOpen(true)
      return
    }
    void commit(val)
  }

  const confirmClear = async () => {
    const cleared = await commit(null)
    if (cleared) setClearOpen(false)
  }

  const statusId = `${editorId}-status`

  return (
    <span className="flex min-w-0 flex-col gap-1.5">
      <span className="flex flex-wrap items-center gap-1.5">
        <Input
          type="date"
          value={val}
          onChange={(e) => {
            setVal(e.target.value)
            setErrorMessage(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              saveDraft()
            }
            if (e.key === 'Escape') {
              e.preventDefault()
              cancelEdit()
            }
          }}
          disabled={saving}
          aria-label={inputLabel}
          aria-describedby={statusId}
          aria-invalid={Boolean(errorMessage)}
          className="h-9 w-[9.75rem] px-2 text-sm"
        />
        {dirty && (
          <>
            <Button
              type="button"
              size="sm"
              className="min-h-9 px-2.5 text-xs"
              disabled={saving}
              onClick={saveDraft}
            >
              {saving ? 'Saving…' : val === '' ? 'Review clear' : 'Save'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="min-h-9 px-2.5 text-xs"
              disabled={saving}
              onClick={cancelEdit}
            >
              Cancel
            </Button>
          </>
        )}
        {savedVal !== '' && !dirty && !saving && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Clear ${inputLabel} ${savedVal}`}
            onClick={() => setClearOpen(true)}
            className="min-h-9 gap-1 px-2 text-xs text-muted-foreground"
          >
            <X className="h-3.5 w-3.5" />
            Clear
          </Button>
        )}
        {trailing}
      </span>
      <span id={statusId} aria-live="polite" className={cn('text-xs', errorMessage ? 'text-red-600' : 'text-muted-foreground')}>
        {errorMessage ?? (saving ? `Saving ${successLabel.toLowerCase()}…` : dirty ? 'Unsaved change' : auditText ?? '')}
      </span>

      <AlertDialog open={clearOpen} onOpenChange={(next) => { if (!saving) setClearOpen(next) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{clearTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {clearDescription(savedVal)}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {errorMessage && (
            <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {errorMessage}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Keep promise</AlertDialogCancel>
            <AlertDialogAction
              disabled={saving}
              className="bg-red-600 text-white hover:bg-red-700"
              onClick={(e) => {
                e.preventDefault()
                void confirmClear()
              }}
            >
              {saving ? 'Clearing…' : 'Clear promise'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </span>
  )
}

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
  if (!orderId) {
    return (
      <span className="text-muted-foreground" title="No matched Shopify order to write the ETA to.">
        {value ?? '—'}
      </span>
    )
  }

  return (
    <PromiseDateEditor
      key={`${orderId}:${value ?? ''}`}
      editorId={`shopify-eta-${orderId}`}
      value={value}
      inputLabel="Customer promise date in Shopify"
      successLabel="Shopify customer promise"
      clearTitle="Clear the customer promise?"
      clearDescription={(savedDate) => (
        <>
          This removes {savedDate ? `the ${savedDate} promise` : 'the promise date'} from Shopify.
          It also removes the date used to judge whether this special order is on time. Only clear
          it when the promise itself is no longer valid.
        </>
      )}
      onCommit={(next) => updateShopifyEta({ shopify_order_id: orderId, eta: next })}
      onSaved={onSaved}
      trailing={ambiguous ? <ShopifyMatchBadge match="ambiguous" /> : undefined}
    />
  )
}

// A service workorder's parts promise is app-owned and deliberately separate from etaOut, which
// describes the bike's planned completion. The audit caption makes that boundary visible and
// identifies the last person who changed the promise.
export function EditableServicePromise({
  specialOrderId,
  value,
  recordedAt,
  recordedBy,
  onSaved,
}: {
  specialOrderId: string
  value: string | null
  recordedAt?: string | null
  recordedBy?: string | null
  onSaved?: () => void | Promise<void>
}) {
  const auditText = value && (recordedBy || recordedAt)
    ? [recordedBy ? `Recorded by ${recordedBy}` : null, recordedAt ? `on ${recordedAt.slice(0, 10)}` : null]
        .filter(Boolean)
        .join(' ')
    : 'Saved in this tool; the workorder ETA-out is not used as the parts promise.'

  return (
    <PromiseDateEditor
      key={`${specialOrderId}:${value ?? ''}:${recordedAt ?? ''}`}
      editorId={`service-promise-${specialOrderId}`}
      value={value}
      inputLabel="Service parts promise date"
      successLabel="Service parts promise"
      clearTitle="Clear the service parts promise?"
      clearDescription={(savedDate) => (
        <>
          This removes {savedDate ? `the ${savedDate} parts promise` : 'the parts promise'} recorded
          in this tool. It also removes the date used to judge whether this service special order is
          on time. It does not change the workorder ETA-out in Lightspeed.
        </>
      )}
      onCommit={(next) => updateServicePromise(specialOrderId, next)}
      onSaved={onSaved}
      auditText={auditText}
    />
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
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</span>
      <div className={cn('gap-x-6 gap-y-2.5', cols === 2 ? 'grid grid-cols-2' : 'flex flex-col')}>
        {children}
      </div>
    </div>
  )
}
