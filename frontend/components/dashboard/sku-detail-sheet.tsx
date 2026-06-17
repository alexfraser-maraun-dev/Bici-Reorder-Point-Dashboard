'use client'

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { useDemandHistory } from '@/lib/hooks'
import { DemandForecastChart } from '@/components/charts/demand-forecast-chart'
import { VelocitySparkline } from '@/components/charts/velocity-sparkline'
import type { DemandHistoryPoint, ForecastPoint } from '@/lib/types'
import { TrendingUp, Activity } from 'lucide-react'

// Store display name (as it appears on replenishment rows) -> Lightspeed shop id.
const LOCATION_TO_SHOP_ID: Record<string, number> = {
  'Bici Adanac': 3,
  Adanac: 3,
  Langford: 20,
  Victoria: 2,
}

interface SkuDetailSheetProps {
  item: any | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkuDetailSheet({ item, open, onOpenChange }: SkuDetailSheetProps) {
  const itemId = item?.lightspeed_item_id ?? item?.system_id ?? null
  const shopId = item?.location ? LOCATION_TO_SHOP_ID[item.location] ?? null : null
  const { data, isLoading } = useDemandHistory('sku', open ? (itemId ? String(itemId) : null) : null, shopId)

  const payload = data?.data
  const history: DemandHistoryPoint[] = payload?.history ?? []
  const forecast: ForecastPoint[] = payload?.forecast ?? []
  const leadTimeWindow = payload?.lead_time_window ?? null
  const referenceMonth = data?.meta?.reference_month ?? new Date().getMonth() + 1

  // Three demand windows, oldest -> newest, for the momentum sparkline.
  const velocityValues = item
    ? [
        Number(item.adjusted_daily_sales_31_60d ?? 0),
        Number(item.adjusted_daily_sales_15_30d ?? 0),
        Number(item.adjusted_daily_sales_14d ?? 0),
      ]
    : []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[520px] sm:max-w-[520px]">
        <SheetHeader className="pb-4">
          <SheetTitle className="flex items-center gap-2">
            <span className="font-mono text-sm">{item?.sku}</span>
            {item?.momentum_label && (
              <Badge variant="secondary" className="text-[10px]">{item.momentum_label}</Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            {item?.description}
            {item?.location ? ` · ${item.location}` : ''}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="h-[calc(100vh-160px)] pr-4">
          <div className="space-y-6">
            <section>
              <div className="mb-2 flex items-center gap-2">
                <TrendingUp className="text-muted-foreground h-4 w-4" />
                <h4 className="text-sm font-medium">Demand history &amp; forecast</h4>
              </div>
              <p className="text-muted-foreground mb-3 text-xs">
                Monthly units sold (bars) with the seasonally-adjusted forward forecast (dashed).
                The shaded band marks the months a PO placed now would cover.
              </p>
              <DemandForecastChart
                history={history}
                forecast={forecast}
                leadTimeWindow={leadTimeWindow}
                referenceMonth={referenceMonth}
                isLoading={isLoading}
              />

              {/* Paired table: the forecast numbers, fully inspectable. */}
              {forecast.length > 0 && (
                <div className="mt-3 overflow-auto rounded-lg border">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="px-2 py-1.5 text-left font-medium">Forward month</th>
                        <th className="px-2 py-1.5 text-right font-medium">Forecast units</th>
                        <th className="px-2 py-1.5 text-right font-medium">Seasonal index</th>
                      </tr>
                    </thead>
                    <tbody>
                      {forecast.map((point, index) => (
                        <tr key={index} className="border-t">
                          <td className="px-2 py-1 tabular-nums">
                            {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][point.month - 1]}
                          </td>
                          <td className="px-2 py-1 text-right tabular-nums">{point.units.toLocaleString()}</td>
                          <td className="px-2 py-1 text-right tabular-nums text-muted-foreground">
                            {point.seasonal_index.toFixed(2)}×
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section>
              <div className="mb-2 flex items-center gap-2">
                <Activity className="text-muted-foreground h-4 w-4" />
                <h4 className="text-sm font-medium">Recent velocity trend</h4>
              </div>
              <p className="text-muted-foreground mb-2 text-xs">
                Adjusted daily sales across the trailing windows — the shape behind the
                <span className="font-medium"> {item?.momentum_label || 'momentum'}</span> label.
              </p>
              <VelocitySparkline values={velocityValues} momentum={item?.momentum_label} />
              <div className="text-muted-foreground mt-1 flex justify-between text-[10px]">
                <span>31–60d: {velocityValues[0]?.toFixed(2)}/d</span>
                <span>15–30d: {velocityValues[1]?.toFixed(2)}/d</span>
                <span>14d: {velocityValues[2]?.toFixed(2)}/d</span>
              </div>
            </section>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
