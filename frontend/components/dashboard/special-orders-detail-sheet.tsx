'use client'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import type { SpecialOrder } from '@/lib/types'
import { StageBadge, FlagBadge, ShopifyMatchBadge, SpecialOrderStatusBadge } from './special-order-badges'
import { ExternalLink, Package, User, FileText, Store, Check } from 'lucide-react'
import { cn } from '@/lib/utils'

const STAGE_LABELS = ['Open Order Pool', 'Unordered PO', 'Ordered', 'Received'] as const

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground text-[11px] font-medium uppercase tracking-wide">{label}</span>
      <span className="text-sm">{value ?? '—'}</span>
    </div>
  )
}

function LightspeedLink({ url, label, icon: Icon }: { url: string | null; label: string; icon: typeof Package }) {
  if (!url) return null
  return (
    <Button variant="outline" size="sm" className="justify-start gap-2" asChild>
      <a href={url} target="_blank" rel="noopener noreferrer">
        <Icon className="h-4 w-4" />
        {label}
        <ExternalLink className="ml-auto h-3.5 w-3.5 opacity-60" />
      </a>
    </Button>
  )
}

function StatusStepper({ stage }: { stage: number }) {
  return (
    <div className="flex items-center gap-0">
      {STAGE_LABELS.map((label, i) => {
        const done = i < stage
        const active = i === stage
        const future = i > stage
        return (
          <div key={label} className="flex items-center">
            {/* connector */}
            {i > 0 && (
              <div className={cn('h-px w-6 flex-shrink-0', done ? 'bg-primary' : 'bg-border')} />
            )}
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  'flex h-6 w-6 items-center justify-center rounded-full border-2 text-[10px] font-bold',
                  done && 'border-primary bg-primary text-primary-foreground',
                  active && 'border-primary bg-background text-primary',
                  future && 'border-muted-foreground/30 bg-background text-muted-foreground/40'
                )}
              >
                {done ? <Check className="h-3 w-3" /> : i + 1}
              </div>
              <span
                className={cn(
                  'mt-1 max-w-[60px] text-center text-[9px] leading-tight',
                  active && 'font-semibold text-primary',
                  future && 'text-muted-foreground/50'
                )}
              >
                {label}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

interface Props {
  order: SpecialOrder | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SpecialOrderDetailSheet({ order, open, onOpenChange }: Props) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[480px]">
        {order && order.kind === 'shopify' && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <span className="font-mono text-sm">{order.shopify_order_name ?? order.special_order_id}</span>
                <StageBadge stage="shopify" />
                <ShopifyMatchBadge match="none" />
              </SheetTitle>
              <SheetDescription>Shopify SO — not matched to a Lightspeed special order.</SheetDescription>
            </SheetHeader>

            <div className="flex flex-col gap-4 px-4 pb-6">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Customer" value={order.customer_email} />
                <Field label="Shopify ETA" value={order.shopify_expected_date} />
                <Field label="SKU(s)" value={order.description ? <span className="font-mono text-xs">{order.description}</span> : null} />
                <Field label="Created" value={order.created_date} />
              </div>

              <Separator />

              <div className="flex flex-col gap-2">
                <span className="text-muted-foreground text-[11px] font-medium uppercase tracking-wide">Open in Shopify</span>
                {order.shopify_order_url
                  ? <LightspeedLink url={order.shopify_order_url} label={order.shopify_order_name ?? 'Shopify order'} icon={Store} />
                  : <span className="text-muted-foreground text-sm">{order.shopify_order_name ?? '—'}</span>}
              </div>
            </div>
          </>
        )}

        {order && order.kind !== 'shopify' && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <span className="font-mono text-sm">SO #{order.special_order_id}</span>
                <SpecialOrderStatusBadge status={order.status} />
              </SheetTitle>
              <SheetDescription>{order.description ?? 'Special order'}</SheetDescription>
            </SheetHeader>

            <div className="flex flex-col gap-4 px-4 pb-6">
              {/* Stage + attention flag */}
              <div className="flex flex-wrap items-center gap-2">
                <StageBadge stage={order.procurement_stage} />
                <FlagBadge stage={order.procurement_stage} flag={order.flag} daysOverdue={order.days_overdue} daysInStage={order.days_since_creation} />
              </div>

              {/* Procurement stage stepper */}
              <div className="overflow-x-auto py-1">
                <StatusStepper stage={order.procurement_stage_index} />
              </div>

              <Separator />

              {/* Customer + item */}
              <div className="grid grid-cols-2 gap-4">
                <Field label="Customer" value={order.customer_name} />
                <Field label="Phone" value={order.customer_phone} />
                <Field label="Store" value={order.store} />
                <Field label="Quantity" value={order.unit_quantity} />
                <Field label="SKU" value={order.system_sku ? <span className="font-mono text-xs">{order.system_sku}</span> : null} />
                <Field label="Vendor" value={order.vendor_name} />
              </div>

              <Separator />

              {/* PO + dates */}
              <div className="grid grid-cols-2 gap-4">
                <Field label="PO #" value={order.order_id} />
                <Field label="Ordered date" value={order.ordered_date} />
                <Field label="Expected date (PO)" value={order.expected_date} />
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
                <Field label="Receiving" value={order.po_complete ? 'Complete' : order.received_started ? 'Started' : 'Not started'} />
                <Field label="SO created" value={order.created_date} />
                <Field
                  label="Days open"
                  value={
                    order.days_since_creation !== null ? (
                      <span className={cn(order.flag === 'aged' && 'font-semibold text-amber-600')}>
                        {order.days_since_creation}
                      </span>
                    ) : null
                  }
                />
              </div>

              <Separator />

              {/* Shopify (customer-promised) date + match */}
              <div className="grid grid-cols-2 gap-4">
                <Field
                  label="Shopify ETA"
                  value={
                    order.shopify_expected_date ? (
                      <span className="flex items-center gap-1.5">
                        {order.shopify_expected_date}
                        {order.shopify_match === 'ambiguous' && <ShopifyMatchBadge match="ambiguous" />}
                      </span>
                    ) : null
                  }
                />
                <Field
                  label="Shopify order"
                  value={
                    order.shopify_order_url
                      ? <a href={order.shopify_order_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">{order.shopify_order_name ?? 'order'}</a>
                      : order.shopify_order_name
                  }
                />
              </div>

              <Separator />

              {/* Deep links */}
              <div className="flex flex-col gap-2">
                <span className="text-muted-foreground text-[11px] font-medium uppercase tracking-wide">
                  Open in Lightspeed
                </span>
                <LightspeedLink url={order.ls_item_url} label="Product" icon={Package} />
                <LightspeedLink url={order.ls_customer_url} label="Customer" icon={User} />
                <LightspeedLink url={order.ls_order_url} label="Purchase order" icon={FileText} />
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
