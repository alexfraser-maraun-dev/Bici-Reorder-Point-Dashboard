'use client'

/** The special-order row: one Card per order, plus the Shopify-only pseudo-row.
 *
 *  Split out of special-orders-grid.tsx so the grid shell stays about sorting and layout while
 *  the row stays about one order. The row composes the presentational fields, the match
 *  controls, the SLA line and the PO recommendation panel.
 */

import { useState } from 'react'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { SpecialOrder, ShopifyOnlyOrder } from '@/lib/types'
import {
  StageBadge,
  SourceBadge,
  SeverityBadge,
  FlagBadge,
  ShopifyMatchBadge,
} from './special-order-badges'
import { SoAckMenu, EscalationBadge } from './so-ack-menu'
import { PoRecommendationPanel } from './so-po-recommendation'
import {
  CopyableUpc, AvailableVendors, Field, FieldGroup, LightspeedLink, EditableEta,
} from './special-order-fields'
import {
  LsMatchControls, MatchPickerDialog, WorkorderFields, type MatchActions,
} from './special-order-match'
import { ExternalLink, Package, User, FileText, Store, Link2, Wrench } from 'lucide-react'

// Left-edge accent by flag severity — the fastest way to scan a long list for trouble.
const ACCENT: Partial<Record<SpecialOrder['flag'], string>> = {
  overdue: 'bg-red-300',
  overdue_mid: 'bg-red-500',
  critical: 'bg-red-600',
}
export function ShopifyOnlyRow({
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
export function SpecialOrderRow({
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

        {/* Matching diagnostics. Both are silent when there is nothing wrong, so their presence
            always means something needs a human. */}
        {(order.link_broken || order.matched_via_closed_order) && (
          <div className="flex flex-wrap items-center gap-x-3 border-t pt-2.5 text-xs">
            {order.link_broken && (
              <span className="text-red-600">
                Manual link to Shopify order {order.link_broken} no longer resolves — that order
                has been deleted or re-created. Re-link it.
                {order.link_provenance?.linked_by && (
                  <span className="text-muted-foreground">
                    {' '}(linked by {order.link_provenance.linked_by})
                  </span>
                )}
              </span>
            )}
            {order.matched_via_closed_order && (
              <span className="text-muted-foreground">
                Matched to a fulfilled/archived Shopify order — found by the late-match pass, so
                it is not in the unmatched list.
              </span>
            )}
          </div>
        )}

        {/* Where to order: only for special orders not yet on a placed PO. Once a PO exists the
            question is answered, and the panel would just be an extra BigQuery round-trip. */}
        {(order.procurement_stage === 'open_pool' || order.procurement_stage === 'unordered_po') && (
          <PoRecommendationPanel order={order} />
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
