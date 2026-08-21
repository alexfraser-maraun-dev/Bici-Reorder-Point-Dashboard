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

export interface OrderMilestone {
  key: 'created' | 'drafted' | 'ordered' | 'received'
  label: string
  date: string | null
  complete: boolean
}

/** A lightweight progress summary built from the worklist row itself.
 *
 * The audited activity feed remains lazy in the Review drawer. Keeping this mapper local to the
 * row avoids turning a 25-order page into 25 additional activity requests. */
export function orderMilestones(order: SpecialOrder): OrderMilestone[] {
  const isShopifyIntake = order.kind === 'shopify'
  const stageIndex = isShopifyIntake ? 0 : order.procurement_stage_index
  const drafted = !isShopifyIntake && Boolean(
    order.po_created_date || order.order_id || stageIndex >= 1,
  )
  const ordered = !isShopifyIntake && Boolean(
    order.ordered_date || order.po_ordered || stageIndex >= 2,
  )
  const received = !isShopifyIntake && Boolean(
    order.po_received_date || order.po_complete || order.received_started || stageIndex >= 3,
  )

  return [
    {
      key: 'created',
      label: isShopifyIntake ? 'Shopify order' : 'SO created',
      date: order.created_date,
      complete: true,
    },
    {
      key: 'drafted',
      label: isShopifyIntake ? 'Lightspeed SO' : 'PO drafted',
      date: order.po_created_date,
      complete: drafted,
    },
    { key: 'ordered', label: 'Ordered', date: order.ordered_date, complete: ordered },
    { key: 'received', label: 'Arrived', date: order.po_received_date, complete: received },
  ]
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

function MetadataLink({
  href,
  label,
  ariaLabel,
  className,
}: {
  href: string | null
  label: string
  ariaLabel: string
  className?: string
}) {
  if (!href) return <span className={className}>{label}</span>
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={ariaLabel}
      className={cn(
        'rounded-sm underline decoration-muted-foreground/50 underline-offset-2 hover:text-foreground hover:decoration-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      {label}
    </a>
  )
}

function MilestoneRail({ order, identity }: { order: SpecialOrder; identity: string }) {
  const milestones = orderMilestones(order)
  const firstIncomplete = milestones.findIndex((milestone) => !milestone.complete)
  const currentIndex = firstIncomplete === -1 ? milestones.length - 1 : firstIncomplete

  return (
    <div className="border-t bg-muted/10 px-4 py-2">
      <div className="flex items-center gap-4">
        <span className="w-14 shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Progress
        </span>
        <ol
          className="grid min-w-0 flex-1 grid-cols-4 gap-3"
          aria-label={`Order milestones for ${identity}`}
        >
          {milestones.map((milestone, index) => {
            const isCurrent = index === currentIndex
            const connectorComplete = milestone.complete && milestones[index + 1]?.complete
            return (
              <li
                key={milestone.key}
                className="relative flex min-w-0 items-start gap-2"
                aria-current={isCurrent ? 'step' : undefined}
              >
                {index < milestones.length - 1 && (
                  <span
                    className={cn(
                      'absolute left-2 top-[7px] h-px w-[calc(100%+0.75rem)]',
                      connectorComplete ? 'bg-primary/45' : 'bg-border',
                    )}
                    aria-hidden="true"
                  />
                )}
                <span
                  className={cn(
                    'relative z-10 mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2 bg-card',
                    milestone.complete && 'border-primary bg-primary',
                    isCurrent && !milestone.complete && 'border-primary ring-2 ring-primary/15',
                    !milestone.complete && !isCurrent && 'border-muted-foreground/30',
                  )}
                  aria-hidden="true"
                />
                <span className="relative z-10 min-w-0 bg-card/90 pr-1 text-[11px] leading-4">
                  <span className={cn('block truncate font-medium', !milestone.complete && !isCurrent && 'text-muted-foreground')}>
                    {milestone.label}
                  </span>
                  <span className="block truncate text-muted-foreground">
                    {milestone.date ? (
                      <time dateTime={milestone.date}>{formatDate(milestone.date)}</time>
                    ) : milestone.complete ? 'Complete' : 'Pending'}
                  </span>
                </span>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
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
  const customer = order.customer_name ?? order.customer_email
  const sourceHref = order.source === 'workorder'
    ? order.workorder_url
    : order.source === 'shopify'
      ? order.shopify_order_url
      : null
  const sourceLinkLabel = order.source === 'workorder'
    ? `Open Lightspeed workorder ${order.workorder_id ?? identity} in a new tab`
    : `Open Shopify order ${order.shopify_order_name ?? identity} in a new tab`
  const hasCustomer = Boolean(customer)
  const hasSystemId = Boolean(order.system_sku)
  const hasPo = Boolean(order.order_id)

  return (
    <article className="overflow-hidden rounded-lg border bg-card shadow-xs transition-colors hover:border-foreground/20">
      <div className="flex min-w-0">
        <div className={cn('w-1 shrink-0', accent)} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className={cn('grid min-w-0 items-center gap-4 px-4 py-3', SPECIAL_ORDER_QUEUE_COLUMNS)}>
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 font-mono text-sm font-semibold">{identity}</span>
                <SourceBadge source={order.source} href={sourceHref} linkLabel={sourceLinkLabel} />
                {order.shopify_order_url && order.source !== 'shopify' && (
                  <SourceBadge
                    source="shopify"
                    href={order.shopify_order_url}
                    linkLabel={`Open Shopify order ${order.shopify_order_name ?? identity} in a new tab`}
                  />
                )}
              </div>
              <p className="mt-1 truncate text-sm font-medium" title={order.description ?? 'Special order'}>
                {order.description ?? 'Special order'}
              </p>
              <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
                {hasCustomer && (
                  <MetadataLink
                    href={order.ls_customer_url}
                    label={customer!}
                    ariaLabel={`Open Lightspeed customer ${customer} in a new tab`}
                    className="max-w-40 truncate"
                  />
                )}
                {hasSystemId && (
                  <>
                    {hasCustomer && <span aria-hidden="true">·</span>}
                    <MetadataLink
                      href={order.ls_item_url}
                      label={`System ID ${order.system_sku}`}
                      ariaLabel={`Open Lightspeed product for System ID ${order.system_sku} in a new tab`}
                      className="font-mono"
                    />
                  </>
                )}
                {hasPo && (
                  <>
                    {(hasCustomer || hasSystemId) && <span aria-hidden="true">·</span>}
                    <MetadataLink
                      href={order.ls_order_url}
                      label={`PO #${order.order_id}`}
                      ariaLabel={`Open Lightspeed purchase order ${order.order_id} in a new tab`}
                      className="font-mono"
                    />
                  </>
                )}
                {order.store && (
                  <>
                    {(hasCustomer || hasSystemId || hasPo) && <span aria-hidden="true">·</span>}
                    <span>{order.store}</span>
                  </>
                )}
                {!hasCustomer && !hasSystemId && !hasPo && !order.store && (
                  <span>No customer or item details</span>
                )}
              </div>
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
          <MilestoneRail order={order} identity={identity} />
        </div>
      </div>
    </article>
  )
}
