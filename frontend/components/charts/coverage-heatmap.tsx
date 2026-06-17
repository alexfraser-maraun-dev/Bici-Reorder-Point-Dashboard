'use client'

import { useMemo } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import type { CoverageRow } from '@/lib/types'

const MONTH = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

// Risk -> cell styling. Heatmaps aren't Recharts' strength, so this is a plain
// CSS grid using the dashboard's red -> amber -> emerald status scale.
function cellClass(risk: string): string {
  switch (risk) {
    case 'critical':
      return 'bg-red-500/85 text-white'
    case 'low':
      return 'bg-amber-400/70 text-amber-950'
    default:
      return 'bg-emerald-500/15 text-emerald-700'
  }
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

interface CoverageHeatmapProps {
  rows: CoverageRow[]
  referenceMonth: number
  isLoading?: boolean
}

export function CoverageHeatmap({ rows, referenceMonth, isLoading }: CoverageHeatmapProps) {
  // Forward month labels starting the month after the reference month.
  const monthLabels = useMemo(() => {
    const horizon = rows[0]?.weeks_of_cover.length ?? 12
    return Array.from({ length: horizon }, (_, step) => {
      const moy = ((referenceMonth - 1 + step + 1) % 12) + 1
      return MONTH[moy - 1]
    })
  }, [rows, referenceMonth])

  if (isLoading) {
    return <Skeleton className="h-[360px] w-full" />
  }

  if (rows.length === 0) {
    return (
      <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
        No coverage data available.
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="overflow-auto rounded-lg border">
        <table className="w-full border-collapse text-[10px]">
          <thead className="bg-muted/50 sticky top-0 z-10">
            <tr>
              <th className="min-w-[200px] px-2 py-1.5 text-left font-medium">SKU</th>
              {monthLabels.map((label, i) => (
                <th key={`${label}-${i}`} className="px-1 py-1.5 text-center font-medium">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.sku}-${row.location}`} className="border-t">
                <td className="max-w-[220px] px-2 py-1">
                  {row.lightspeed_item_id ? (
                    <a
                      href={`${API_BASE}/api/replenishment/ls-link/${row.lightspeed_item_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate font-medium hover:text-blue-600 hover:underline"
                      title={`Open ${row.product} in Lightspeed`}
                    >
                      {row.product}
                    </a>
                  ) : (
                    <div className="truncate font-medium" title={row.product}>{row.product}</div>
                  )}
                  <div className="text-muted-foreground flex items-center gap-1.5 font-mono text-[9px]">
                    <span className="bg-muted rounded px-1">{row.sku}</span>
                    <span>{row.location}</span>
                  </div>
                </td>
                {row.weeks_of_cover.map((month, i) => (
                  <td key={i} className="px-0.5 py-0.5">
                    <div
                      className={`flex h-7 items-center justify-center rounded-sm tabular-nums ${cellClass(month.stockout_risk)}`}
                      title={`${MONTH[month.month - 1]}: ${month.weeks >= 52 ? '52+' : month.weeks} weeks of cover (${month.stockout_risk})`}
                    >
                      {month.weeks >= 52 ? '52+' : month.weeks}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="text-muted-foreground flex items-center gap-4 text-[10px]">
        <span className="font-medium">Weeks of cover:</span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded-sm bg-red-500/85" /> Critical (&lt;2 wks)
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded-sm bg-amber-400/70" /> Low (&lt;4 wks)
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-3 rounded-sm bg-emerald-500/30" /> Healthy
        </span>
      </div>
    </div>
  )
}
