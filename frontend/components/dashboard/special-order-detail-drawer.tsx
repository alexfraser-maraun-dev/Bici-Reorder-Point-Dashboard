'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import type { ShopifyOnlyOrder, SpecialOrder, SpecialOrderActivityEvent } from '@/lib/types'
import { useSoActivity } from '@/lib/hooks'
import {
  AvailableVendors,
  CopyableUpc,
  EditableServicePromise,
  EditableEta,
} from './special-order-fields'
import {
  LsMatchControls,
  MatchPickerDialog,
  type MatchActions,
} from './special-order-match'
import { SeverityBadge, ShopifyMatchBadge, SourceBadge } from './special-order-badges'
import { EscalationBadge, SoAckMenu } from './so-ack-menu'
import { PoRecommendationPanel } from './so-po-recommendation'
import { ownerLabel, specialOrderReceivingState, StagePill, workStateLabel } from './special-order-row'
import { CalendarClock, Link2 } from 'lucide-react'

interface Props {
  order: SpecialOrder
  open: boolean
  onOpenChange: (open: boolean) => void
  onEtaSaved?: () => void | Promise<void>
  lsUnmatched: SpecialOrder[]
  unmatchedShopify: ShopifyOnlyOrder[]
  actions?: MatchActions
}

function display(value: React.ReactNode): React.ReactNode {
  return value === null || value === undefined || value === '' ? '—' : value
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function Info({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{display(value)}</dd>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

function routeLabel(tier: SpecialOrder['fastest_path_tier']): string {
  if (tier === 'in_stock') return 'Already in stock'
  if (tier === 'transfer') return 'Transfer from another store'
  if (tier === 'inbound_po') return 'Already inbound'
  if (tier === 'new_po') return 'New vendor order'
  if (tier === 'received') return 'Received'
  return 'Not calculated'
}

function receivingLabel(order: SpecialOrder): string {
  const state = specialOrderReceivingState(order)
  if (state === 'so_received') return 'Special order received'
  if (state === 'po_complete_so_unreceived') {
    return 'PO complete · SO pending (split shipment/backorder likely)'
  }
  if (state === 'po_receiving') {
    return 'PO receiving · SO pending (split shipment/backorder likely)'
  }
  return 'Not started'
}

function eventDetails(details: Record<string, unknown> | string | null): string | null {
  if (!details) return null
  if (typeof details === 'string') return details
  const parts = Object.entries(details)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${String(value)}`)
  return parts.length > 0 ? parts.join(' · ') : null
}

function ActivityPanel({
  order,
  activity,
  isLoading,
  error,
  onRetry,
}: {
  order: SpecialOrder
  activity: SpecialOrderActivityEvent[]
  isLoading: boolean
  error: unknown
  onRetry: () => void
}) {
  return (
    <Section title="Activity">
      {isLoading && <Skeleton className="h-24 w-full" />}
      {activity.length > 0 ? (
        <ol className="space-y-3 border-l pl-4 text-sm">
          {activity.map((event, index) => {
            const details = eventDetails(event.details)
            return (
              <li key={`${event.timestamp}-${event.type}-${index}`}>
                <p className="font-medium">{event.label}</p>
                <p className="text-xs text-muted-foreground">
                  {new Date(event.timestamp).toLocaleString()}
                  {event.actor ? ` · ${event.actor}` : ''}
                </p>
                {details && <p className="mt-1 text-xs text-muted-foreground">{details}</p>}
              </li>
            )
          })}
        </ol>
      ) : !isLoading && (
        <ol className="space-y-3 border-l pl-4 text-sm">
          <li>
            <p className="font-medium">Special order created</p>
            <p className="text-xs text-muted-foreground">{formatDate(order.created_date)}</p>
          </li>
          {order.ordered_date && (
            <li>
              <p className="font-medium">Purchase order placed</p>
              <p className="text-xs text-muted-foreground">{formatDate(order.ordered_date)}</p>
            </li>
          )}
          {order.po_received_date && (
            <li>
              <p className="font-medium">Purchase order receiving activity recorded</p>
              <p className="text-xs text-muted-foreground">
                {formatDate(order.po_received_date)}
                {(order.receiving_state === 'po_receiving' || order.receiving_state === 'po_complete_so_unreceived') && (
                  <span> · SO pending — likely split shipment or backorder</span>
                )}
              </p>
            </li>
          )}
          {order.so_received_date && (
            <li>
              <p className="font-medium">Special order checked in</p>
              <p className="text-xs text-muted-foreground">{formatDate(order.so_received_date)}</p>
            </li>
          )}
          {order.link_provenance && (
            <li>
              <p className="font-medium">Shopify order linked manually</p>
              <p className="text-xs text-muted-foreground">
                {order.link_provenance.linked_at
                  ? new Date(order.link_provenance.linked_at).toLocaleString()
                  : 'Date unavailable'}
                {order.link_provenance.linked_by ? ` · ${order.link_provenance.linked_by}` : ''}
              </p>
            </li>
          )}
          {order.service_promise_recorded_at && (
            <li>
              <p className="font-medium">Service parts promise recorded</p>
              <p className="text-xs text-muted-foreground">
                {new Date(order.service_promise_recorded_at).toLocaleString()}
                {order.service_promise_recorded_by ? ` · ${order.service_promise_recorded_by}` : ''}
              </p>
            </li>
          )}
          {order.ack && (
            <li>
              <p className="font-medium">Parked: {order.ack.reason_code.replace(/_/g, ' ')}</p>
              <p className="text-xs text-muted-foreground">
                {new Date(order.ack.acked_at).toLocaleString()} · check back {formatDate(order.ack.checkback_date)}
                {order.ack.acked_by ? ` · ${order.ack.acked_by}` : ''}
              </p>
              {order.ack.note && <p className="mt-1 text-xs text-muted-foreground">{order.ack.note}</p>}
            </li>
          )}
        </ol>
      )}
      {Boolean(error) && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <p>The live activity feed is unavailable; showing milestones from the current record.</p>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
    </Section>
  )
}

export function SpecialOrderDetailDrawer({
  order,
  open,
  onOpenChange,
  onEtaSaved,
  lsUnmatched,
  unmatchedShopify,
  actions,
}: Props) {
  const [linkOpen, setLinkOpen] = useState(false)
  const identity = order.kind === 'shopify'
    ? order.shopify_order_name ?? order.special_order_id
    : `SO #${order.special_order_id}`
  const hasShopify = Boolean(order.shopify_order_id)
  const {
    activity,
    isLoading: activityLoading,
    error: activityError,
    revalidate: revalidateActivity,
  } = useSoActivity(order.kind === 'shopify' ? null : order.special_order_id)
  const linkDialogId = `link-shopify-order-${order.special_order_id}`
  const refreshAfterMutation = async () => {
    const [worklistResult] = await Promise.allSettled([
      Promise.resolve().then(() => onEtaSaved?.()),
      revalidateActivity(),
    ])
    if (worklistResult.status === 'rejected') throw worklistResult.reason
  }
  const drawerActions = actions ? {
    onMatch: async (specialOrderId: string, shopifyOrderId: string) => {
      await actions.onMatch(specialOrderId, shopifyOrderId)
      await revalidateActivity().catch(() => undefined)
    },
    onUnmatch: async (specialOrderId: string, shopifyOrderId: string) => {
      await actions.onUnmatch(specialOrderId, shopifyOrderId)
      await revalidateActivity().catch(() => undefined)
    },
    onBatchUnmatch: async (specialOrderId: string, shopifyOrderIds: string[]) => {
      await actions.onBatchUnmatch(specialOrderId, shopifyOrderIds)
      await revalidateActivity().catch(() => undefined)
    },
  } : undefined

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-[680px] gap-0 p-0 sm:max-w-[680px]">
          <SheetHeader className="border-b px-6 py-5 pr-12">
            <div className="flex flex-wrap items-center gap-2">
              <SheetTitle className="font-mono text-lg">{identity}</SheetTitle>
              <StagePill order={order} />
              <SourceBadge source={order.source} />
              <SeverityBadge severity={order.sla_severity} muted={order.ack_active} />
              <EscalationBadge level={order.escalation_level} />
            </div>
            <SheetDescription className="line-clamp-2">
              {order.description ?? 'Special order details and next action'}
            </SheetDescription>
          </SheetHeader>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-6 px-6 py-5">
              <section className="rounded-lg border border-primary/20 bg-primary/5 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {order.next_action ? `Next action · ${ownerLabel(order.action_owner)}` : 'Current status'}
                    </p>
                    <p className="mt-1 text-base font-semibold">{order.next_action ?? 'No action required'}</p>
                    {order.sla_reason && (
                      <p className="mt-1 text-sm text-muted-foreground">{order.sla_reason}</p>
                    )}
                  </div>
                  <Badge variant="secondary">{workStateLabel(order.work_state)}</Badge>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  {order.action_due_date && (
                    <span className="inline-flex items-center gap-1.5 text-sm">
                      <CalendarClock className="h-4 w-4 text-muted-foreground" />
                      Due {formatDate(order.action_due_date)}
                    </span>
                  )}
                  {order.kind !== 'shopify' && (
                    <SoAckMenu
                      order={order}
                      onDone={() => {
                        void refreshAfterMutation().catch(() => {
                          toast.warning('The parked status was saved, but the worklist could not refresh.')
                        })
                      }}
                    />
                  )}
                </div>
              </section>

              <ActivityPanel
                order={order}
                activity={activity}
                isLoading={activityLoading}
                error={activityError}
                onRetry={() => void revalidateActivity()}
              />

              <Separator />

              <Section title="Customer and item">
                <dl className="grid grid-cols-3 gap-x-5 gap-y-4">
                  <Info label="Customer" value={order.customer_name ?? order.customer_email} />
                  <Info label="Phone" value={order.customer_phone} />
                  <Info label="Store" value={order.store} />
                  <Info label="SKU" value={order.system_sku ? <span className="font-mono">{order.system_sku}</span> : null} />
                  <Info label="UPC" value={order.upc ? <CopyableUpc upc={order.upc} /> : null} />
                  <Info label="Quantity" value={order.unit_quantity} />
                  <Info label="Brand" value={order.brand} />
                  <Info label="Vendor" value={order.vendor_name} />
                  <Info label="Created" value={formatDate(order.created_date)} />
                </dl>
              </Section>

              <Separator />

              <Section title="Dates and timing">
                <dl className="grid grid-cols-3 gap-x-5 gap-y-4">
                  <Info label="Customer promise" value={formatDate(order.promise_date)} />
                  <Info label="Ordered" value={formatDate(order.ordered_date)} />
                  <Info label="PO expected" value={formatDate(order.expected_date)} />
                  <Info label="Soonest landing" value={formatDate(order.fastest_landing_date)} />
                  <Info label="Days open" value={order.days_since_creation} />
                  <Info label="Days in stage" value={order.days_in_stage} />
                  <Info label="Order by" value={formatDate(order.order_by_date)} />
                  <Info
                    label="Slack"
                    value={order.slack_days == null ? null : `${order.slack_days} days`}
                  />
                  <Info
                    label="Days lost"
                    value={order.days_lost == null ? null : `${order.days_lost} days`}
                  />
                </dl>
                {hasShopify && order.source !== 'workorder' && (
                  <div className="rounded-md border bg-muted/30 p-3">
                    <p className="mb-2 text-xs font-medium text-muted-foreground">Shopify customer promise</p>
                    <EditableEta
                      orderId={order.shopify_order_id}
                      value={order.shopify_expected_date}
                      ambiguous={order.shopify_match === 'ambiguous'}
                      onSaved={refreshAfterMutation}
                    />
                  </div>
                )}
                {order.source === 'workorder' && (
                  <div className="rounded-md border bg-muted/30 p-3">
                    <p className="text-xs font-medium text-muted-foreground">Service parts promise</p>
                    <div className="mt-2">
                      <EditableServicePromise
                        specialOrderId={order.special_order_id}
                        value={order.service_promise_date}
                        recordedAt={order.service_promise_recorded_at}
                        recordedBy={order.service_promise_recorded_by}
                        onSaved={refreshAfterMutation}
                      />
                    </div>
                  </div>
                )}
              </Section>

              <Separator />

              <Section title="Purchase and supply">
                <dl className="grid grid-cols-3 gap-x-5 gap-y-4">
                  <Info label="PO number" value={order.order_id} />
                  <Info label="PO type" value={order.order_type} />
                  <Info
                    label="Receiving"
                    value={receivingLabel(order)}
                  />
                  <Info label="Fastest route" value={routeLabel(order.fastest_path_tier)} />
                  <Info label="Could have landed" value={formatDate(order.could_have_landed)} />
                  <Info label="Vendor lead time" value={order.vendor_lead_time_days == null ? null : `${order.vendor_lead_time_days} days`} />
                </dl>
                {(order.procurement_stage === 'open_pool' || order.procurement_stage === 'unordered_po') && (
                  <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                    Purchase-order allocation is completed in Lightspeed. This tool shows the supply route and verifies the result after sync.
                  </p>
                )}
                {order.available_vendors.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">Available from</p>
                    <AvailableVendors vendors={order.available_vendors} />
                  </div>
                )}
                {order.kind !== 'shopify' && order.procurement_stage !== 'received' && (
                  <PoRecommendationPanel order={order} />
                )}
              </Section>

              {(order.workorder_id || order.source === 'workorder') && (
                <>
                  <Separator />
                  <Section title="Service workorder">
                    <dl className="grid grid-cols-2 gap-x-5 gap-y-4">
                      <Info label="Workorder" value={order.workorder_id} />
                      <Info label="Status" value={order.workorder_status} />
                      <Info label="Bike received" value={formatDate(order.workorder_time_in)} />
                      <Info label="Bike ETA out" value={formatDate(order.workorder_eta_out)} />
                      <Info label="Hook-in" value={order.workorder_hook_in} />
                      <Info label="Close-out" value={order.closeout_state} />
                    </dl>
                    {order.workorder_note && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground">Customer note</p>
                        <p className="mt-1 whitespace-pre-wrap text-sm">{order.workorder_note}</p>
                      </div>
                    )}
                    {order.workorder_internal_note && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground">Internal note</p>
                        <p className="mt-1 whitespace-pre-wrap text-sm">{order.workorder_internal_note}</p>
                      </div>
                    )}
                  </Section>
                </>
              )}

              <Separator />

              <Section title="Shopify matching">
                <div className="flex flex-wrap items-center gap-2">
                  <ShopifyMatchBadge
                    match={order.shopify_match}
                    basis={order.shopify_match_basis}
                    possible={order.ambiguous_candidate}
                  />
                  {order.shopify_order_name && (
                    <span className="font-mono text-sm">{order.shopify_order_name}</span>
                  )}
                  {order.kind === 'shopify' && drawerActions && lsUnmatched.length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1.5"
                      aria-haspopup="dialog"
                      aria-expanded={linkOpen}
                      aria-controls={linkDialogId}
                      onClick={() => setLinkOpen(true)}
                    >
                      <Link2 className="h-4 w-4" />
                      Link to Lightspeed SO
                    </Button>
                  )}
                  {order.kind !== 'shopify' && drawerActions && (
                    <LsMatchControls order={order} unmatchedShopify={unmatchedShopify} actions={drawerActions} />
                  )}
                </div>
                {order.link_broken && (
                  <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    The saved link to Shopify order {order.link_broken} no longer resolves. Re-link this order.
                  </p>
                )}
                {order.matched_via_closed_order && (
                  <p className="text-sm text-muted-foreground">
                    This was matched through the fulfilled or archived Shopify-order lookup.
                  </p>
                )}
              </Section>
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>

      {order.kind === 'shopify' && drawerActions && (
        <MatchPickerDialog
          open={linkOpen}
          onOpenChange={setLinkOpen}
          contentId={linkDialogId}
          title={`Link ${order.shopify_order_name ?? 'this Shopify order'} to a Lightspeed special order`}
          description="Choose the Lightspeed special order that fulfils this Shopify order. You will confirm the link before it is saved."
          items={lsUnmatched.map((candidate) => ({
            key: String(candidate.special_order_id),
            title: `SO #${candidate.special_order_id}${candidate.description ? ` — ${candidate.description}` : ''}`,
            subtitle: [candidate.customer_name, candidate.customer_email].filter(Boolean).join(' · ') || null,
            meta: [candidate.system_sku, candidate.store].filter(Boolean).join(' · ') || null,
          }))}
          onPick={(specialOrderId) => drawerActions.onMatch(specialOrderId, order.shopify_order_id!)}
        />
      )}
    </>
  )
}
