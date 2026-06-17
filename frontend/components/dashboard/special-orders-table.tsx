'use client'

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { SpecialOrder } from '@/lib/types'
import { AgingBadge } from './special-order-badges'

interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  onRowClick: (order: SpecialOrder) => void
}

export function SpecialOrdersTable({ orders, isLoading, onRowClick }: Props) {
  if (isLoading) {
    return (
      <div className="space-y-2 rounded-md border p-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
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
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[80px]">SO #</TableHead>
            <TableHead>Customer</TableHead>
            <TableHead>Product</TableHead>
            <TableHead>Vendor</TableHead>
            <TableHead className="hidden md:table-cell">Store</TableHead>
            <TableHead className="hidden lg:table-cell">PO #</TableHead>
            <TableHead>Expected</TableHead>
            <TableHead className="text-right">Overdue</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.map((o) => (
            <TableRow
              key={o.special_order_id}
              onClick={() => onRowClick(o)}
              className={cn(
                'cursor-pointer',
                o.is_overdue && 'bg-red-50/50 hover:bg-red-50'
              )}
            >
              <TableCell className="font-mono text-xs">{o.special_order_id}</TableCell>
              <TableCell className="max-w-[140px] truncate">{o.customer_name ?? '—'}</TableCell>
              <TableCell className="max-w-[220px] truncate" title={o.description ?? ''}>
                {o.description ?? '—'}
              </TableCell>
              <TableCell className="max-w-[120px] truncate">{o.vendor_name ?? '—'}</TableCell>
              <TableCell className="hidden md:table-cell">{o.store ?? '—'}</TableCell>
              <TableCell className="hidden font-mono text-xs lg:table-cell">{o.order_id ?? '—'}</TableCell>
              <TableCell className="whitespace-nowrap text-sm">{o.expected_date ?? '—'}</TableCell>
              <TableCell className="text-right tabular-nums">
                {o.days_overdue !== null && o.days_overdue > 0 ? (
                  <span className="font-semibold text-red-600">{o.days_overdue}d</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell>
                <AgingBadge bucket={o.aging_bucket} daysOverdue={o.days_overdue} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
