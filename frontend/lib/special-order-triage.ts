// Shared tone vocabulary and stage-dwell banding for the special-order tiles.
//
// This module used to hold the age-based sub-triage config (STAGE_SUBTRIAGES) and the
// threshold-derived label builder. Both went when the tiles became positional — they now split
// only into needs-action vs on-track, which is read straight off `actionable` rather than from
// tier boundaries. The backend still exposes `meta.thresholds`; nothing consumes it.
import type { SpecialOrder, TriageStage } from '@/lib/types'

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
