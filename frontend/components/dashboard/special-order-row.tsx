'use client'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type {
  SpecialOrder,
  SpecialOrderActionOwner,
  SpecialOrderWorkState,
  TriageStage,
} from '@/lib/types'
import { SeverityBadge, SourceBadge } from './special-order-badges'
import {
  AlertCircle,
  ChevronRight,
  Clock3,
  PackageCheck,
  ShoppingCart,
  Store,
  Truck,
} from 'lucide-react'

export const SPECIAL_ORDER_QUEUE_COLUMNS =
  'grid-cols-[minmax(240px,1.8fr)_minmax(220px,1.55fr)_150px_190px_105px_auto]'

export const STAGE_LABELS: Record<TriageStage, string> = {
  shopify: 'Shopify intake',
  open_pool: 'Awaiting PO',
  unordered_po: 'Draft PO',
  ordered: 'In transit',
  received: 'Arrived',
}

const STAGE_STYLE: Record<TriageStage, string> = {
  shopify: 'border-violet-200 bg-violet-50 text-violet-700',
  open_pool: 'border-slate-200 bg-slate-50 text-slate-700',
  unordered_po: 'border-orange-200 bg-orange-50 text-orange-700',
  ordered: 'border-blue-200 bg-blue-50 text-blue-700',
  received: 'border-emerald-200 bg-emerald-50 text-emerald-700',
}

const OWNER_LABELS: Record<SpecialOrderActionOwner, string> = {
  procurement: 'Procurement',
  service: 'Service',
  cs: 'Customer service',
  receiving: 'Receiving',
  retail: 'Retail',
}

const WORK_STATE_LABELS: Record<SpecialOrderWorkState, string> = {
  intake: 'Intake',
  needs_ordering: 'Needs ordering',
  vendor_followup: 'Vendor follow-up',
  promise_needed: 'Promise needed',
  closeout: 'Close-out',
  on_track: 'On track',
}

const ACCENT: Partial<Record<SpecialOrder['sla_severity'], string>> = {
  promise_missed: 'bg-red-600',
  impossible: 'bg-red-500',
  order_today: 'bg-orange-500',
  stage_stalled: 'bg-amber-500',
  at_risk: 'bg-yellow-400',
}

const WORK_ACCENT: Partial<Record<SpecialOrderWorkState, string>> = {
  intake: 'bg-violet-500',
  needs_ordering: 'bg-orange-500',
  vendor_followup: 'bg-blue-500',
  promise_needed: 'bg-amber-500',
  closeout: 'bg-emerald-500',
}

export function triageStage(order: SpecialOrder): TriageStage {
  return order.kind === 'shopify' ? 'shopify' : order.procurement_stage
}

export function ownerLabel(owner: SpecialOrderActionOwner | null): string {
  return owner ? OWNER_LABELS[owner] : 'Monitoring'
}

export function workStateLabel(state: SpecialOrderWorkState): string {
  return WORK_STATE_LABELS[state]
}

export function StagePill({ order }: { order: SpecialOrder }) {
  const stage = triageStage(order)
  return (
    <Badge variant="outline" className={cn('text-[11px] font-medium', STAGE_STYLE[stage])}>
      {STAGE_LABELS[stage]}
    </Badge>
  )
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function primaryDate(order: SpecialOrder): { label: string; value: string | null } {
  if (order.procurement_stage === 'ordered') {
    return { label: 'Expected', value: order.expected_date ?? order.fastest_landing_date }
  }
  if (order.procurement_stage === 'received') {
    return { label: 'Arrived', value: order.po_received_date ?? order.expected_date }
  }
  return { label: 'Promise', value: order.promise_date }
}

function WorkIcon({ state }: { state: SpecialOrderWorkState }) {
  const className = 'h-4 w-4 shrink-0'
  if (state === 'intake') return <Store className={className} />
  if (state === 'needs_ordering') return <ShoppingCart className={className} />
  if (state === 'vendor_followup') return <Truck className={className} />
  if (state === 'closeout') return <PackageCheck className={className} />
  if (state === 'promise_needed') return <AlertCircle className={className} />
  return <Clock3 className={className} />
}

export function SpecialOrderRow({
  order,
  onReview,
}: {
  order: SpecialOrder
  onReview: (order: SpecialOrder) => void
}) {
  const date = primaryDate(order)
  const identity = order.kind === 'shopify'
    ? order.shopify_order_name ?? order.special_order_id
    : `SO #${order.special_order_id}`
  const accent = ACCENT[order.sla_severity] ?? WORK_ACCENT[order.work_state] ?? 'bg-border'

  return (
    <article className="overflow-hidden rounded-lg border bg-card shadow-xs transition-colors hover:border-foreground/20">
      <div className="flex min-w-0">
        <div className={cn('w-1 shrink-0', accent)} aria-hidden="true" />
        <div className={cn('grid min-w-0 flex-1 items-center gap-4 px-4 py-3', SPECIAL_ORDER_QUEUE_COLUMNS)}>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <span className="shrink-0 font-mono text-sm font-semibold">{identity}</span>
              <SourceBadge source={order.source} />
            </div>
            <p className="mt-1 truncate text-sm font-medium" title={order.description ?? 'Special order'}>
              {order.description ?? 'Special order'}
            </p>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {[order.customer_name ?? order.customer_email, order.system_sku, order.store]
                .filter(Boolean)
                .join(' · ') || 'No customer or item details'}
            </p>
          </div>

          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-medium">
              <WorkIcon state={order.work_state} />
              <span className="truncate" title={order.next_action ?? undefined}>
                {order.next_action ?? 'No action required'}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
              <span>{ownerLabel(order.action_owner)}</span>
              {order.action_due_date && <span>· due {formatDate(order.action_due_date)}</span>}
              {order.ack_active && <span>· parked</span>}
            </div>
          </div>

          <div className="space-y-1.5">
            <StagePill order={order} />
            <div><SeverityBadge severity={order.sla_severity} muted={order.ack_active} /></div>
          </div>

          <div className="min-w-0 text-xs">
            <div className="flex justify-between gap-3">
              <span className="text-muted-foreground">{date.label}</span>
              <span className="font-medium tabular-nums">{formatDate(date.value)}</span>
            </div>
            <div className="mt-1 flex justify-between gap-3">
              <span className="text-muted-foreground">Customer promise</span>
              <span className="font-medium tabular-nums">{formatDate(order.promise_date)}</span>
            </div>
          </div>

          <div className="text-right">
            <p className="text-lg font-semibold tabular-nums">{order.days_since_creation ?? '—'}</p>
            <p className="text-xs text-muted-foreground">days open</p>
            {order.days_lost != null && order.days_lost > 0 && (
              <p className="mt-1 text-xs font-medium text-red-600">{order.days_lost}d lost</p>
            )}
          </div>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => onReview(order)}
            aria-label={`Review ${identity}`}
          >
            Review
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </article>
  )
}
