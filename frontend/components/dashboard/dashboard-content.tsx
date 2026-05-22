'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SheetsReplenishment } from './sheets-replenishment'
import { VendorLeadTimes } from './vendor-lead-times'
import { LayoutDashboard, Truck } from 'lucide-react'
import type { AdjustmentMode, VelocityMode } from './sheets-replenishment'

interface DashboardContentProps {
  data: any
  isLoading: boolean
  refetch: () => void
  forecastPeriod: number
  setForecastPeriod: (value: number) => void
  safetyDays: number
  setSafetyDays: (value: number) => void
  growthMultiplier: number
  setGrowthMultiplier: (value: number) => void
  velocityMode: VelocityMode
  setVelocityMode: (value: VelocityMode) => void
  customRecentWeight: number
  setCustomRecentWeight: (value: number) => void
  adjustmentMode: AdjustmentMode
  setAdjustmentMode: (value: AdjustmentMode) => void
}

export function DashboardContent(props: DashboardContentProps) {
  return (
    <div className="space-y-3 animate-in fade-in duration-700">
      <Tabs defaultValue="inventory" className="w-full">
        <TabsList className="grid w-[400px] grid-cols-2 mb-2 bg-muted/50 p-1 rounded-xl">
          <TabsTrigger value="inventory" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <LayoutDashboard className="w-4 h-4" />
            Inventory Dashboard
          </TabsTrigger>
          <TabsTrigger value="vendors" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <Truck className="w-4 h-4" />
            Vendor Lead Times
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="inventory" className="mt-0 border-none p-0 focus-visible:ring-0">
          <SheetsReplenishment {...props} />
        </TabsContent>
        
        <TabsContent value="vendors" className="mt-0 border-none p-0 focus-visible:ring-0">
          <VendorLeadTimes />
        </TabsContent>
      </Tabs>
    </div>
  )
}
