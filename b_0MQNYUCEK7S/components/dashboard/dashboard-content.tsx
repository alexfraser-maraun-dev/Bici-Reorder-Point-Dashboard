'use client'

import { useState, useCallback } from 'react'
import { toast } from 'sonner'
import { KpiCards } from './kpi-cards'
import { FilterBar } from './filter-bar'
import { SkuDataTable } from './sku-data-table'
import { BulkActionsBar } from './bulk-actions-bar'
import { RowDetailDrawer } from './row-detail-drawer'
import { useSkuData, useKpiSummary, useRecommendationRuns, pushToLightspeed, lockRows, unlockRows, updateOverride } from '@/lib/hooks'
import type { FilterState, SkuLocationRow } from '@/lib/types'

const initialFilters: FilterState = {
  search: '',
  locations: [],
  vendors: [],
  brands: [],
  categories: [],
  needsOrderOnly: false,
  changedOnly: false,
  lockedOnly: false,
  overriddenOnly: false,
  writebackFailedOnly: false,
  recommendationRunId: null,
}

export function DashboardContent() {
  const [filters, setFilters] = useState<FilterState>(initialFilters)
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [selectedRow, setSelectedRow] = useState<SkuLocationRow | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const { data, allData, isLoading, refetch } = useSkuData(filters)
  const { data: recommendationRuns } = useRecommendationRuns()
  const kpiSummary = useKpiSummary(allData)

  const handleRowClick = useCallback((row: SkuLocationRow) => {
    setSelectedRow(row)
    setDrawerOpen(true)
  }, [])

  const handleClearSelection = useCallback(() => {
    setSelectedRows([])
  }, [])

  const handleApprove = useCallback(async () => {
    // In a real app, this would mark rows as approved
    toast.success(`Approved ${selectedRows.length} row(s)`)
    setSelectedRows([])
  }, [selectedRows])

  const handleBulkPush = useCallback(async () => {
    const result = await pushToLightspeed(selectedRows)
    if (result.success) {
      toast.success(result.message)
      setSelectedRows([])
      refetch()
    } else {
      toast.error(result.message)
    }
  }, [selectedRows, refetch])

  const handleBulkLock = useCallback(async () => {
    await lockRows(selectedRows)
    toast.success(`Locked ${selectedRows.length} row(s)`)
    setSelectedRows([])
    refetch()
  }, [selectedRows, refetch])

  const handleBulkUnlock = useCallback(async () => {
    await unlockRows(selectedRows)
    toast.success(`Unlocked ${selectedRows.length} row(s)`)
    setSelectedRows([])
    refetch()
  }, [selectedRows, refetch])

  const handleExport = useCallback(() => {
    // In a real app, this would export to CSV/Excel
    toast.success(`Exported ${selectedRows.length} row(s) to CSV`)
  }, [selectedRows])

  const handleSinglePush = useCallback(async (rowId: string) => {
    const result = await pushToLightspeed([rowId])
    if (result.success) {
      toast.success('Successfully pushed to Lightspeed')
      refetch()
    } else {
      toast.error(result.message)
    }
  }, [refetch])

  const handleToggleLock = useCallback(async (rowId: string) => {
    const row = data.find(r => r.id === rowId)
    if (row?.locked) {
      await unlockRows([rowId])
      toast.success('Row unlocked')
    } else {
      await lockRows([rowId])
      toast.success('Row locked')
    }
    refetch()
  }, [data, refetch])

  const handleSaveOverride = useCallback(async (rowId: string, reorderPoint: number, desiredLevel: number) => {
    await updateOverride(rowId, reorderPoint, desiredLevel)
    toast.success('Override saved')
    refetch()
  }, [refetch])

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Inventory Dashboard</h1>
          <p className="text-muted-foreground text-sm">
            Review and manage SKU reorder points and desired inventory levels
          </p>
        </div>
      </div>

      {/* KPI Cards */}
      <KpiCards summary={kpiSummary} isLoading={isLoading} />

      {/* Filters */}
      <FilterBar
        filters={filters}
        onFiltersChange={setFilters}
        recommendationRuns={recommendationRuns}
      />

      {/* Data Table */}
      <SkuDataTable
        data={data}
        isLoading={isLoading}
        selectedRows={selectedRows}
        onSelectedRowsChange={setSelectedRows}
        onRowClick={handleRowClick}
      />

      {/* Bulk Actions Bar */}
      <BulkActionsBar
        selectedCount={selectedRows.length}
        onClearSelection={handleClearSelection}
        onApprove={handleApprove}
        onPush={handleBulkPush}
        onLock={handleBulkLock}
        onUnlock={handleBulkUnlock}
        onExport={handleExport}
      />

      {/* Row Detail Drawer */}
      <RowDetailDrawer
        row={selectedRow}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        onPush={handleSinglePush}
        onToggleLock={handleToggleLock}
        onSaveOverride={handleSaveOverride}
      />
    </div>
  )
}
