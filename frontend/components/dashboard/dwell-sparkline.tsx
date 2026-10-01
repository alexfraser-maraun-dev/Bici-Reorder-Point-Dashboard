'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { DwellSeries } from '@/lib/special-order-triage'

// Fewer points than this is a stage that only just started being tracked; a two-point "trend"
// would be noise dressed up as a signal.
const MIN_POINTS = 7
// Comparison windows at each end, and how far apart they must be to count as a direction.
const EDGE_POINTS = 7
const DIRECTION_THRESHOLD = 0.2
// Below this, the y-scale stops shrinking — otherwise a stage wobbling between 1 and 2 days
// would draw as dramatically as one climbing from 10 to 40.
const MIN_Y_DAYS = 7

const VIEW_W = 100
const VIEW_H = 32
const PAD = 3

type Direction = 'worse' | 'better' | 'flat'

const DIRECTION_STYLE: Record<Direction, { stroke: string; dot: string; word: string }> = {
  worse: { stroke: 'stroke-red-500', dot: 'bg-red-500', word: 'rising' },
  better: { stroke: 'stroke-emerald-500', dot: 'bg-emerald-500', word: 'falling' },
  flat: { stroke: 'stroke-muted-foreground', dot: 'bg-muted-foreground', word: 'steady' },
}

function mean(values: number[]): number {
  return values.reduce((sum, v) => sum + v, 0) / values.length
}

function direction(points: number[]): Direction {
  const head = mean(points.slice(0, EDGE_POINTS))
  const tail = mean(points.slice(-EDGE_POINTS))
  const base = Math.max(head, 1)
  if ((tail - head) / base > DIRECTION_THRESHOLD) return 'worse'
  if ((head - tail) / base > DIRECTION_THRESHOLD) return 'better'
  return 'flat'
}

/** Catmull-Rom through the points, emitted as cubic Béziers. Runs of nulls split the path so a
 *  day with nothing in the stage is a gap, not an invented line. */
function smoothPath(xy: ([number, number] | null)[]): string {
  const runs: [number, number][][] = [[]]
  for (const p of xy) {
    if (p) runs[runs.length - 1].push(p)
    else if (runs[runs.length - 1].length) runs.push([])
  }
  return runs.filter((run) => run.length > 1).map((run) => {
    let d = `M${run[0][0].toFixed(2)},${run[0][1].toFixed(2)}`
    for (let i = 0; i < run.length - 1; i += 1) {
      const p0 = run[i - 1] ?? run[i]
      const p1 = run[i]
      const p2 = run[i + 1]
      const p3 = run[i + 2] ?? p2
      const c1x = p1[0] + (p2[0] - p0[0]) / 6
      const c1y = p1[1] + (p2[1] - p0[1]) / 6
      const c2x = p2[0] - (p3[0] - p1[0]) / 6
      const c2y = p2[1] - (p3[1] - p1[1]) / 6
      d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`
    }
    return d
  }).join(' ')
}

function formatDays(value: number): string {
  return `${Math.round(value)}d`
}

function dayLabel(endIso: string, offset: number): string {
  const date = new Date(`${endIso.slice(0, 10)}T12:00:00`)
  date.setDate(date.getDate() + offset)
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

/** Median days-in-stage over the trend window, drawn into a pipeline tile's spare width.
 *
 * The line's slope carries the direction for everyone; its colour (red rising, green falling)
 * is a second channel, never the only one. The hover readout and the wrapper's `title` give the
 * figures. Deliberately no SVG `<title>`: its text becomes part of the tile's textContent,
 * which the pipeline count parsing (and its e2e test) reads. */
export function DwellSparkline({
  series,
  endDate,
  stageLabel,
  filterNote,
  className,
}: {
  series: DwellSeries | undefined
  endDate: string | undefined
  stageLabel: string
  filterNote: string
  className?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const points = series?.values.filter((v): v is number => v != null) ?? []
  if (!series || !endDate || points.length < MIN_POINTS) return <span className={className} aria-hidden="true" />

  const { values, startOffset } = series
  const yMax = Math.max(MIN_Y_DAYS, ...points)
  const xAt = (i: number) => PAD + (i / Math.max(values.length - 1, 1)) * (VIEW_W - PAD * 2)
  const yAt = (v: number) => VIEW_H - PAD - (v / yMax) * (VIEW_H - PAD * 2)
  const xy = values.map((v, i) => (v == null ? null : [xAt(i), yAt(v)] as [number, number]))

  const dir = direction(points)
  const style = DIRECTION_STYLE[dir]
  const firstIndex = values.findIndex((v) => v != null)
  let lastIndex = values.length - 1
  while (values[lastIndex] == null) lastIndex -= 1
  const now = values[lastIndex] as number
  const then = values[firstIndex] as number
  const thenLabel = dayLabel(endDate, startOffset + firstIndex)
  const summary = `${stageLabel}: median ${formatDays(now)} in stage now, ${formatDays(then)} on ${thenLabel} (${style.word}).`
  const active = hover != null && values[hover] != null ? hover : null

  return (
    <div
      className={cn('relative self-stretch', className)}
      title={`${summary} ${filterNote}`}
      onMouseMove={(event) => {
        const box = event.currentTarget.getBoundingClientRect()
        const ratio = (event.clientX - box.left) / Math.max(box.width, 1)
        setHover(Math.round(Math.min(1, Math.max(0, ratio)) * (values.length - 1)))
      }}
      onMouseLeave={() => setHover(null)}
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full overflow-visible"
        role="img"
        aria-label={summary}
      >
        <path
          d={smoothPath(xy)}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          className={cn(style.stroke, 'opacity-70')}
        />
        {active != null && (
          <line
            x1={xAt(active)} x2={xAt(active)} y1={0} y2={VIEW_H}
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
            className="stroke-border"
          />
        )}
      </svg>
      {/* Dots live outside the stretched SVG so they stay round at any tile width. */}
      {[active ?? lastIndex].map((i) => (
        <span
          key="dot"
          className={cn(
            'pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card',
            style.dot,
          )}
          style={{ left: `${xAt(i)}%`, top: `${(yAt(values[i] as number) / VIEW_H) * 100}%` }}
          aria-hidden="true"
        />
      ))}
      {active != null && (
        <span
          className="pointer-events-none absolute -top-4 right-0 whitespace-nowrap rounded bg-popover px-1.5 text-[10px] font-medium tabular-nums text-popover-foreground shadow-sm"
          aria-hidden="true"
        >
          {formatDays(values[active] as number)} · {dayLabel(endDate, startOffset + active)}
        </span>
      )}
    </div>
  )
}
