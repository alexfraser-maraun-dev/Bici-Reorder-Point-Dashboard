'use client'

import { useState, useMemo } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  type ColumnDef,
  flexRender,
} from '@tanstack/react-table'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { SkuLocationRow } from '@/lib/types'
import {
  WritebackStatusBadge,
  NeedsOrderBadge,
  ChangedBadge,
  LockedBadge,
  OverrideBadge,
} from './status-badges'
import { ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react'

interface SkuDataTableProps {
  data: SkuLocationRow[]
  isLoading: boolean
  selectedRows: string[]
  onSelectedRowsChange: (rows: string[]) => void
  onRowClick: (row: SkuLocationRow) => void
}

export function SkuDataTable({
  data,
  isLoading,
  selectedRows,
  onSelectedRowsChange,
  onRowClick,
}: SkuDataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([])

  const columns: ColumnDef<SkuLocationRow>[] = useMemo(
    () => [
      {
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && 'indeterminate')
            }
            onCheckedChange={(value) => {
              table.toggleAllPageRowsSelected(!!value)
              if (value) {
                onSelectedRowsChange(data.map((r) => r.id))
              } else {
                onSelectedRowsChange([])
              }
            }}
            aria-label="Select all"
            className="h-3.5 w-3.5"
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={selectedRows.includes(row.original.id)}
            onCheckedChange={(value) => {
              if (value) {
                onSelectedRowsChange([...selectedRows, row.original.id])
              } else {
                onSelectedRowsChange(selectedRows.filter((id) => id !== row.original.id))
              }
            }}
            aria-label="Select row"
            className="h-3.5 w-3.5"
            onClick={(e) => e.stopPropagation()}
          />
        ),
        enableSorting: false,
        size: 40,
      },
      {
        accessorKey: 'sku',
        header: ({ column }) => <SortableHeader column={column} title="SKU" />,
        cell: ({ row }) => (
          <span className="font-mono text-xs font-medium">{row.original.sku}</span>
        ),
        size: 100,
      },
      {
        accessorKey: 'product',
        header: ({ column }) => <SortableHeader column={column} title="Product" />,
        cell: ({ row }) => (
          <span className="max-w-[150px] truncate text-sm">{row.original.product}</span>
        ),
        size: 150,
      },
      {
        accessorKey: 'brand',
        header: ({ column }) => <SortableHeader column={column} title="Brand" />,
        cell: ({ row }) => <span className="text-xs">{row.original.brand}</span>,
        size: 100,
      },
      {
        accessorKey: 'vendor',
        header: ({ column }) => <SortableHeader column={column} title="Vendor" />,
        cell: ({ row }) => (
          <span className="text-muted-foreground max-w-[120px] truncate text-xs">
            {row.original.vendor}
          </span>
        ),
        size: 130,
      },
      {
        accessorKey: 'category',
        header: ({ column }) => <SortableHeader column={column} title="Category" />,
        cell: ({ row }) => <span className="text-xs">{row.original.category}</span>,
        size: 120,
      },
      {
        accessorKey: 'location',
        header: ({ column }) => <SortableHeader column={column} title="Location" />,
        cell: ({ row }) => <span className="text-xs">{row.original.location}</span>,
        size: 130,
      },
      {
        accessorKey: 'trailingUnitsSold',
        header: ({ column }) => <SortableHeader column={column} title="Trailing Sold" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.trailingUnitsSold}</span>
        ),
        size: 90,
      },
      {
        accessorKey: 'daysOutOfStock',
        header: ({ column }) => <SortableHeader column={column} title="Days OOS" />,
        cell: ({ row }) => {
          const days = row.original.daysOutOfStock
          return (
            <span
              className={cn('tabular-nums text-xs', days > 0 && 'text-red-600 font-medium')}
            >
              {days}
            </span>
          )
        },
        size: 80,
      },
      {
        accessorKey: 'avgDailySales',
        header: ({ column }) => <SortableHeader column={column} title="Avg Daily" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.avgDailySales.toFixed(1)}</span>
        ),
        size: 80,
      },
      {
        accessorKey: 'leadTimeDays',
        header: ({ column }) => <SortableHeader column={column} title="Lead Days" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.leadTimeDays}</span>
        ),
        size: 80,
      },
      {
        accessorKey: 'onHand',
        header: ({ column }) => <SortableHeader column={column} title="On Hand" />,
        cell: ({ row }) => {
          const onHand = row.original.onHand
          return (
            <span
              className={cn('tabular-nums text-xs', onHand === 0 && 'text-red-600 font-medium')}
            >
              {onHand}
            </span>
          )
        },
        size: 70,
      },
      {
        accessorKey: 'onOrder',
        header: ({ column }) => <SortableHeader column={column} title="On Order" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.onOrder}</span>
        ),
        size: 70,
      },
      {
        accessorKey: 'inventoryPosition',
        header: ({ column }) => <SortableHeader column={column} title="Inv Pos" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs font-medium">
            {row.original.inventoryPosition}
          </span>
        ),
        size: 70,
      },
      {
        accessorKey: 'currentReorderPoint',
        header: ({ column }) => <SortableHeader column={column} title="Curr ROP" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.currentReorderPoint}</span>
        ),
        size: 80,
      },
      {
        accessorKey: 'recommendedReorderPoint',
        header: ({ column }) => <SortableHeader column={column} title="Rec ROP" />,
        cell: ({ row }) => {
          const curr = row.original.currentReorderPoint
          const rec = row.original.recommendedReorderPoint
          const changed = curr !== rec
          return (
            <span
              className={cn(
                'tabular-nums text-xs',
                changed && 'text-blue-600 font-medium'
              )}
            >
              {rec}
            </span>
          )
        },
        size: 80,
      },
      {
        accessorKey: 'currentDesiredLevel',
        header: ({ column }) => <SortableHeader column={column} title="Curr Desired" />,
        cell: ({ row }) => (
          <span className="tabular-nums text-xs">{row.original.currentDesiredLevel}</span>
        ),
        size: 90,
      },
      {
        accessorKey: 'recommendedDesiredLevel',
        header: ({ column }) => <SortableHeader column={column} title="Rec Desired" />,
        cell: ({ row }) => {
          const curr = row.original.currentDesiredLevel
          const rec = row.original.recommendedDesiredLevel
          const changed = curr !== rec
          return (
            <span
              className={cn(
                'tabular-nums text-xs',
                changed && 'text-blue-600 font-medium'
              )}
            >
              {rec}
            </span>
          )
        },
        size: 90,
      },
      {
        accessorKey: 'suggestedBuyQty',
        header: ({ column }) => <SortableHeader column={column} title="Buy Qty" />,
        cell: ({ row }) => {
          const qty = row.original.suggestedBuyQty
          return (
            <span
              className={cn(
                'tabular-nums text-xs',
                qty > 0 && 'text-amber-600 font-medium'
              )}
            >
              {qty}
            </span>
          )
        },
        size: 70,
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1">
            <NeedsOrderBadge needsOrder={row.original.needsOrder} />
            <ChangedBadge changed={row.original.changed} />
            <LockedBadge locked={row.original.locked} />
            <OverrideBadge override={row.original.override} />
          </div>
        ),
        size: 140,
      },
      {
        accessorKey: 'writebackStatus',
        header: ({ column }) => <SortableHeader column={column} title="Writeback" />,
        cell: ({ row }) => <WritebackStatusBadge status={row.original.writebackStatus} />,
        size: 90,
      },
    ],
    [data, selectedRows, onSelectedRowsChange]
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (isLoading) {
    return (
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              {Array.from({ length: 12 }).map((_, i) => (
                <TableHead key={i}>
                  <Skeleton className="h-4 w-16" />
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: 10 }).map((_, i) => (
              <TableRow key={i}>
                {Array.from({ length: 12 }).map((_, j) => (
                  <TableCell key={j}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )
  }

  return (
    <div className="rounded-lg border">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader className="bg-muted/50 sticky top-0">
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    style={{ width: header.column.getSize() }}
                    className="text-xs"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-24 text-center">
                  <div className="text-muted-foreground">
                    <p className="font-medium">No results found</p>
                    <p className="text-sm">Try adjusting your filters</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  className={cn(
                    'cursor-pointer',
                    selectedRows.includes(row.original.id) && 'bg-muted/50',
                    row.original.needsOrder && 'bg-amber-50/50',
                    row.original.writebackStatus === 'failed' && 'bg-red-50/50'
                  )}
                  onClick={() => onRowClick(row.original)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="py-2">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <div className="bg-muted/30 border-t px-4 py-2">
        <p className="text-muted-foreground text-xs">
          Showing {table.getRowModel().rows.length} of {data.length} rows
          {selectedRows.length > 0 && ` · ${selectedRows.length} selected`}
        </p>
      </div>
    </div>
  )
}

// Sortable header component
function SortableHeader({
  column,
  title,
}: {
  column: { getIsSorted: () => false | 'asc' | 'desc'; toggleSorting: (desc?: boolean) => void }
  title: string
}) {
  const sorted = column.getIsSorted()

  return (
    <Button
      variant="ghost"
      size="sm"
      className="-ml-3 h-8 text-xs font-medium"
      onClick={() => column.toggleSorting(sorted === 'asc')}
    >
      {title}
      {sorted === 'asc' ? (
        <ArrowUp className="ml-1 h-3 w-3" />
      ) : sorted === 'desc' ? (
        <ArrowDown className="ml-1 h-3 w-3" />
      ) : (
        <ArrowUpDown className="ml-1 h-3 w-3 opacity-50" />
      )}
    </Button>
  )
}
