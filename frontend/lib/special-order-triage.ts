// Shared tone vocabulary for the special-order tiles.
//
// This module used to hold the age-based sub-triage config (STAGE_SUBTRIAGES) and the
// threshold-derived label builder. Both went when the tiles became positional — they now split
// only into needs-action vs on-track, which is read straight off `actionable` rather than from
// tier boundaries. The backend still exposes `meta.thresholds`; nothing consumes it.
export type TriageTone = 'danger' | 'warn' | 'ok'
