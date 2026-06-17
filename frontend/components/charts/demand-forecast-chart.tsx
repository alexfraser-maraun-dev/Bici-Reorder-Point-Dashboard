'use client'

import { useMemo } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import type { DemandHistoryPoint, ForecastPoint } from '@/lib/types'

const MONTH = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

const chartConfig: ChartConfig = {
  history: { label: 'Actual', color: 'var(--chart-2)' },
  forecast: { label: 'Forecast', color: 'var(--chart-1)' },
}

function yy(year: number) {
  return `'${String(year).slice(2)}`
}

// Months covered by [start..end], inclusive, wrapping around December.
function windowMonths(start: number, end: number): Set<number> {
  const set = new Set<number>()
  let m = start
  for (let i = 0; i < 12; i++) {
    set.add(m)
    if (m === end) break
    m = m === 12 ? 1 : m + 1
  }
  return set
}

interface DemandForecastChartProps {
  history: DemandHistoryPoint[]
  forecast: ForecastPoint[]
  leadTimeWindow?: { start_month: number; end_month: number } | null
  referenceMonth: number
  isLoading?: boolean
  className?: string
}

export function DemandForecastChart({
  history,
  forecast,
  leadTimeWindow,
  referenceMonth,
  isLoading,
  className,
}: DemandForecastChartProps) {
  const { rows, windowBounds } = useMemo(() => {
    const rows: Array<Record<string, number | string>> = []
    for (const h of history) {
      rows.push({ label: `${MONTH[h.month - 1]} ${yy(h.year ?? new Date().getFullYear())}`, history: h.units })
    }

    // Assign calendar years to the forward forecast months (they wrap 12 -> 1).
    const currentYear = new Date().getFullYear()
    let year = currentYear
    let prev = referenceMonth
    const forecastLabels: Array<{ month: number; label: string }> = []
    for (const f of forecast) {
      if (f.month <= prev) year += 1
      prev = f.month
      const label = `${MONTH[f.month - 1]} ${yy(year)}`
      rows.push({ label, forecast: f.units })
      forecastLabels.push({ month: f.month, label })
    }

    let windowBounds: { x1: string; x2: string } | null = null
    if (leadTimeWindow) {
      const months = windowMonths(leadTimeWindow.start_month, leadTimeWindow.end_month)
      const inWindow = forecastLabels.filter((f) => months.has(f.month))
      if (inWindow.length) {
        windowBounds = { x1: inWindow[0].label, x2: inWindow[inWindow.length - 1].label }
      }
    }

    return { rows, windowBounds }
  }, [history, forecast, leadTimeWindow, referenceMonth])

  if (isLoading) {
    return <Skeleton className={className ?? 'h-[260px] w-full'} />
  }

  if (rows.length === 0) {
    return (
      <div className="text-muted-foreground flex h-[260px] items-center justify-center text-sm">
        No demand history available.
      </div>
    )
  }

  return (
    <ChartContainer config={chartConfig} className={className ?? 'h-[260px] w-full'}>
      <ComposedChart data={rows} margin={{ left: 4, right: 12, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} fontSize={10} />
        <YAxis tickLine={false} axisLine={false} tickMargin={8} width={36} />
        {windowBounds && (
          <ReferenceArea
            x1={windowBounds.x1}
            x2={windowBounds.x2}
            fill="var(--chart-1)"
            fillOpacity={0.1}
            label={{ value: 'PO covers', position: 'insideTop', fontSize: 9, fill: 'var(--muted-foreground)' }}
          />
        )}
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="history" fill="var(--color-history)" radius={[2, 2, 0, 0]} maxBarSize={28} />
        <Line
          dataKey="forecast"
          type="monotone"
          stroke="var(--color-forecast)"
          strokeWidth={2}
          strokeDasharray="5 4"
          dot={{ r: 2 }}
          connectNulls
        />
      </ComposedChart>
    </ChartContainer>
  )
}
