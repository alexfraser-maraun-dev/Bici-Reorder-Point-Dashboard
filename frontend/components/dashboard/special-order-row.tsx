'use client'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type {
  SpecialOrder,
  SpecialOrderActionOwner,
  SpecialOrderReceivingState,
  SpecialOrderWorkState,
  TriageStage,
} from '@/lib/types'
import {
  CustomerWaitingBadge,
  SeriousnessBadge,
  SeverityBadge,
  ShopifyClosedBadge,
  SourceBadge,
} from './special-order-badges'
import { SoWorkActions } from './so-work-actions'
import {
  AlertCircle,
  ChevronRight,
  CircleSlash,
  Clock3,
  PackageCheck,
  ShoppingCart,
  Store,
  Truck,
} from 'lucide-react'

// Measured against live content at 1440px, not guessed: the Status badges need 123px, the widest
// date row 157px, and the Age cell exactly 105px. The slack that bought went to Next action, whose
// whole job is now to be read at a glance rather than truncated into a tooltip.
export const SPECIAL_ORDER_QUEUE_COLUMNS =
  'grid-cols-[minmax(240px,1.62fr)_minmax(240px,2fr)_135px_168px_105px_auto]'

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
  shopify_fulfilled: 'Shopify fulfilled',
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
  shopify_fulfilled: 'bg-violet-500',
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

