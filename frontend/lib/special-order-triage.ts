// Shared triage config for the Special Orders page: the sub-triage breakdown that lives
// under each top-level procurement stage, plus helpers to map an SO onto its sub-triage
// and to label it. Used by both the stage tiles (KPIs) and the table's Flag column so the
// wording stays in lock-step.
import type { ProcurementStage, SpecialOrder, SpecialOrderFlag } from './types'

// Keep in sync with _STALE_STAGE_DAYS in backend/app/services/special_order_service.py.
export const STALE_STAGE_DAYS = 5

export type TriageTone = 'danger' | 'warn' | 'ok'

export interface SubTriage {
  key: string          // matches subKeyForOrder(o)
  label: string
  tone: TriageTone
}

// The sub-triages under each stage, in display order. The flag value an SO carries maps
// 1:1 onto a sub key here; a healthy (flag === 'none') SO maps to 'healthy'.
export const STAGE_SUBTRIAGES: Record<ProcurementStage, SubTriage[]> = {
  open_pool: [
    { key: 'aged', label: `Open Pool > ${STALE_STAGE_DAYS} Days`, tone: 'warn' },
    { key: 'healthy', label: 'Healthy', tone: 'ok' },
  ],
  unordered_po: [
    { key: 'aged', label: `Unordered PO > ${STALE_STAGE_DAYS} Days`, tone: 'warn' },
    { key: 'healthy', label: 'Healthy', tone: 'ok' },
  ],
  ordered: [
    { key: 'overdue', label: 'Overdue', tone: 'danger' },
    { key: 'critical', label: 'Critical (8d+)', tone: 'danger' },
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
