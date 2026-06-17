'use client'

import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
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
import type { SeasonalProfile } from '@/lib/types'

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

// Up to five overlaid series, themed via the shared chart palette.
const SERIES_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
]

// A stable, CSS-var-safe key for a category label (config keys can't be arbitrary).
function seriesKey(label: string) {
  return `s_${label.replace(/[^a-zA-Z0-9]/g, '_')}`
}

interface SeasonalProfileChartProps {
  profiles: SeasonalProfile[]
}

export function SeasonalProfileChart({ profiles }: SeasonalProfileChartProps) {
  const { chartData, config } = useMemo(() => {
    const config: ChartConfig = {}
    profiles.forEach((profile, index) => {
      config[seriesKey(profile.category_label)] = {
        label: profile.category_label,
        color: SERIES_COLORS[index % SERIES_COLORS.length],
      }
    })

    const chartData = MONTH_LABELS.map((label, monthIndex) => {
      const monthNumber = monthIndex + 1
      const row: Record<string, number | string> = { month: label }
      for (const profile of profiles) {
        const value = profile.indices[String(monthNumber)] ?? profile.indices[monthNumber]
        row[seriesKey(profile.category_label)] = value ?? 1
      }
      return row
    })

    return { chartData, config }
  }, [profiles])

  if (profiles.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center text-sm text-muted-foreground">
        Select one or more categories to compare their seasonal shapes.
      </div>
    )
  }

  return (
    <ChartContainer config={config} className="h-[280px] w-full">
      <LineChart data={chartData} margin={{ left: 4, right: 12, top: 8, bottom: 4 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="month" tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={36}
          domain={[0, 'auto']}
          tickFormatter={(value: number) => `${value.toFixed(1)}×`}
        />
        {/* The average month sits at 1.0; above the line = peak, below = trough. */}
        <ReferenceLine
          y={1}
          stroke="var(--muted-foreground)"
          strokeDasharray="4 4"
          label={{ value: 'avg', position: 'insideTopRight', fontSize: 10, fill: 'var(--muted-foreground)' }}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              formatter={(value, name) => (
                <div className="flex w-full items-center justify-between gap-3">
                  <span className="text-muted-foreground">{config[name as string]?.label ?? name}</span>
                  <span className="font-mono font-medium tabular-nums">
                    {Number(value).toFixed(2)}×
                  </span>
                </div>
              )}
            />
          }
        />
        <ChartLegend content={<ChartLegendContent />} />
        {profiles.map((profile) => {
          const key = seriesKey(profile.category_label)
          return (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={`var(--color-${key})`}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          )
        })}
      </LineChart>
    </ChartContainer>
  )
}
