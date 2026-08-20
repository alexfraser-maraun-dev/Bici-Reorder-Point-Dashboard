'use client'

import { useState } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { useSoScoreboard } from '@/lib/hooks'
import type { DwellStat } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ChevronDown, ChevronRight, BarChart3 } from 'lucide-react'

const STAGE_LABEL: Record<string, string> = {
  open_pool: 'Awaiting a PO',
  unordered_po: 'On an unplaced PO',
  ordered: 'Placed, awaiting arrival',
  received: 'Received',
}

function DwellTable({ title, data, unit = 'days' }: {
  title: string; data: Record<string, DwellStat | null>; unit?: string
}) {
  const entries = Object.entries(data).filter(([, v]) => v) as [string, DwellStat][]
  if (!entries.length) return null
  entries.sort((a, b) => b[1].median - a[1].median)
  return (
    <div className="min-w-0">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">{title}</p>
      <table className="w-full text-xs">
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
      <p className="mt-0.5 text-[10px] text-muted-foreground/60">{unit}</p>
    </div>
  )
}

/** Where special-order time goes, and who owns each failure.
 *
 *  Current-state numbers are correct from the first sweep because stage entry timestamps are
 *  derived from Lightspeed's own dates rather than observed. */
export function SoScoreboard() {
  const [open, setOpen] = useState(false)
  const { scoreboard, isLoading } = useSoScoreboard(open)
  const p = scoreboard?.promise

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-muted/50"
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <BarChart3 className="h-4 w-4 text-muted-foreground" />
        Scoreboard
        <span className="text-xs font-normal text-muted-foreground">
          — where the time goes, and who owns it
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t px-3 py-3">
          {isLoading && <Skeleton className="h-40 w-full" />}

          {scoreboard && (
            <>
              <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
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
                  <p className="text-[11px] text-muted-foreground">
                    {p?.met ?? 0} met of {p?.settled ?? 0} delivered
                  </p>
                </div>
                <div className="space-y-0.5 text-xs">
                  {!!p?.breached_outstanding && (
                    <p className="text-red-600">{p.breached_outstanding} past promise, not yet here</p>
                  )}
                  <p className="text-muted-foreground">{p?.undetermined ?? 0} still inside their window</p>
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
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                    Why parked orders are waiting
                  </p>
                  <div className="flex flex-wrap gap-x-4 text-xs">
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
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                    Completed orders, last {scoreboard.history.lookback_months} months (median days)
                  </p>
                  <table className="w-full max-w-lg text-xs">
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
                <p className="text-xs text-muted-foreground">
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
