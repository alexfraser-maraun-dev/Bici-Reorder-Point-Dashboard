'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SheetsReplenishment } from './sheets-replenishment'
import { VendorLeadTimes } from './vendor-lead-times'
import { LayoutDashboard, Truck, History } from 'lucide-react'
import { WritebackLogs } from './writeback-logs'

export function DashboardContent() {
  return (
    <div className="space-y-6 animate-in fade-in duration-700">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text">
            Replenishment Automation
          </h1>
          <p className="text-muted-foreground text-sm font-medium mt-1">
            Manual Ingestion Workflow • Live Google Sheets Sync
          </p>
        </div>
      </div>

      <Tabs defaultValue="inventory" className="w-full">
        <TabsList className="grid w-[400px] grid-cols-2 mb-4 bg-muted/50 p-1 rounded-xl">
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
          <SheetsReplenishment />
        </TabsContent>
        
        <TabsContent value="vendors" className="mt-0 border-none p-0 focus-visible:ring-0">
          <VendorLeadTimes />
        </TabsContent>
      </Tabs>
    </div>
  )
}
