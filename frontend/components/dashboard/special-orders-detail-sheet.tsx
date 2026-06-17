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
import { AgingBadge, SpecialOrderStatusBadge, ReadyNotCalledBadge } from './special-order-badges'
import { ExternalLink, Package, User, FileText } from 'lucide-react'

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

interface Props {
  order: SpecialOrder | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SpecialOrderDetailSheet({ order, open, onOpenChange }: Props) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[480px]">
        {order && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <span className="font-mono text-sm">SO #{order.special_order_id}</span>
                <SpecialOrderStatusBadge status={order.status} />
              </SheetTitle>
              <SheetDescription>{order.description ?? 'Special order'}</SheetDescription>
            </SheetHeader>

            <div className="flex flex-col gap-4 px-4 pb-6">
              <div className="flex flex-wrap items-center gap-2">
                <AgingBadge bucket={order.aging_bucket} daysOverdue={order.days_overdue} />
                <ReadyNotCalledBadge active={order.ready_not_called} />
              </div>

              <Separator />

              <div className="grid grid-cols-2 gap-4">
                <Field label="Customer" value={order.customer_name} />
                <Field label="Phone" value={order.customer_phone} />
                <Field label="Store" value={order.store} />
                <Field label="Quantity" value={order.unit_quantity} />
                <Field label="SKU" value={order.system_sku ? <span className="font-mono text-xs">{order.system_sku}</span> : null} />
                <Field label="Vendor" value={order.vendor_name} />
              </div>

              <Separator />

              <div className="grid grid-cols-2 gap-4">
                <Field label="PO #" value={order.order_id} />
                <Field label="Expected date" value={order.expected_date} />
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
              </div>

              <Separator />

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
