'use client'

import dynamic from 'next/dynamic'
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from '@/components/ui/skeleton'
import { LayoutDashboard, PackageSearch, SlidersHorizontal, Truck, ShoppingCart, TrendingUp } from 'lucide-react'
import { useAccess } from '@/lib/access/hooks'
import type { FeatureKey } from '@/lib/access/types'
import type { AdjustmentMode, DemandWeights } from './sheets-replenishment'

// Radix already unmounts the inactive tab panels, so only one tab is ever rendered.
// These dynamic imports make the *code* follow the same rule: each tab's JavaScript is
// fetched when the buyer first opens it, not on every page load. That matters most for
// Demand & Seasonality, which pulls in the ~380 KB charting library — the landing tab
// (PO Tracker) draws no charts at all and used to download it anyway.
//
// ssr: false is safe here: this is a Client Component, and every tab is a live
// dashboard whose content comes from the API after hydration regardless.
const TabFallback = () => (
  <div className="space-y-2 p-4" role="status" aria-label="Loading">
    <Skeleton className="h-8" />
    <Skeleton className="h-8" />
    <Skeleton className="h-8" />
  </div>
)

const SheetsReplenishment = dynamic(
  () => import('./sheets-replenishment').then((m) => m.SheetsReplenishment),
  { ssr: false, loading: TabFallback },
)
const VendorLeadTimes = dynamic(
  () => import('./vendor-lead-times').then((m) => m.VendorLeadTimes),
  { ssr: false, loading: TabFallback },
)
const BrandConfiguration = dynamic(
  () => import('./brand-configuration').then((m) => m.BrandConfiguration),
  { ssr: false, loading: TabFallback },
)
const PurchaseOrders = dynamic(
  () => import('./purchase-orders').then((m) => m.PurchaseOrders),
  { ssr: false, loading: TabFallback },
)
const PoTracker = dynamic(
  () => import('./po-tracker').then((m) => m.PoTracker),
  { ssr: false, loading: TabFallback },
)
const DemandInsights = dynamic(
  () => import('./demand-insights').then((m) => m.DemandInsights),
  { ssr: false, loading: TabFallback },
)

export type DashboardTab = 'inventory' | 'demand' | 'purchase-orders' | 'po-tracker' | 'vendors' | 'brands'

// Tab order on screen. Each tab is gated on its feature key: a tab whose feature
// is off renders neither trigger nor content, so its component never mounts and
// never fetches. Toggle them in the Admin page.
export const DASHBOARD_TABS: {
  value: DashboardTab
  feature: FeatureKey
  label: string
  icon: typeof LayoutDashboard
}[] = [
  { value: 'inventory', feature: 'ordering.inventory', label: 'Inventory Dashboard', icon: LayoutDashboard },
  { value: 'demand', feature: 'ordering.demand', label: 'Demand & Seasonality', icon: TrendingUp },
  { value: 'purchase-orders', feature: 'ordering.purchase_orders', label: 'Purchase Orders', icon: ShoppingCart },
  { value: 'po-tracker', feature: 'ordering.po_tracker', label: 'PO Tracker', icon: PackageSearch },
  { value: 'vendors', feature: 'ordering.vendors', label: 'Vendor Lead Times', icon: Truck },
  { value: 'brands', feature: 'ordering.brands', label: 'Brand Configuration', icon: SlidersHorizontal },
]

/** The tabs this user can see, in display order. */
export function useVisibleDashboardTabs() {
  const { isEnabled, isLoading } = useAccess()
  return { tabs: DASHBOARD_TABS.filter((tab) => isEnabled(tab.feature)), isLoading }
}

interface DashboardContentProps {
  activeTab: DashboardTab
  setActiveTab: (value: DashboardTab) => void
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
  const { tabs } = useVisibleDashboardTabs()
  const visible = (tab: DashboardTab) => tabs.some((t) => t.value === tab)

  if (tabs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        No ordering tabs are switched on for your account.
      </div>
    )
  }

  return (
    <div className="space-y-3 animate-in fade-in duration-700">
      <Tabs
        value={props.activeTab}
        onValueChange={(value) => props.setActiveTab(value as DashboardTab)}
        className="w-full"
      >
        <TabsList
          className="mb-2 grid bg-muted/50 p-1 rounded-xl"
          style={{
            width: `${tabs.length * 197}px`,
            gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))`,
          }}
        >
          {tabs.map((tab) => (
            <TabsTrigger
              key={tab.value}
              value={tab.value}
              className="gap-2 rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm"
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {visible('inventory') && (
          <TabsContent value="inventory" className="mt-0 border-none p-0 focus-visible:ring-0">
            <SheetsReplenishment {...props} />
          </TabsContent>
        )}

        {visible('demand') && (
          <TabsContent value="demand" className="mt-0 border-none p-0 focus-visible:ring-0">
            <DemandInsights />
          </TabsContent>
        )}

        {visible('purchase-orders') && (
          <TabsContent value="purchase-orders" className="mt-0 border-none p-0 focus-visible:ring-0">
            <PurchaseOrders />
          </TabsContent>
        )}

        {visible('po-tracker') && (
          <TabsContent value="po-tracker" className="mt-0 border-none p-0 focus-visible:ring-0">
            <PoTracker />
          </TabsContent>
        )}

        {visible('vendors') && (
          <TabsContent value="vendors" className="mt-0 border-none p-0 focus-visible:ring-0">
            <VendorLeadTimes />
          </TabsContent>
        )}

        {visible('brands') && (
          <TabsContent value="brands" className="mt-0 border-none p-0 focus-visible:ring-0">
            <BrandConfiguration />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
