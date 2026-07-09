'use client'

import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import {
  CheckCircle2, AlertTriangle, XCircle, Loader2, CircleDashed, CircleSlash, HelpCircle,
} from 'lucide-react'
import type { ScrapeHealth, ScrapeHealthCompetitor } from '@/lib/price-intel/types'

// Overall-status → pill styling + label. Kept theme-neutral (Tailwind tokens the
// rest of the dashboard already uses) so it reads in light and dark.
const OVERALL: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  healthy: { label: 'Healthy', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700', Icon: CheckCircle2 },
  degraded: { label: 'Degraded', cls: 'border-amber-200 bg-amber-50 text-amber-700', Icon: AlertTriangle },
  failed: { label: 'Failed', cls: 'border-red-200 bg-red-50 text-red-700', Icon: XCircle },
  running: { label: 'Running', cls: 'border-sky-200 bg-sky-50 text-sky-700', Icon: Loader2 },
  never: { label: 'No runs yet', cls: 'border-muted bg-muted text-muted-foreground', Icon: CircleDashed },
}

// Per-competitor bucket → dot colour + human label.
const BUCKET: Record<ScrapeHealthCompetitor['bucket'], { label: string; dot: string; Icon: typeof CheckCircle2 }> = {
  ok: { label: 'OK', dot: 'text-emerald-500', Icon: CheckCircle2 },
  empty: { label: 'No products returned', dot: 'text-amber-500', Icon: AlertTriangle },
  no_matches: { label: 'Products found, none tracked', dot: 'text-amber-500', Icon: CircleDashed },
  failed: { label: 'Failed', dot: 'text-red-500', Icon: XCircle },
  skipped: { label: 'No connector', dot: 'text-muted-foreground', Icon: CircleSlash },
  unknown: { label: 'Not yet crawled', dot: 'text-muted-foreground', Icon: HelpCircle },
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

export function ScrapeHealthPill({ health }: { health: ScrapeHealth | undefined }) {
  const [open, setOpen] = useState(false)
  if (!health) return null

  const meta = OVERALL[health.overall] ?? OVERALL.never
  const { Icon } = meta
  const run = health.last_run
  const { counts } = health

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${meta.cls}`}
          title="Last scrape health — click for the per-store breakdown"
        >
          <Icon className={`h-3.5 w-3.5 ${health.overall === 'running' ? 'animate-spin' : ''}`} />
          Scrape: {meta.label}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0 text-sm">
        <div className="px-3 py-2.5">
          <div className="flex items-center justify-between">
            <span className="font-medium">Last scrape</span>
            <span className="text-xs text-muted-foreground">{timeAgo(run?.finished_at ?? run?.started_at ?? null)}</span>
          </div>
          {run && (
            <div className="mt-1 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
              <span>{run.observations_count?.toLocaleString() ?? 0} obs</span>
              <span>{run.changes_count ?? 0} changes</span>
              <span className="capitalize">{run.trigger}</span>
            </div>
          )}
          {run?.error && (
            <p className="mt-1.5 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{run.error}</p>
          )}
        </div>
        <Separator />
        <div className="max-h-72 overflow-y-auto px-1.5 py-1.5">
          {health.competitors.map((c) => {
            const b = BUCKET[c.bucket] ?? BUCKET.unknown
            const B = b.Icon
            return (
              <div key={c.competitor_id ?? c.name} className="flex items-center gap-2 rounded px-1.5 py-1">
                <B className={`h-3.5 w-3.5 shrink-0 ${b.dot}`} />
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="shrink-0 text-xs text-muted-foreground" title={c.status ?? undefined}>
                  {b.label}
                </span>
              </div>
            )
          })}
          {health.competitors.length === 0 && (
            <p className="px-2 py-1 text-xs text-muted-foreground">No enabled competitors.</p>
          )}
        </div>
        <Separator />
        <div className="px-3 py-2 text-xs text-muted-foreground">
          {counts.ok}/{counts.total} stores OK
          {counts.empty > 0 && ` · ${counts.empty} empty`}
          {counts.no_matches > 0 && ` · ${counts.no_matches} no matches`}
          {counts.failed > 0 && ` · ${counts.failed} failed`}
        </div>
      </PopoverContent>
    </Popover>
  )
}
