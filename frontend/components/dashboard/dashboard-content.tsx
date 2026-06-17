'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SheetsReplenishment } from './sheets-replenishment'
import { VendorLeadTimes } from './vendor-lead-times'
import { BrandConfiguration } from './brand-configuration'
import { PurchaseOrders } from './purchase-orders'
import { DemandInsights } from './demand-insights'
import { LayoutDashboard, SlidersHorizontal, Truck, ShoppingCart, TrendingUp } from 'lucide-react'
import type { AdjustmentMode, DemandWeights } from './sheets-replenishment'

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
  demandWeights: DemandWeights
  setDemandWeights: (value: DemandWeights | ((previous: DemandWeights) => DemandWeights)) => void
  demandWeightTotal: number
  isDemandWeightValid: boolean
  adjustmentMode: AdjustmentMode
  setAdjustmentMode: (value: AdjustmentMode) => void
}

export function DashboardContent(props: DashboardContentProps) {
  return (
    <div className="space-y-3 animate-in fade-in duration-700">
      <Tabs defaultValue="inventory" className="w-full">
        <TabsList className="grid w-[1020px] grid-cols-5 mb-2 bg-muted/50 p-1 rounded-xl">
          <TabsTrigger value="inventory" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <LayoutDashboard className="w-4 h-4" />
            Inventory Dashboard
          </TabsTrigger>
          <TabsTrigger value="demand" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <TrendingUp className="w-4 h-4" />
            Demand &amp; Seasonality
          </TabsTrigger>
          <TabsTrigger value="purchase-orders" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <ShoppingCart className="w-4 h-4" />
            Purchase Orders
          </TabsTrigger>
          <TabsTrigger value="vendors" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <Truck className="w-4 h-4" />
            Vendor Lead Times
          </TabsTrigger>
          <TabsTrigger value="brands" className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm">
            <SlidersHorizontal className="w-4 h-4" />
            Brand Configuration
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="inventory" className="mt-0 border-none p-0 focus-visible:ring-0">
          <SheetsReplenishment {...props} />
        </TabsContent>

        <TabsContent value="demand" className="mt-0 border-none p-0 focus-visible:ring-0">
          <DemandInsights />
        </TabsContent>

        <TabsContent value="purchase-orders" className="mt-0 border-none p-0 focus-visible:ring-0">
          <PurchaseOrders data={props.data} isLoading={props.isLoading} />
        </TabsContent>

        <TabsContent value="vendors" className="mt-0 border-none p-0 focus-visible:ring-0">
          <VendorLeadTimes />
        </TabsContent>

        <TabsContent value="brands" className="mt-0 border-none p-0 focus-visible:ring-0">
          <BrandConfiguration />
        </TabsContent>
      </Tabs>
    </div>
  )
}
