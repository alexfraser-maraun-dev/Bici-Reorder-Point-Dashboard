'use client'

import { useMemo } from 'react'
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import {
  ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip,
  ChartTooltipContent, type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import { useItemPriceHistory } from '@/lib/price-intel/hooks'

// Our line is emphasized; competitors cycle the dashboard chart palette.
const OUR_COLOR = 'var(--foreground)'
const COMPETITOR_PALETTE = [
  'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)',
  'var(--chart-4)', 'var(--chart-5)',
]

const OUR_KEY = 'ours'
const WINDOW_DAYS = 60
const DAY_MS = 24 * 60 * 60 * 1000

// Expands the compressed per-series change-points into a daily grid over the
// trailing 2-month window: each series' price is carried forward day by day
// (the backend supplies a baseline point before the window so lines start at
// the left edge, not at their first in-window change). Daily points + monotone
// interpolation give the smoothed look; a change-point still reads as a clear
// level shift.
export function PriceHistoryChart({ itemId }: { itemId: string }) {
  const { history, isLoading } = useItemPriceHistory(itemId, WINDOW_DAYS)

  const { data, config, series } = useMemo(() => {
    const cfg: ChartConfig = {}
    const seriesList: { key: string; color: string; points: { ts: number; price: number }[] }[] = []

    const add = (key: string, label: string, color: string,
                 points: { observed_at: string; price: number | null }[]) => {
      const pts = points
        .filter((p) => p.price != null)
        .map((p) => ({ ts: new Date(p.observed_at).getTime(), price: p.price as number }))
        .sort((a, b) => a.ts - b.ts)
      if (!pts.length) return
      cfg[key] = { label, color }
      seriesList.push({ key, color, points: pts })
    }

    if (history) {
      add(OUR_KEY, 'Our price', OUR_COLOR, history.ours)
      history.competitors.forEach((c, i) => {
        add(`c${i}`, c.competitor_name, COMPETITOR_PALETTE[i % COMPETITOR_PALETTE.length], c.points)
      })
    }

    // Daily grid: local midnight for each day in the window, ending today.
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const rows: Record<string, number | null>[] = []
    for (let d = WINDOW_DAYS; d >= 0; d--) {
      rows.push({ ts: today.getTime() - d * DAY_MS })
    }
    for (const s of seriesList) {
      let i = 0
      let last: number | null = null
      for (const row of rows) {
        const dayEnd = (row.ts as number) + DAY_MS - 1
        while (i < s.points.length && s.points[i].ts <= dayEnd) {
          last = s.points[i].price
          i++
        }
        row[s.key] = last
      }
    }
    return { data: rows, config: cfg, series: seriesList }
  }, [history])

  if (isLoading) return <Skeleton className="h-52 rounded-md" />
  if (series.length === 0) {
    return (
      <p className="py-3 text-xs text-muted-foreground">
        No price history yet — the trend appears once scrapes record price changes.
      </p>
    )
  }

  const fmtDate = (ts: number) =>
    new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

  return (
    <ChartContainer config={config} className="aspect-auto h-52 w-full">
      <LineChart data={data} margin={{ left: 4, right: 12, top: 8, bottom: 2 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={fmtDate}
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          minTickGap={32}
        />
        <YAxis
          tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
          tickLine={false}
          axisLine={false}
          width={48}
          domain={['auto', 'auto']}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(_, payload) =>
                payload?.[0] != null ? fmtDate(payload[0].payload.ts) : ''
              }
              formatter={(value, name, item) => (
                <span className="flex w-full items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="h-2 w-2 shrink-0 rounded-[2px]"
                          style={{ backgroundColor: item?.color }} />
                    {config[name as string]?.label ?? name}
                  </span>
                  <span className="font-mono font-medium tabular-nums">
                    ${Number(value).toFixed(2)}
                  </span>
                </span>
              )}
            />
          }
        />
        <ChartLegend content={<ChartLegendContent />} />
        {series.map(({ key, color }) => (
          <Line
            key={key}
            dataKey={key}
            name={key}
            type="monotone"
            stroke={color}
            strokeWidth={key === OUR_KEY ? 2.5 : 1.5}
            dot={false}
            activeDot={{ r: 3 }}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  )
}
