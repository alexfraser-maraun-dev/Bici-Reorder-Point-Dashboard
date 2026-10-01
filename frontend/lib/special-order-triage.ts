// Shared tone vocabulary and stage-dwell banding for the special-order tiles.
//
// This module used to hold the age-based sub-triage config (STAGE_SUBTRIAGES) and the
// threshold-derived label builder. Both went when the tiles became positional — they now split
// only into needs-action vs on-track, which is read straight off `actionable` rather than from
// tier boundaries. The backend still exposes `meta.thresholds`; nothing consumes it.
import type { SpecialOrder, SpecialOrderStageHistory, TriageStage } from '@/lib/types'

export type TriageTone = 'danger' | 'warn' | 'ok'

/** How long each pipeline bucket's orders have been sitting in THAT step.
 *
 * The bands split each scorecard total so a bucket of 8 stops reading the same whether those 8
 * arrived this morning or have been stuck for a month. Upper bounds are inclusive. */
export const DWELL_BANDS = [
  { key: 'fresh', label: '<1d', max: 1, bar: 'bg-emerald-500' },
  { key: 'early', label: '2–4d', max: 4, bar: 'bg-lime-400' },
  { key: 'ageing', label: '5–10d', max: 10, bar: 'bg-amber-500' },
  { key: 'stalled', label: '11d+', max: Number.POSITIVE_INFINITY, bar: 'bg-red-500' },
] as const

export type DwellBandKey = (typeof DWELL_BANDS)[number]['key']

export type DwellCounts = Record<DwellBandKey, number>

export function emptyDwellCounts(): DwellCounts {
  return { fresh: 0, early: 0, ageing: 0, stalled: 0 }
}

/** Whole calendar days between a date string and today.
 *
 * Both sides are anchored to LOCAL noon. Anchoring only the stored date (the trick the tile's
 * `formatDate` uses to stop an ISO date rendering as the previous day) and comparing it against
 * a raw `Date.now()` drifts by one whenever the local offset pushes the pair across a boundary —
 * which is a silent off-by-one in every band count, not a rounding nicety. */
function daysSince(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(`${value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(parsed)) return null
  const now = new Date()
  const todayNoon = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12).getTime()
  return Math.max(0, Math.round((todayNoon - parsed) / 86_400_000))
}

/** Days this order has been in its CURRENT stage.
 *
 * Mirrors the backend's canonical stage→timestamp map (`so_stage_log._STAGE_TIMESTAMP_FIELD`).
 * Deliberately not `order.days_in_stage`: that field collapses to total SO age for the `ordered`
 * and `received` stages, which would make an "In transit · 11d+" count mean "the customer asked
 * 11 days ago" rather than "this has been in transit 11 days". */
export function stageDwellDays(order: SpecialOrder): number | null {
  const stage: TriageStage = order.kind === 'shopify' ? 'shopify' : order.procurement_stage
  const anchor =
    stage === 'unordered_po' ? order.po_created_date
      : stage === 'ordered' ? order.ordered_date
        : stage === 'received' ? order.so_received_date ?? order.po_received_date
          : order.created_date
  return daysSince(anchor ?? order.created_date)
}

export function dwellBand(days: number | null): DwellBandKey {
  if (days == null) return 'stalled' // an order with no usable date is not a fresh one
  return (DWELL_BANDS.find((band) => days <= band.max) ?? DWELL_BANDS[DWELL_BANDS.length - 1]).key
}

/** One stage's daily median dwell. `startOffset` is the day offset of `values[0]` relative to
 *  the payload's `end` (today = 0), so `values.at(-1)` is today. Null = nothing in the stage. */
export interface DwellSeries {
  startOffset: number
  values: (number | null)[]
}

export interface DwellSeriesFilters {
  /** Lightspeed shop ids the Store filter resolves to; null = all stores. */
  shopIds: ReadonlySet<string> | null
  /** Same vocabulary as the Source filter ('all' | 'shopify' | 'workorder' | 'neither'). */
  source: string
  /** Mirror the 365-day live window the worklist applies when archive is excluded. */
  liveOnly: boolean
}

const LIVE_WINDOW_DAYS = 365

function dayOffset(from: string, to: string): number {
  return Math.round((Date.parse(`${from.slice(0, 10)}T12:00:00Z`) - Date.parse(`${to.slice(0, 10)}T12:00:00Z`)) / 86_400_000)
}

function median(values: number[]): number {
  values.sort((a, b) => a - b)
  const mid = values.length >> 1
  return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2
}

/** Median days-in-current-stage for every day of the trend window, per stage.
 *
 * Only Store, Source and the archive toggle apply: they are recorded on the historical rows.
 * Type, Action, Search and the queue tab describe the CURRENT state of today's rows and have
 * no meaning for an order that left the stage three weeks ago.
 *
 * Each stage starts at its own `tracking_since` — before that, orders that had already left are
 * missing from the log and the median would read artificially high. A 3-day centred mean takes
 * the day-to-day jitter out so the line reads as a trend. */
export function stageDwellSeries(
  history: SpecialOrderStageHistory | undefined,
  filters: DwellSeriesFilters,
): Partial<Record<TriageStage, DwellSeries>> {
  const out: Partial<Record<TriageStage, DwellSeries>> = {}
  if (!history?.start || history.rows.length === 0) return out
  const windowStart = dayOffset(history.start, history.end)

  const byStage = new Map<number, SpecialOrderStageHistory['rows']>()
  for (const row of history.rows) {
    const [stageIndex, , , shopId, source] = row
    if (filters.shopIds && !(shopId != null && filters.shopIds.has(String(shopId)))) continue
    if (filters.source !== 'all' && (source ?? 'neither') !== filters.source) continue
    const list = byStage.get(stageIndex)
    if (list) list.push(row)
    else byStage.set(stageIndex, [row])
  }

  history.stages.forEach((stage, stageIndex) => {
    const tracked = history.tracking_since[stage]
    if (!tracked) return
    const first = Math.max(windowStart, dayOffset(tracked, history.end))
    const rows = byStage.get(stageIndex) ?? []
    const raw: (number | null)[] = []
    for (let day = first; day <= 0; day += 1) {
      const dwell: number[] = []
      for (const [, entered, left, , , created] of rows) {
        if (entered > day || (left != null && left <= day)) continue
        if (filters.liveOnly && day - created > LIVE_WINDOW_DAYS) continue
        dwell.push(day - entered)
      }
      raw.push(dwell.length ? median(dwell) : null)
    }
    const values = raw.map((value, i) => {
      if (value == null) return null
      const window = [raw[i - 1], value, raw[i + 1]].filter((v): v is number => v != null)
      return window.reduce((sum, v) => sum + v, 0) / window.length
    })
    out[stage] = { startOffset: first, values }
  })
  return out
}
