'use client'

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { AppShell } from '@/components/layout/app-shell'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { useConnectionStatus, useReplenishmentData } from '@/lib/hooks'
import { cn } from '@/lib/utils'
import { DashboardContent } from './dashboard-content'
import type { AdjustmentMode, VelocityMode } from './sheets-replenishment'

const VELOCITY_PRESETS: Record<Exclude<VelocityMode, 'custom'>, number> = {
  stable: 0.5,
  balanced: 0.7,
  reactive: 0.85,
}

function StatusIndicator({
  label,
  status,
}: {
  label: string
  status: 'checking' | 'connected' | 'disconnected'
}) {
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <div className={cn(
        "h-1.5 w-1.5 rounded-full",
        status === 'connected' ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.3)]" :
        status === 'checking' ? "bg-yellow-500 animate-pulse" : "bg-red-500"
      )} />
      <span className="text-[10px] font-medium text-foreground/70">{label}</span>
      <span className={cn(
        "text-[9px] font-bold uppercase tracking-tight",
        status === 'connected' ? "text-emerald-600" :
        status === 'checking' ? "text-yellow-600" : "text-red-600"
      )}>
        {status}
      </span>
    </div>
  )
}

export function DashboardPageClient() {
  const [forecastPeriod, setForecastPeriod] = useState(60)
  const [safetyDays, setSafetyDays] = useState(7)
  const [growthMultiplier, setGrowthMultiplier] = useState(1.0)
  const [velocityMode, setVelocityMode] = useState<VelocityMode>('balanced')
  const [customRecentWeight, setCustomRecentWeight] = useState(70)
  const [adjustmentMode, setAdjustmentMode] = useState<AdjustmentMode>('shrink')

  const recent30dWeight = velocityMode === 'custom'
    ? customRecentWeight / 100
    : VELOCITY_PRESETS[velocityMode]

  const [debouncedForecast, setDebouncedForecast] = useState(forecastPeriod)
  const [debouncedSafety, setDebouncedSafety] = useState(safetyDays)
  const [debouncedRecent30dWeight, setDebouncedRecent30dWeight] = useState(recent30dWeight)

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedForecast(forecastPeriod)
      setDebouncedSafety(safetyDays)
      setDebouncedRecent30dWeight(recent30dWeight)
    }, 300)
    return () => clearTimeout(timer)
  }, [forecastPeriod, safetyDays, recent30dWeight])

  const { data, isLoading, refetch } = useReplenishmentData(
    debouncedForecast,
    debouncedSafety,
    growthMultiplier,
    debouncedRecent30dWeight,
    adjustmentMode
  )
  const { lsStatus, bqStatus } = useConnectionStatus()

  const headerActions = (
    <>
      <Button
        variant="secondary"
        className="h-8 gap-2 text-xs font-semibold border"
        onClick={() => refetch()}
        disabled={isLoading}
      >
        <RefreshCw className={cn("w-3 h-3", isLoading && "animate-spin")} />
        {isLoading ? "Syncing..." : "Sync Product Data"}
      </Button>
      <div className="hidden h-5 w-px bg-border 2xl:block" />
      <StatusIndicator label="Lightspeed" status={lsStatus} />
      <StatusIndicator label="BigQuery" status={bqStatus} />
    </>
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
        velocityMode={velocityMode}
        setVelocityMode={setVelocityMode}
        customRecentWeight={customRecentWeight}
        setCustomRecentWeight={setCustomRecentWeight}
        adjustmentMode={adjustmentMode}
        setAdjustmentMode={setAdjustmentMode}
      />
      <Toaster position="bottom-right" />
    </AppShell>
  )
}
