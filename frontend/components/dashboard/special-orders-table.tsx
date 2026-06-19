'use client'

import { useState, useMemo } from 'react'
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
import { StageBadge, FlagBadge, ShopifyMatchBadge } from './special-order-badges'
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'

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

function SortIcon({ col, active, dir }: { col: string; active: boolean; dir: SortDir }) {
  if (!active) return <ChevronsUpDown className="ml-1 inline h-3 w-3 opacity-40" />
  return dir === 'asc'
    ? <ChevronUp className="ml-1 inline h-3 w-3" />
    : <ChevronDown className="ml-1 inline h-3 w-3" />
}

interface Props {
  orders: SpecialOrder[]
  isLoading?: boolean
  onRowClick: (order: SpecialOrder) => void
}

export function SpecialOrdersTable({ orders, isLoading, onRowClick }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('created_date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = useMemo(
    () => [...orders].sort((a, b) => compare(a, b, sortKey, sortDir)),
    [orders, sortKey, sortDir]
  )

  function th(label: string, key: SortKey, className?: string) {
    const active = sortKey === key
    return (
      <TableHead
        className={cn('cursor-pointer select-none whitespace-nowrap', className)}
        onClick={() => handleSort(key)}
      >
        <span className={cn('inline-flex items-center', active && 'text-foreground font-semibold')}>
          {label}
          <SortIcon col={key} active={active} dir={sortDir} />
        </span>
      </TableHead>
    )
  }

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
            {th('SO #', 'special_order_id', 'w-[80px]')}
            {th('Customer', 'customer_name')}
            {th('Product', 'description')}
            {th('Vendor', 'vendor_name')}
            {th('Store', 'store', 'hidden md:table-cell')}
            {th('PO #', 'order_id', 'hidden lg:table-cell')}
            {th('Ordered', 'ordered_date', 'hidden xl:table-cell')}
            {th('PO date', 'expected_date')}
            {th('Shopify ETA', 'shopify_expected_date')}
            {th('Created', 'created_date', 'hidden xl:table-cell')}
            {th('Stage', 'procurement_stage_index')}
            {th('Flag', 'flag', 'text-right')}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((o) => (
            <TableRow
              key={`${o.kind ?? 'ls'}-${o.special_order_id}`}
              onClick={() => onRowClick(o)}
              className={cn(
                'cursor-pointer',
                o.flag === 'overdue' && 'bg-red-50/60 hover:bg-red-50',
                o.flag === 'overdue_mid' && 'bg-red-100/70 hover:bg-red-100',
                o.flag === 'critical' && 'bg-red-200/70 hover:bg-red-200'
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
              <TableCell className="hidden whitespace-nowrap text-sm xl:table-cell">{o.ordered_date ?? '—'}</TableCell>
              <TableCell className="whitespace-nowrap text-sm">{o.expected_date ?? '—'}</TableCell>
              <TableCell className="whitespace-nowrap text-sm">
                {o.shopify_expected_date ?? '—'}
                {o.shopify_match === 'ambiguous' && (
                  <span className="ml-1 text-[10px] text-amber-600" title="Multiple Shopify orders match — verify">≈</span>
                )}
              </TableCell>
              <TableCell className="hidden whitespace-nowrap text-sm xl:table-cell">
                {o.created_date ?? '—'}
              </TableCell>
              <TableCell>
                <StageBadge stage={o.kind === 'shopify' ? 'shopify' : o.procurement_stage} />
              </TableCell>
              <TableCell className="text-right">
                {o.kind === 'shopify'
                  ? <ShopifyMatchBadge match="none" />
                  : <FlagBadge stage={o.procurement_stage} flag={o.flag} daysOverdue={o.days_overdue} />}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
