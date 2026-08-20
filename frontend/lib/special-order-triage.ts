// Shared triage config for the Special Orders page: the sub-triage breakdown that lives
// under each top-level procurement stage, plus helpers to map an SO onto its sub-triage
// and to label it. Used by both the stage tiles (KPIs) and the table's Flag column so the
// wording stays in lock-step.
import type { ProcurementStage, SpecialOrder, SpecialOrderFlag } from './types'

export type TriageTone = 'danger' | 'warn' | 'ok'

export interface SubTriage {
  key: string          // matches subKeyForOrder(o)
  label: string
  tone: TriageTone
}

// The sub-triages under each stage, in display order. The flag value an SO carries maps
// 1:1 onto a sub key here; a healthy (flag === 'none') SO maps to 'healthy'.
// Two regimes (mirrors _compute_flag in special_order_service.py): the pre-order stages
// (open_pool / unordered_po) are AGE-driven — days sitting in stage, after a 5-day grace —
// while the ordered stage is DATE-driven, judged against the Shopify ETA (customer promise)
// when present, else the PO's expected date.
// Tier boundaries as shipped by the backend (`meta.thresholds`), so the labels below are
// arithmetic on the real constants rather than numbers retyped by hand. The defaults match
// special_order_service.py at the time of writing and are only used before the payload lands.
export interface TriageThresholds {
  grace_days: number
  overdue_mid_min: number
  overdue_max: number
  ordered: { overdue: [number, number]; overdue_mid: [number, number]; critical_from: number }
  preorder: {
    healthy_below: number
    overdue: [number, number]
    overdue_mid: [number, number]
    critical_from: number
  }
}

export const DEFAULT_THRESHOLDS: TriageThresholds = {
  grace_days: 5,
  overdue_mid_min: 3,
  overdue_max: 7,
  ordered: { overdue: [1, 2], overdue_mid: [3, 7], critical_from: 8 },
  preorder: { healthy_below: 5, overdue: [5, 6], overdue_mid: [7, 11], critical_from: 12 },
}

const range = (r: [number, number]) => `${r[0]}-${r[1]}d`

/** Build the sub-triage config for the given thresholds. Prefer this over STAGE_SUBTRIAGES so a
 *  backend threshold change flows straight through to the tile labels. */
export function buildStageSubtriages(t: TriageThresholds = DEFAULT_THRESHOLDS):
    Record<ProcurementStage, SubTriage[]> {
  const p = t.preorder
  const o = t.ordered
  const preorder = (noun: string): SubTriage[] => [
    { key: 'critical', label: `${noun} ${p.critical_from}d+`, tone: 'danger' },
    { key: 'overdue_mid', label: `${noun} ${range(p.overdue_mid)}`, tone: 'danger' },
    { key: 'overdue', label: `${noun} ${range(p.overdue)}`, tone: 'danger' },
    { key: 'healthy', label: `Healthy (0-${p.healthy_below - 1}d)`, tone: 'ok' },
  ]
  return {
    open_pool: preorder('Open Order'),
    unordered_po: preorder('Unordered'),
    ordered: [
      { key: 'critical', label: `PO Critically Overdue (${o.critical_from}d+)`, tone: 'danger' },
      { key: 'overdue_mid', label: `PO Overdue (${range(o.overdue_mid)})`, tone: 'danger' },
      { key: 'overdue', label: `PO Overdue (${range(o.overdue)})`, tone: 'danger' },
      { key: 'no_eta', label: 'No ETA', tone: 'warn' },
      { key: 'healthy', label: 'Healthy', tone: 'ok' },
    ],
    received: [
      { key: 'ready_not_called', label: 'Ready, Not Called', tone: 'warn' },
      { key: 'healthy', label: 'Healthy', tone: 'ok' },
    ],
  }
}

export const STAGE_SUBTRIAGES: Record<ProcurementStage, SubTriage[]> = {
  open_pool: [
    { key: 'critical', label: 'Open Order 12d+', tone: 'danger' },
    { key: 'overdue_mid', label: 'Open Order 7-11d', tone: 'danger' },
    { key: 'overdue', label: 'Open Order 5-6d', tone: 'danger' },
    { key: 'healthy', label: 'Healthy (0-4d)', tone: 'ok' },
  ],
  unordered_po: [
    { key: 'critical', label: 'Unordered 12d+', tone: 'danger' },
    { key: 'overdue_mid', label: 'Unordered 7-11d', tone: 'danger' },
    { key: 'overdue', label: 'Unordered 5-6d', tone: 'danger' },
    { key: 'healthy', label: 'Healthy (0-4d)', tone: 'ok' },
  ],
  ordered: [
    { key: 'critical', label: 'PO Critically Overdue (8d+)', tone: 'danger' },
    { key: 'overdue_mid', label: 'PO Overdue (3-7d)', tone: 'danger' },
    { key: 'overdue', label: 'PO Overdue (1-2d)', tone: 'danger' },
    { key: 'no_eta', label: 'No ETA', tone: 'warn' },
    { key: 'healthy', label: 'Healthy', tone: 'ok' },
  ],
  received: [
    { key: 'ready_not_called', label: 'Ready, Not Called', tone: 'warn' },
    { key: 'healthy', label: 'Healthy', tone: 'ok' },
  ],
}

// The sub key an SO falls under within its stage.
export function subKeyForOrder(o: SpecialOrder): string {
  return o.flag === 'none' ? 'healthy' : o.flag
}

// The sub-triage label for a (stage, flag) pair — matches the tile wording exactly.
export function subTriageLabel(stage: ProcurementStage, flag: SpecialOrderFlag): string {
  const key = flag === 'none' ? 'healthy' : flag
  return STAGE_SUBTRIAGES[stage]?.find((s) => s.key === key)?.label ?? 'Healthy'
}
