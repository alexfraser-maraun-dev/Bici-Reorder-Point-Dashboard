'use client'

import { AppShell } from '@/components/layout/app-shell'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { 
  Zap, 
  RefreshCw, 
  Settings2, 
  ShieldCheck, 
  History, 
  Package,
  Info,
  Database,
  ShoppingCart
} from 'lucide-react'

export default function HowToUsePage() {
  return (
    <AppShell>
      <div className="max-w-4xl mx-auto space-y-8 py-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">How the Config Tool Works</h1>
          <p className="text-muted-foreground text-lg">
            A guide to the data sources, demand logic, and writeback workflow powering replenishment.
          </p>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          {/* Step 1: Syncing */}
          <Card className="shadow-sm border-blue-100 bg-blue-50/10">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-blue-700">
                <RefreshCw className="w-5 h-5" /> 1. Sync Product Data
              </CardTitle>
              <CardDescription>Refreshing the latest trusted BigQuery snapshot</CardDescription>
            </CardHeader>
            <CardContent className="text-sm space-y-3">
              <p>
                Click <strong>"Sync Product Data"</strong> to refresh qualified replenishment items from BigQuery. The app
                starts with products tagged <strong>auto-replen</strong>, then reads current inventory, sales, and open PO
                values from <strong>v_master_snapshot_latest</strong>.
              </p>
              <p className="text-muted-foreground italic">
                Tip: Data is cached for 5 minutes automatically. Use this button to force a fresh read after a BigQuery sync or view update.
              </p>
            </CardContent>
          </Card>

          {/* Step 2: Analysis */}
          <Card className="shadow-sm border-purple-100 bg-purple-50/10">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-purple-700">
                <Zap className="w-5 h-5" /> 2. Proactive Analysis
              </CardTitle>
              <CardDescription>Understanding the Urgency logic</CardDescription>
            </CardHeader>
            <CardContent className="text-sm space-y-3">
              <p>
                The tool compares each location's QOH against the recommended Reorder Point and Desired Level. Status
                badges help prioritize review before pushing updates:
              </p>
              <ul className="grid grid-cols-1 gap-2 mt-2">
                <li className="flex items-center gap-2 text-[11px]"><div className="w-2 h-2 rounded-full bg-emerald-500"/> <strong>Optimal:</strong> Stock is &gt; 80% of your Desired Level.</li>
                <li className="flex items-center gap-2 text-[11px]"><div className="w-2 h-2 rounded-full bg-blue-500"/> <strong>Healthy:</strong> Stock is safe (&gt; 115% of your Reorder Point).</li>
                <li className="flex items-center gap-2 text-[11px]"><div className="w-2 h-2 rounded-full bg-amber-500"/> <strong>Warning:</strong> Order now (Stock is 100-115% of ROP).</li>
                <li className="flex items-center gap-2 text-[11px]"><div className="w-2 h-2 rounded-full bg-orange-500"/> <strong>Low Stock:</strong> Dipping into safety stock (50-100% of ROP).</li>
                <li className="flex items-center gap-2 text-[11px]"><div className="w-2 h-2 rounded-full bg-red-500"/> <strong>Critical:</strong> Stock-out imminent (&lt; 50% of ROP).</li>
              </ul>
            </CardContent>
          </Card>
        </div>

        {/* Section: Data Source */}
        <Card className="shadow-sm border-slate-200">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="w-5 h-5 text-slate-700" /> Source of Truth
            </CardTitle>
            <CardDescription>Where dashboard rows come from</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-3">
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Qualifying Products</h4>
              <p className="text-sm">Only products currently tagged <strong>auto-replen</strong> qualify for the dashboard.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Latest Snapshot</h4>
              <p className="text-sm">Core facts come from the latest local snapshot date in BigQuery's master snapshot view.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Locations</h4>
              <p className="text-sm">Rows are limited to Victoria, Bici Adanac, and Langford: shop IDs 2, 3, and 20.</p>
            </div>
          </CardContent>
        </Card>

        {/* Section: Key Metrics */}
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Info className="w-5 h-5 text-indigo-600" /> Understanding the Metrics
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-3">
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">30d / 60d</h4>
              <p className="text-sm">The main value is raw units sold. The smaller value underneath is stockout-adjusted demand.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">QOH / QOO</h4>
              <p className="text-sm">QOH is quantity on hand. QOO is open PO quantity remaining from the trusted snapshot view.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Cover</h4>
              <p className="text-sm">How many days current QOH will last based on the stockout-adjusted 60-day demand rate.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Lead</h4>
              <p className="text-sm">Vendor lead time by location, using recent receiving history where available.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">ROP</h4>
              <p className="text-sm">Recommended reorder point: adjusted daily demand times lead days, plus safety stock.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">DL</h4>
              <p className="text-sm">Recommended desired level: adjusted daily demand times the selected forecast period.</p>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-sm border-emerald-100 bg-emerald-50/10">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-emerald-700">
              <ShoppingCart className="w-5 h-5" /> Recommendation Math
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-3">
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Adjusted Demand</h4>
              <p className="text-sm">Raw sales are divided by in-stock days, then scaled back to 30 or 60 days.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Safety Days</h4>
              <p className="text-sm">Safety stock equals adjusted daily demand times the selected safety-day buffer.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Order Qty</h4>
              <p className="text-sm">Suggested order is desired level minus QOH and QOO, floored at zero.</p>
            </div>
          </CardContent>
        </Card>

        {/* Section: Overrides and Writing Back */}
        <div className="space-y-4">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Settings2 className="w-5 h-5 text-muted-foreground" /> Configuration & Pushing
          </h2>
          <div className="grid gap-6 md:grid-cols-3">
            <div className="bg-card border rounded-lg p-4 space-y-2">
              <h3 className="font-bold text-sm">Fine-Tuning</h3>
              <p className="text-xs text-muted-foreground">
                You can manually edit the <strong>Reorder Point</strong> and <strong>Desired Level</strong> directly in the table
                before pushing.
              </p>
            </div>
            <div className="bg-card border rounded-lg p-4 space-y-2">
              <h3 className="font-bold text-sm">Lightspeed Deep Links</h3>
              <p className="text-xs text-muted-foreground">
                Click any <strong>Item Description</strong> to open that product directly in Lightspeed R-Series for deeper inspection.
              </p>
            </div>
            <div className="bg-card border rounded-lg p-4 space-y-2 border-emerald-200 bg-emerald-50/10">
              <h3 className="font-bold text-sm text-emerald-700">Push to Lightspeed</h3>
              <p className="text-xs text-muted-foreground">
                Select your SKUs using the checkboxes and hit <strong>"Push to Lightspeed"</strong>. This updates the ROP/Desired Level
                values in the Lightspeed database immediately.
              </p>
            </div>
          </div>
        </div>

        {/* Section: Auditing */}
        <Card className="shadow-md border-indigo-100">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-indigo-700">
              <History className="w-5 h-5" /> Audit & History
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm">
              Transparency is key. The tool keeps a permanent log of every action taken:
            </p>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="flex items-start gap-3">
                <div className="mt-1 bg-indigo-100 p-1.5 rounded-md"><History className="w-4 h-4 text-indigo-700" /></div>
                <div>
                  <h5 className="text-sm font-bold">Recommendation Runs</h5>
                  <p className="text-xs text-muted-foreground">Logs every time data is synced, showing how many items were analyzed.</p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <div className="mt-1 bg-emerald-100 p-1.5 rounded-md"><ShieldCheck className="w-4 h-4 text-emerald-700" /></div>
                <div>
                  <h5 className="text-sm font-bold">Writeback Audit</h5>
                  <p className="text-xs text-muted-foreground">A row-by-row trail of every SKU updated in Lightspeed, including old vs. new values.</p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Footer Note */}
        <div className="text-center pt-8 border-t">
          <p className="text-muted-foreground text-sm flex items-center justify-center gap-2">
            Need more help? Confirm the product has the <Package className="w-4 h-4 inline" /> <strong>auto-replen</strong> tag in Lightspeed.
          </p>
        </div>
      </div>
    </AppShell>
  )
}
