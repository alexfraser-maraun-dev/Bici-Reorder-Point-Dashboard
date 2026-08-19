'use client'

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { AppShell } from '@/components/layout/app-shell'
import { FeatureGate } from '@/components/layout/feature-gate'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { useReplenishmentData } from '@/lib/hooks'
import { cn } from '@/lib/utils'
import { DashboardContent, useVisibleDashboardTabs, type DashboardTab } from './dashboard-content'
import type { AdjustmentMode, DemandWeights } from './sheets-replenishment'

// Where the app opens. Falls back to the first tab still switched on if this one
// has been turned off.
const DEFAULT_TAB: DashboardTab = 'po-tracker'

export function DashboardPageClient() {
  // PO Tracker is the landing surface. The rest of the cockpit — and the legacy
  // inventory engine with its BigQuery/Pandas workload — opens only on demand,
  // and only for tabs switched on in the Admin page.
  const [activeTab, setActiveTab] = useState<DashboardTab>(DEFAULT_TAB)
  const { tabs: visibleTabs, isLoading: accessLoading } = useVisibleDashboardTabs()
  const [forecastPeriod, setForecastPeriod] = useState(60)
  const [safetyDays, setSafetyDays] = useState(7)
  const [growthMultiplier, setGrowthMultiplier] = useState(1.0)
  const [demandWeights, setDemandWeights] = useState<DemandWeights>({
    weight14d: 40,
    weight15To30d: 40,
    weight31To60d: 20,
  })
  const [adjustmentMode, setAdjustmentMode] = useState<AdjustmentMode>('shrink')

  const [debouncedForecast, setDebouncedForecast] = useState(forecastPeriod)
  const [debouncedSafety, setDebouncedSafety] = useState(safetyDays)
  const [debouncedDemandWeights, setDebouncedDemandWeights] = useState(demandWeights)
  const demandWeightTotal = demandWeights.weight14d + demandWeights.weight15To30d + demandWeights.weight31To60d
  const isDemandWeightValid = demandWeightTotal === 100

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedForecast(forecastPeriod)
      setDebouncedSafety(safetyDays)
      if (isDemandWeightValid) setDebouncedDemandWeights(demandWeights)
    }, 300)
    return () => clearTimeout(timer)
  }, [forecastPeriod, safetyDays, demandWeights, isDemandWeightValid])

  // If the active tab gets switched off (or was never on for this user), land on
  // the default, else the first tab they do have. Keyed on the tab list itself so
  // this settles once when access resolves rather than on every render.
  const visibleTabKeys = visibleTabs.map((tab) => tab.value).join(',')
  useEffect(() => {
    if (accessLoading || !visibleTabKeys) return
    const keys = visibleTabKeys.split(',') as DashboardTab[]
    if (keys.includes(activeTab)) return
    setActiveTab(keys.includes(DEFAULT_TAB) ? DEFAULT_TAB : keys[0])
  }, [accessLoading, visibleTabKeys, activeTab])

  const inventoryVisible = visibleTabs.some((tab) => tab.value === 'inventory')

  const { data, isLoading, refetch } = useReplenishmentData(
    debouncedForecast,
    debouncedSafety,
    growthMultiplier,
    debouncedDemandWeights,
    adjustmentMode,
    inventoryVisible && isDemandWeightValid && activeTab === 'inventory'
  )
  const headerActions = inventoryVisible && activeTab === 'inventory' ? (
    <Button
      variant="secondary"
      className="h-8 gap-2 text-xs font-semibold border"
      onClick={() => refetch()}
      disabled={isLoading}
    >
      <RefreshCw className={cn("w-3 h-3", isLoading && "animate-spin")} />
      {isLoading ? "Syncing..." : "Sync Product Data"}
    </Button>
  ) : undefined

  return (
    <AppShell headerActions={headerActions} mainClassName="p-2 lg:p-3">
      <FeatureGate feature="ordering">
      <DashboardContent
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        data={data}
        isLoading={isLoading}
        refetch={refetch}
        forecastPeriod={forecastPeriod}
        setForecastPeriod={setForecastPeriod}
        safetyDays={safetyDays}
        setSafetyDays={setSafetyDays}
        growthMultiplier={growthMultiplier}
        setGrowthMultiplier={setGrowthMultiplier}
        demandWeights={demandWeights}
        setDemandWeights={setDemandWeights}
        demandWeightTotal={demandWeightTotal}
        isDemandWeightValid={isDemandWeightValid}
        adjustmentMode={adjustmentMode}
        setAdjustmentMode={setAdjustmentMode}
      />
      </FeatureGate>
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
