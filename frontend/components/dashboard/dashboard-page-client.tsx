'use client'

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { AppShell } from '@/components/layout/app-shell'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { useReplenishmentData } from '@/lib/hooks'
import { cn } from '@/lib/utils'
import { DashboardContent } from './dashboard-content'
import type { AdjustmentMode, DemandWeights } from './sheets-replenishment'

export function DashboardPageClient() {
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

  const { data, isLoading, refetch } = useReplenishmentData(
    debouncedForecast,
    debouncedSafety,
    growthMultiplier,
    debouncedDemandWeights,
    adjustmentMode,
    isDemandWeightValid
  )
  const headerActions = (
    <Button
      variant="secondary"
      className="h-8 gap-2 text-xs font-semibold border"
      onClick={() => refetch()}
      disabled={isLoading}
    >
      <RefreshCw className={cn("w-3 h-3", isLoading && "animate-spin")} />
      {isLoading ? "Syncing..." : "Sync Product Data"}
    </Button>
  )

  return (
    <AppShell headerActions={headerActions} mainClassName="p-2 lg:p-3">
      <DashboardContent
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
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
