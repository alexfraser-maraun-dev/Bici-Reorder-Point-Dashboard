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

// Turns the compressed per-series change-points into one time-indexed dataset.
// Each series contributes a value only at its own change timestamps; the step
// line (type="stepAfter") + connectNulls carries each price forward until the
// next change, and dots mark the actual change-points.
export function PriceHistoryChart({ itemId }: { itemId: string }) {
  const { history, isLoading } = useItemPriceHistory(itemId)

  const { data, config, series, span } = useMemo(() => {
    const cfg: ChartConfig = {}
    const seriesKeys: { key: string; color: string }[] = []
    const byTs = new Map<number, Record<string, number | null>>()

    const add = (key: string, label: string, color: string,
                 points: { observed_at: string; price: number | null }[]) => {
      if (!points.some((p) => p.price != null)) return
      cfg[key] = { label, color }
      seriesKeys.push({ key, color })
      for (const p of points) {
        if (p.price == null) continue
        const ts = new Date(p.observed_at).getTime()
        const row = byTs.get(ts) ?? { ts }
        row[key] = p.price
        byTs.set(ts, row)
      }
    }

    if (history) {
      add(OUR_KEY, 'Our price', OUR_COLOR, history.ours)
      history.competitors.forEach((c, i) => {
        add(`c${i}`, c.competitor_name, COMPETITOR_PALETTE[i % COMPETITOR_PALETTE.length], c.points)
      })
    }

    const rows = [...byTs.values()].sort((a, b) => (a.ts as number) - (b.ts as number))
    // Carry each series' last known value forward to the right edge, so a price
    // that hasn't changed (a single change-point) still draws as a flat line
    // instead of a lone dot.
    if (rows.length) {
      const lastRow = rows[rows.length - 1]
      for (const { key } of seriesKeys) {
        let lastVal: number | null = null
        for (const r of rows) if (r[key] != null) lastVal = r[key]
        if (lastVal != null && lastRow[key] == null) lastRow[key] = lastVal
      }
    }
    const span = rows.length ? (rows[rows.length - 1].ts as number) - (rows[0].ts as number) : 0
    return { data: rows, config: cfg, series: seriesKeys, span }
  }, [history])

  if (isLoading) return <Skeleton className="h-52 rounded-md" />
  if (data.length === 0) {
    return (
      <p className="py-3 text-xs text-muted-foreground">
        No price history yet — the trend appears once scrapes record price changes.
      </p>
    )
  }

  // Within ~2 days, points cluster on one date — show the time so intraday
  // snapshots are distinguishable; over longer ranges show the date.
  const intraday = span < 2 * 24 * 60 * 60 * 1000
  const fmtDate = (ts: number) =>
    new Date(ts).toLocaleString(undefined, intraday
      ? { hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric' })
  const fmtFull = (ts: number) =>
    new Date(ts).toLocaleString(undefined,
      { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

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
                payload?.[0] != null ? fmtFull(payload[0].payload.ts) : ''
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
            type="stepAfter"
            stroke={color}
            strokeWidth={key === OUR_KEY ? 2.5 : 1.5}
            dot={{ r: 2, fill: color }}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  )
}
