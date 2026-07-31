'use client'

import { useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import {
  ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useItemPriceHistory } from '@/lib/price-intel/hooks'

// Our line is emphasized; competitors cycle the dashboard chart palette. Once
// the palette is exhausted the same colours come back with a different dash
// pattern, so two stores are never drawn identically.
const OUR_COLOR = 'var(--foreground)'
const COMPETITOR_PALETTE = [
  'var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)',
  'var(--chart-4)', 'var(--chart-5)',
]
const DASH_CYCLE: (string | undefined)[] = [undefined, '6 3', '2 3', '9 3 2 3']

const OUR_KEY = 'ours'
const WINDOW_DAYS = 60
const DAY_MS = 24 * 60 * 60 * 1000

type Series = {
  key: string
  label: string
  color: string
  dash?: string
  latest: number | null
}

// Expands the compressed per-series change-points into a daily grid over the
// trailing 2-month window: each series' price is carried forward day by day
// (the backend supplies a baseline point before the window so lines start at
// the left edge, not at their first in-window change). Daily points + monotone
// interpolation give the smoothed look; a change-point still reads as a clear
// level shift.
export function PriceHistoryChart({ itemId }: { itemId: string }) {
  const { history, isLoading } = useItemPriceHistory(itemId, WINDOW_DAYS)
  // Hovering (or clicking to pin) a legend entry isolates that line — the exact
  // answer to "which line is this store?" when two prices sit close together.
  const [hovered, setHovered] = useState<string | null>(null)
  const [pinned, setPinned] = useState<string | null>(null)
  const focused = hovered ?? pinned

  const { data, config, series } = useMemo(() => {
    const cfg: ChartConfig = {}
    const built: { key: string; color: string; dash?: string
                   points: { ts: number; price: number }[] }[] = []

    const add = (key: string, label: string, color: string, dash: string | undefined,
                 points: { observed_at: string; price: number | null }[]) => {
      const pts = points
        .filter((p) => p.price != null)
        .map((p) => ({ ts: new Date(p.observed_at).getTime(), price: p.price as number }))
        .sort((a, b) => a.ts - b.ts)
      if (!pts.length) return
      cfg[key] = { label, color }
      built.push({ key, color, dash, points: pts })
    }

    if (history) {
      add(OUR_KEY, 'Our price', OUR_COLOR, undefined, history.ours)
      history.competitors.forEach((c, i) => {
        add(`c${i}`, c.competitor_name,
            COMPETITOR_PALETTE[i % COMPETITOR_PALETTE.length],
            DASH_CYCLE[Math.floor(i / COMPETITOR_PALETTE.length) % DASH_CYCLE.length],
            c.points)
      })
    }

    // Daily grid: local midnight for each day in the window, ending today.
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const rows: Record<string, number | null>[] = []
    for (let d = WINDOW_DAYS; d >= 0; d--) {
      rows.push({ ts: today.getTime() - d * DAY_MS })
    }
    for (const s of built) {
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

    // The legend is ordered by each line's price *today*, i.e. top-to-bottom in
    // the same order the lines stack at the right edge of the chart. That
    // ordering is what makes a line identifiable without colour-matching.
    const last = rows[rows.length - 1] ?? {}
    const seriesList: Series[] = built
      .map((s) => ({
        key: s.key, label: String(cfg[s.key]?.label ?? s.key),
        color: s.color, dash: s.dash, latest: last[s.key] ?? null,
      }))
      .sort((a, b) => (b.latest ?? -Infinity) - (a.latest ?? -Infinity))

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
    <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch sm:gap-3">
      <ChartContainer config={config} className="aspect-auto h-52 min-w-0 flex-1">
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
            // Highest price first, matching the lines' vertical order and the
            // legend beside the chart.
            itemSorter={(item) => -(Number(item.value) || 0)}
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
          {series.map(({ key, color, dash }) => {
            const dimmed = focused != null && focused !== key
            return (
              <Line
                key={key}
                dataKey={key}
                name={key}
                type="monotone"
                stroke={color}
                strokeDasharray={dash}
                strokeWidth={focused === key ? 3.5 : key === OUR_KEY ? 2.5 : 1.5}
                strokeOpacity={dimmed ? 0.15 : 1}
                dot={false}
                activeDot={{ r: 3 }}
                connectNulls
                isAnimationActive={false}
              />
            )
          })}
        </LineChart>
      </ChartContainer>

      <div className="flex max-h-52 shrink-0 flex-col gap-0.5 overflow-y-auto pr-1
                      sm:w-56 sm:border-l sm:pl-3">
        {series.map((s) => {
          const dimmed = focused != null && focused !== s.key
          return (
            <button
              key={s.key}
              type="button"
              title={`${s.label} — click to keep this line highlighted`}
              onMouseEnter={() => setHovered(s.key)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => setPinned((p) => (p === s.key ? null : s.key))}
              className={cn(
                'flex items-center gap-2 rounded px-1.5 py-1 text-left text-[11px] leading-tight transition-opacity hover:bg-muted',
                dimmed && 'opacity-40',
                pinned === s.key && 'bg-muted'
              )}
            >
              {/* A line swatch, not a dot — it carries the dash pattern too. */}
              <svg width="18" height="8" viewBox="0 0 18 8" className="shrink-0">
                <line x1="0" y1="4" x2="18" y2="4" stroke={s.color}
                      strokeWidth={s.key === OUR_KEY ? 3 : 2}
                      strokeDasharray={s.dash} strokeLinecap="round" />
              </svg>
              <span className={cn('min-w-0 flex-1 truncate',
                s.key === OUR_KEY ? 'font-semibold' : 'text-muted-foreground')}>
                {s.label}
              </span>
              <span className="shrink-0 tabular-nums font-medium">
                {s.latest == null ? '—' : `$${s.latest.toFixed(2)}`}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
