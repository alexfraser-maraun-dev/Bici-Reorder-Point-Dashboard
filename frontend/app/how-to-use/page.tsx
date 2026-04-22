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
  ExternalLink,
  Info
} from 'lucide-react'

export default function HowToUsePage() {
  return (
    <AppShell>
      <div className="max-w-4xl mx-auto space-y-8 py-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">How the Config Tool Works</h1>
          <p className="text-muted-foreground text-lg">
            A comprehensive guide to the proactive logic, metrics, and workflows powering your replenishment.
          </p>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          {/* Step 1: Syncing */}
          <Card className="shadow-sm border-blue-100 bg-blue-50/10">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-blue-700">
                <RefreshCw className="w-5 h-5" /> 1. Sync Product Data
              </CardTitle>
              <CardDescription>Refreshing your data from Google Sheets</CardDescription>
            </CardHeader>
            <CardContent className="text-sm space-y-3">
              <p>
                Click the <strong>"Sync Product Data"</strong> button in the sidebar to fetch the latest sales velocity, 
                current stock levels, and vendor lead times.
              </p>
              <p className="text-muted-foreground italic">
                Tip: Do this every time you start a session to ensure your recommendations are based on the latest snapshots.
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
                The tool uses a <strong>proactive buffer</strong> to warn you before you hit your Reorder Point. 
                Pay attention to the status icons:
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

        {/* Section: Key Metrics */}
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Info className="w-5 h-5 text-indigo-600" /> Understanding the Metrics
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-3">
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Cover (Days)</h4>
              <p className="text-sm">How many days your current stock (QOH) will last based on recent sales momentum.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">30d / 60d Forecast</h4>
              <p className="text-sm">The projected number of units you will sell in the next 30 or 60 days.</p>
            </div>
            <div className="space-y-1">
              <h4 className="font-bold text-xs uppercase text-muted-foreground">Growth Multiplier</h4>
              <p className="text-sm">Adjust this to account for seasonality (e.g., set to 1.5 for 50% predicted growth).</p>
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
            Need more help? Check the <Package className="w-4 h-4 inline" /> <strong>Managed SKUs</strong> tab to ensure all your priority products are being tracked.
          </p>
        </div>
      </div>
    </AppShell>
  )
}
