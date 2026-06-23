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
import { cn } from '@/lib/utils'
import { updateShopifyEta } from '@/lib/hooks'
import type { SpecialOrder } from '@/lib/types'
import {
  StageBadge,
  FlagBadge,
  ShopifyMatchBadge,
} from './special-order-badges'
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
} from 'lucide-react'

type SortKey =
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

function compare(a: SpecialOrder, b: SpecialOrder, key: SortKey, dir: SortDir): number {
  const av = a[key]
  const bv = b[key]

  let result = 0
  if (av === null || av === undefined) result = 1
  else if (bv === null || bv === undefined) result = -1
  else if (typeof av === 'number' && typeof bv === 'number') result = av - bv
  else result = String(av).localeCompare(String(bv))

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

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-muted-foreground text-[10px] font-medium uppercase tracking-wide">{label}</span>
      <span className="truncate text-sm">{value ?? '—'}</span>
    </div>
  )
}

// The Shopify ETA, rendered as an always-editable native date box. Picking a (complete) new
// date writes it straight back to the Shopify order metafield (create or update) and calls
// onSaved (a dashboard refetch) to pull the now-live value. When the order has no Shopify id to
// attach the metafield to (an unmatched LS SO) it renders read-only.
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

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = e.target.value
    setVal(next)
    // Ignore partial/cleared input (native date input emits these mid-edit). No clear support.
    if (next.length !== 10 || next === savedRef.current) return
    setSaving(true)
    try {
      await updateShopifyEta({ shopify_order_id: orderId, eta: next })
      savedRef.current = next
      toast.success('Shopify ETA updated.')
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
        onChange={handleChange}
        disabled={saving}
        aria-label="Shopify ETA"
        className="h-7 w-[9.5rem] px-2 text-sm"
      />
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

// The flag accent stripe colour (left edge), mirroring the old row background coding.
const ACCENT: Partial<Record<SpecialOrder['flag'], string>> = {
  overdue: 'bg-red-300',
  overdue_mid: 'bg-red-500',
  critical: 'bg-red-600',
}

// A Shopify-only ("Unmatched") pseudo-row — full-width horizontal row, simpler content.
function ShopifyOnlyRow({ order, onEtaSaved }: { order: SpecialOrder; onEtaSaved?: () => void | Promise<void> }) {
  return (
    <Card className="flex-row gap-0 overflow-hidden p-0">
      <div className="w-1 shrink-0 self-stretch bg-violet-400" />
      <div className="flex min-w-0 flex-1 flex-col gap-3 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-medium">{order.shopify_order_name ?? order.special_order_id}</span>
          <StageBadge stage="shopify" />
          <ShopifyMatchBadge match="none" />
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

function SpecialOrderRow({ order, onEtaSaved }: { order: SpecialOrder; onEtaSaved?: () => void | Promise<void> }) {
  if (order.kind === 'shopify') return <ShopifyOnlyRow order={order} onEtaSaved={onEtaSaved} />

  const hasShopify = order.shopify_match === 'matched' || order.shopify_match === 'ambiguous'

  return (
    <Card className="flex-row gap-0 overflow-hidden p-0">
      {/* Flag accent (left edge) */}
      <div className={cn('w-1 shrink-0 self-stretch', ACCENT[order.flag] ?? 'bg-border')} />

      {/* Main content */}
      <div className="flex min-w-0 flex-1 flex-col gap-3 px-4 py-3">
        {/* Header line: identity + product + badges + Shopify indicator */}
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-sm font-medium">SO #{order.special_order_id}</span>
          <StageBadge stage={order.procurement_stage} />
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
                <ShopifyMatchBadge match={order.shopify_match} />
                {order.shopify_order_name && (
                  <span className="font-mono text-xs text-blue-600 underline">{order.shopify_order_name}</span>
                )}
              </a>
            ) : (
              <span className="shrink-0">
                <ShopifyMatchBadge match={order.shopify_match} />
              </span>
            ))}
        </div>

        {/* Fields grouped into logical clusters that read left-to-right:
            who/what → sourcing PO → when (all dates together) → how late.
            A full-width grid spreads the groups evenly across the available room. */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
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
          </FieldGroup>

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
      </div>

      {/* Lightspeed deep links (right edge) */}
      <div className="flex shrink-0 flex-col justify-center gap-1.5 border-l px-3 py-3 sm:w-44">
        <span className="text-muted-foreground text-[10px] font-medium uppercase tracking-wide">Open in Lightspeed</span>
        <LightspeedLink url={order.ls_item_url} label="Product" icon={Package} />
        <LightspeedLink url={order.ls_customer_url} label="Customer" icon={User} />
        <LightspeedLink url={order.ls_order_url} label="Purchase order" icon={FileText} />
      </div>
    </Card>
  )
}

interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  // Called after an ETA is written to Shopify, so the parent can refetch the live value.
  onEtaSaved?: () => void | Promise<void>
}

export function SpecialOrdersGrid({ orders, isLoading, onEtaSaved }: Props) {
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
          <SpecialOrderRow key={`${o.kind ?? 'ls'}-${o.special_order_id}`} order={o} onEtaSaved={onEtaSaved} />
        ))}
      </div>
    </div>
  )
}
