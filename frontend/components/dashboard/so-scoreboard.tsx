'use client'

import { useState } from 'react'
import { useSWRConfig } from 'swr'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSoScoreboard } from '@/lib/hooks'
import type { DwellStat } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ChevronDown, ChevronRight, BarChart3, RefreshCw } from 'lucide-react'

const STAGE_LABEL: Record<string, string> = {
  open_pool: 'Awaiting PO',
  unordered_po: 'Draft PO',
  ordered: 'In transit',
  received: 'Arrived',
}

function DwellTable({ title, data, unit = 'days' }: {
  title: string; data: Record<string, DwellStat | null>; unit?: string
}) {
  const entries = Object.entries(data).filter(([, v]) => v) as [string, DwellStat][]
  if (!entries.length) return null
  entries.sort((a, b) => b[1].median - a[1].median)
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted-foreground">
            <th className="py-0.5 text-left font-normal"> </th>
            <th className="py-0.5 text-right font-normal">n</th>
            <th className="py-0.5 text-right font-normal">med</th>
            <th className="py-0.5 text-right font-normal">p75</th>
            <th className="py-0.5 text-right font-normal">max</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td className="py-0.5 pr-2">{STAGE_LABEL[k] ?? k}</td>
              <td className="py-0.5 text-right tabular-nums text-muted-foreground">{v.n}</td>
              <td className="py-0.5 text-right font-medium tabular-nums">{v.median}</td>
              <td className="py-0.5 text-right tabular-nums text-muted-foreground">{v.p75}</td>
              <td className="py-0.5 text-right tabular-nums text-muted-foreground">{v.max}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-0.5 text-xs text-muted-foreground">{unit}</p>
    </div>
  )
}

/** Where special-order time goes, and who owns each failure.
 *
 *  Current-state numbers are correct from the first sweep because stage entry timestamps are
 *  derived from Lightspeed's own dates rather than observed. */
export function SoScoreboard() {
  const [open, setOpen] = useState(false)
  const { mutate } = useSWRConfig()
  const { scoreboard, isLoading, error } = useSoScoreboard(open)
  const p = scoreboard?.promise
  const contentId = 'special-orders-scoreboard-content'

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={contentId}
        className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium hover:bg-muted/50"
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <BarChart3 className="h-4 w-4 text-muted-foreground" />
        Scoreboard
        <span className="text-xs font-normal text-muted-foreground">
          — where the time goes, and who owns it
        </span>
      </button>

      {open && (
        <div id={contentId} className="space-y-4 border-t px-3 py-3">
          {isLoading && (
            <div aria-live="polite" aria-busy="true">
              <span className="sr-only">Loading special-order insights</span>
              <Skeleton className="h-40 w-full" />
            </div>
          )}

          {error && !isLoading && (
            <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              <span className="min-w-0 flex-1">Insights could not be loaded.</span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-9 gap-1.5 bg-background"
                onClick={() => void mutate('/backend/api/special-orders/scoreboard')}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </Button>
            </div>
          )}

          {!isLoading && !error && !scoreboard && (
            <p className="rounded-md border bg-muted/30 px-3 py-3 text-sm text-muted-foreground">
              No insight data is available yet.
            </p>
          )}

          {scoreboard && (
            <>
              <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    On time vs original promise
                  </p>
                  <p className={cn('text-2xl font-semibold tabular-nums',
                    (p?.on_time_pct_vs_original ?? 100) >= 90 ? 'text-emerald-600'
                      : (p?.on_time_pct_vs_original ?? 100) >= 70 ? 'text-amber-600' : 'text-red-600')}>
                    {p?.on_time_pct_vs_original ?? '—'}
                    {p?.on_time_pct_vs_original != null && <span className="text-base">%</span>}
                  </p>
                  {/* The denominator matters: only delivered orders can be scored. Saying so
                      stops the number being read as "97% of everything is fine". */}
                  <p className="text-xs text-muted-foreground">
                    {p?.met ?? 0} met of {p?.settled ?? 0} delivered
                  </p>
                </div>
                <div className="space-y-0.5 text-sm">
                  {!!p?.breached_outstanding && (
                    <p className="text-red-600">{p.breached_outstanding} past promise, not yet here</p>
                  )}
                  <p className="text-muted-foreground">{p?.undetermined ?? 0} still inside their window</p>
                  {!!p?.received_date_unknown && (
                    <p className="text-muted-foreground">
                      {p.received_date_unknown} received with no individual check-in date; excluded from the rate
                    </p>
                  )}
                  {!!p?.revised_at_least_once && (
                    <p className="text-amber-700">{p.revised_at_least_once} had the date revised</p>
                  )}
                  <p className="text-muted-foreground">
                    {p?.missing_promise ?? 0} never got a promised date
                    {p?.missing_promise_by_owner && (
                      <span className="opacity-70">
                        {' '}({p.missing_promise_by_owner.service} service · {p.missing_promise_by_owner.cs} CS)
                      </span>
                    )}
                  </p>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <DwellTable title="Time in current stage" data={scoreboard.dwell_days.by_stage} />
                <DwellTable title="Age by store" data={scoreboard.dwell_days.by_store} />
                <DwellTable title="Age by source" data={scoreboard.dwell_days.by_source} />
              </div>

              {scoreboard.queue.top_blocking_reasons.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Why parked orders are waiting
                  </p>
                  <div className="flex flex-wrap gap-x-4 text-sm">
                    {scoreboard.queue.top_blocking_reasons.map(([reason, n]) => (
                      <span key={reason}>
                        {reason.replace(/_/g, ' ')} <span className="tabular-nums text-muted-foreground">{n}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {scoreboard.history && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Completed orders, last {scoreboard.history.lookback_months} months (median days)
                  </p>
                  <table className="w-full max-w-lg text-sm">
                    <thead>
                      <tr className="text-muted-foreground">
                        <th className="py-0.5 text-left font-normal">Store</th>
                        <th className="py-0.5 text-right font-normal">n</th>
                        <th className="py-0.5 text-right font-normal">to place</th>
                        <th className="py-0.5 text-right font-normal">to receive</th>
                        <th className="py-0.5 text-right font-normal">end to end</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scoreboard.history.stores.map((s) => (
                        <tr key={s.store}>
                          <td className="py-0.5">{s.store}</td>
                          <td className="py-0.5 text-right tabular-nums text-muted-foreground">{s.n}</td>
                          <td className="py-0.5 text-right tabular-nums">{s.create_to_place?.median ?? '—'}</td>
                          <td className="py-0.5 text-right tabular-nums">{s.place_to_receive?.median ?? '—'}</td>
                          <td className="py-0.5 text-right font-medium tabular-nums">{s.end_to_end?.median ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {!!scoreboard.population.stale_beyond_live_window && (
                <p className="text-sm text-muted-foreground">
                  {scoreboard.population.stale_beyond_live_window} received orders are older than a
                  year and excluded from these figures — close-out backlog, not late delivery.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