/** Whole calendar days from a date string to today, both anchored at local noon. */
function daysBetween(value: string | null): number | null {
  if (!value) return null
  const parsed = Date.parse(`${value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(parsed)) return null
  const now = new Date()
  const todayNoon = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12).getTime()
  return Math.max(0, Math.round((todayNoon - parsed) / 86_400_000))
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
  detail?: string
  hint?: string
  attention?: boolean
}

/** Resolve the new explicit receiving contract, with a safe fallback for cached responses made
 * before it existed. PO-level progress must never be interpreted as receipt of this SO. */
export function specialOrderReceivingState(order: SpecialOrder): SpecialOrderReceivingState {
  // `so_received` is the authoritative individual-unit signal when present. The enum adds the
  // PO context; neither a PO header nor another received line may override an explicit `false`.
  if (order.so_received === true) return 'so_received'
  if (order.receiving_state && order.receiving_state !== 'so_received') {
    return order.receiving_state
  }
  if (order.so_received === false) {
    if (order.po_complete) return 'po_complete_so_unreceived'
    if (order.received_started || order.po_received_date) return 'po_receiving'
    return 'not_started'
  }
  if (order.receiving_state === 'so_received') return 'so_received'
  if (order.procurement_stage === 'received' || order.procurement_stage_index >= 3) {
    return 'so_received'
  }
  if (order.po_complete) return 'po_complete_so_unreceived'
  if (order.received_started || order.po_received_date) return 'po_receiving'
  return 'not_started'
}

/** A lightweight progress summary built from the worklist row itself.
 *
 * The audited activity feed remains lazy in the Review drawer. Keeping this mapper local to the
 * row avoids turning a 25-order page into 25 additional activity requests. */
export function orderMilestones(order: SpecialOrder): OrderMilestone[] {
  const isShopifyIntake = order.kind === 'shopify'
  const stageIndex = isShopifyIntake ? 0 : order.procurement_stage_index
  const receivingState = isShopifyIntake
    ? 'not_started'
    : specialOrderReceivingState(order)
  const drafted = !isShopifyIntake && Boolean(
    order.po_created_date || order.order_id || stageIndex >= 1,
  )
  const ordered = !isShopifyIntake && Boolean(
    order.ordered_date || order.po_ordered || stageIndex >= 2,
  )
  const received = !isShopifyIntake && receivingState === 'so_received'
  const poContextDate = order.po_received_date ? ` ${formatDate(order.po_received_date)}` : ''
  const receivingDetail = receivingState === 'po_complete_so_unreceived'
    ? `PO complete${poContextDate} · SO pending`
    : receivingState === 'po_receiving'
      ? `PO receiving${poContextDate} · SO pending`
      : undefined

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
    {
      key: 'received',
      label: 'SO check-in',
      // `po_received_date` is a PO-header timestamp and can predate this unit's arrival.
      date: received ? order.so_received_date ?? null : null,
      complete: received,
      detail: receivingDetail,
      hint: receivingDetail ? 'Likely split shipment / backorder' : undefined,
      attention: Boolean(receivingDetail),
    },
  ]
}

const READY_BASIS_HINT: Record<SpecialOrder['earliest_ready_basis'], string> = {
  received: 'Already received.',
  po_eta_plus_buffer: "The purchase order's expected arrival plus the receiving buffer.",
  fastest_route: 'Soonest by any route — stock, transfer, an inbound PO, or ordering now (lead time + receiving buffer).',
  lead_time_default: 'Ordering now: vendor lead time + receiving buffer.',
}

/** The date rows, fixed rather than stage-dependent.
 *
 * These used to collapse to a single stage-chosen row above "Customer promise", which rendered
 * the promise date twice on every order that had not been placed yet. Naming each date instead
 * is what makes the difference between them legible: `PO expected` is when the BOX lands at the
 * store, `Fastest possible` is the earliest the customer could actually collect it. */
function dateRows(order: SpecialOrder): { label: string; value: string | null; hint?: string }[] {
  if (order.procurement_stage === 'received') {
    return [
      { label: 'Arrived', value: order.so_received_date ?? order.expected_date },
      { label: 'Customer promise', value: order.promise_date },
    ]
  }
  return [
    { label: 'PO expected', value: order.expected_date },
    {
      label: 'Fastest possible',
      value: order.earliest_ready_date,
      hint: READY_BASIS_HINT[order.earliest_ready_basis] ?? undefined,
    },
    { label: 'Customer promise', value: order.promise_date },
  ]
}

function WorkIcon({ state }: { state: SpecialOrderWorkState }) {
  const className = 'h-4 w-4 shrink-0'
  if (state === 'intake') return <Store className={className} />
  if (state === 'needs_ordering') return <ShoppingCart className={className} />
  if (state === 'shopify_fulfilled') return <CircleSlash className={className} />
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

function MilestoneRail({
  order,
  identity,
  onWorkStateChanged,
}: {
  order: SpecialOrder
  identity: string
  onWorkStateChanged?: () => void | Promise<void>
}) {
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
                    isCurrent && !milestone.complete && !milestone.attention && 'border-primary ring-2 ring-primary/15',
                    milestone.attention && 'border-amber-600 bg-amber-50 ring-2 ring-amber-500/20',
                    !milestone.complete && !isCurrent && 'border-muted-foreground/30',
                  )}
                  aria-hidden="true"
                />
                <span className="relative z-10 min-w-0 bg-card/90 pr-1 text-[11px] leading-4">
                  <span className={cn(
                    'block truncate font-medium',
                    milestone.attention && 'text-amber-800',
                    !milestone.complete && !isCurrent && 'text-muted-foreground',
                  )}>
                    {milestone.label}
                  </span>
                  <span className={cn(
                    'block truncate text-muted-foreground',
                    milestone.attention && 'font-medium text-amber-800',
                  )}>
                    {milestone.detail ?? (milestone.date ? (
                      <time dateTime={milestone.date}>{formatDate(milestone.date)}</time>
                    ) : milestone.complete ? 'Complete' : 'Pending')}
                  </span>
                  {milestone.hint && (
                    <span className="block truncate text-[10px] font-medium text-amber-700">
                      {milestone.hint}
                    </span>
                  )}
                </span>
              </li>
            )
          })}
        </ol>
        {/* The score lives beside the actions, not beside "days open": stacked directly above
            that label it read as a qualifier on the age rather than a rating of its own. Here it
            sits where the decision is made. Rendered even without actions so a read-only row
            still carries its priority. */}
        <div className="flex shrink-0 items-center gap-3 border-l pl-4">
          <SeriousnessBadge
            score={order.priority_score}
            band={order.priority_band}
            reasons={order.priority_reasons}
            muted={order.ack_active}
          />
          {onWorkStateChanged && (
            <SoWorkActions order={order} size="compact" onDone={onWorkStateChanged} />
          )}
        </div>
      </div>
    </div>
  )
}

export function SpecialOrderRow({
  order,
  onReview,
  onWorkStateChanged,
}: {
  order: SpecialOrder
  onReview: (order: SpecialOrder) => void
  /** Refresh the worklist after Start/Done. Omit to render the row read-only. */
  onWorkStateChanged?: () => void | Promise<void>
}) {
  const dates = dateRows(order)
  // Cached responses written before the open clock existed carry no `days_open`.
  const daysOpen = order.days_open ?? order.days_since_creation
  const identity = order.kind === 'shopify'
    ? order.shopify_order_name ?? order.special_order_id
    : `SO #${order.special_order_id}`
  // A finished Shopify order takes the stripe off the severity axis for the same reason it
  // replaces the badge: `sla_severity` is still `promise_missed` on these rows and that breach is
  // an artefact, so a red stripe next to a violet badge and a 4 tells three different stories.
  // A stranded customer must not wear the emerald "arrived, all good" stripe that every other
  // received row gets from WORK_ACCENT.closeout.
  const stranded = order.closeout_state === 'customer_stranded'
  const daysSinceArrival = stranded ? daysBetween(order.so_received_date ?? null) : null
  const accent = stranded
    ? (daysSinceArrival != null && daysSinceArrival >= 7 ? 'bg-red-600' : 'bg-amber-500')
    : order.shopify_order_closed
      ? WORK_ACCENT.shopify_fulfilled
      : ACCENT[order.sla_severity] ?? WORK_ACCENT[order.work_state] ?? 'bg-border'
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
              {/* Wraps because a workorder-origin SO that also has a Shopify order carries four
                  chips (id, Workorder, Shopify, store), which overflows the column by ~27px and
                  silently clips the store — the one thing this row was rearranged to surface. */}
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <span className="shrink-0 font-mono text-sm font-semibold">{identity}</span>
                <SourceBadge source={order.source} href={sourceHref} linkLabel={sourceLinkLabel} />
                {order.shopify_order_url && order.source !== 'shopify' && (
                  <SourceBadge
                    source="shopify"
                    href={order.shopify_order_url}
                    linkLabel={`Open Shopify order ${order.shopify_order_name ?? identity} in a new tab`}
                  />
                )}
                {order.store && (
                  <Badge
                    variant="outline"
                    className="gap-1 border-border bg-secondary text-xs font-medium text-muted-foreground"
                  >
                    <Store className="h-3 w-3" aria-hidden="true" />
                    {order.store}
                  </Badge>
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
                {!hasCustomer && !hasSystemId && !hasPo && (
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
                {order.work_status === 'in_progress' && (
                  <span className="font-medium text-blue-700">· in progress</span>
                )}
                {order.work_status === 'done' && (
                  <span className="font-medium text-emerald-700">· done</span>
                )}
                {order.work_status === 'parked' && <span>· parked</span>}
              </div>
            </div>

            <div className="space-y-1.5">
              <StagePill order={order} />
              {order.closeout_state === 'customer_stranded'
                ? <div><CustomerWaitingBadge days={daysSinceArrival} /></div>
                : order.shopify_order_closed
                  ? <div><ShopifyClosedBadge state={order.shopify_order_closed} /></div>
                  : <div><SeverityBadge severity={order.sla_severity} muted={order.ack_active} /></div>}
            </div>

            <div className="min-w-0 space-y-1 text-xs">
              {dates.map((row) => (
                <div key={row.label} className="flex justify-between gap-3" title={row.hint}>
                  <span className="text-muted-foreground">{row.label}</span>
                  <span className="font-medium tabular-nums">{formatDate(row.value)}</span>
                </div>
              ))}
            </div>

            <div className="text-right">
              <p className="text-lg font-semibold tabular-nums">{daysOpen ?? '—'}</p>
              <p className="text-xs text-muted-foreground">days open</p>
              {order.intake_lag_days != null && order.intake_lag_days >= 2 && (
                <p
                  className="mt-1 text-[10px] font-medium text-amber-700"
                  title="The Shopify order was placed before the Lightspeed special order was raised — the customer had already been waiting."
                >
                  Shopify order {order.intake_lag_days}d earlier
                </p>
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
          <MilestoneRail
            order={order}
            identity={identity}
            onWorkStateChanged={onWorkStateChanged}
          />
        </div>
      </div>
    </article>
  )
}
