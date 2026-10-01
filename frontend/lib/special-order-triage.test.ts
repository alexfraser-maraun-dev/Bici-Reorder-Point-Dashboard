import { describe, expect, it } from 'vitest'
import { stageDwellSeries } from './special-order-triage'
import type { SpecialOrderStageHistory } from './types'

const STAGES: SpecialOrderStageHistory['stages'] = ['shopify', 'open_pool', 'unordered_po', 'ordered', 'received']
const ORDERED = 3

function history(rows: SpecialOrderStageHistory['rows'], tracking: SpecialOrderStageHistory['tracking_since'] = { ordered: '2026-09-01' }): SpecialOrderStageHistory {
  return {
    start: '2026-09-21',
    end: '2026-10-01',
    stages: STAGES,
    tracking_since: tracking,
    columns: ['stage', 'entered', 'left', 'shop_id', 'source', 'created'],
    rows,
  }
}

const ALL = { shopIds: null, source: 'all', liveOnly: true }

describe('stageDwellSeries', () => {
  it('takes the daily median of days-in-stage and ends on today', () => {
    // Open since -20 and -4; on day 0 the dwells are 20 and 4 -> median 12 (raw).
    const result = stageDwellSeries(history([
      [ORDERED, -20, null, '1', 'neither', -30],
      [ORDERED, -4, null, '1', 'neither', -10],
    ]), ALL)
    const series = result.ordered!
    expect(series.startOffset).toBe(-10)
    expect(series.values).toHaveLength(11)
    // Day -10 only the older row is in stage: dwell 10, smoothed with day -9's 11 -> 10.5.
    expect(series.values[0]).toBe(10.5)
    // Last day smooths -1 (median of 19 and 3 = 11) with 0 (12) -> 11.5.
    expect(series.values.at(-1)).toBe(11.5)
  })

  it('stops counting an interval on its exit day', () => {
    const series = stageDwellSeries(history([[ORDERED, -15, -5, '1', 'neither', -15]]), ALL).ordered!
    expect(series.values.slice(-5)).toEqual([null, null, null, null, null])
  })

  it('never starts before the stage was first tracked', () => {
    const series = stageDwellSeries(history([[ORDERED, -20, null, '1', 'neither', -20]], { ordered: '2026-09-28' }), ALL).ordered!
    expect(series.startOffset).toBe(-3)
    expect(stageDwellSeries(history([[ORDERED, -20, null, '1', 'neither', -20]]), ALL).shopify).toBeUndefined()
  })

  it('applies store, source and the live window', () => {
    const rows: SpecialOrderStageHistory['rows'] = [
      [ORDERED, -2, null, '1', 'workorder', -2],
      [ORDERED, -8, null, '2', 'neither', -8],
      [ORDERED, -9, null, '1', null, -400],
    ]
    const at = (filters: typeof ALL) => stageDwellSeries(history(rows), filters).ordered!.values.at(-1)
    expect(at({ ...ALL, shopIds: new Set(['2']) })).toBe(7.5)
    expect(at({ ...ALL, source: 'workorder' })).toBe(1.5)
    // The 400-day-old order is outside the live window; with archive it counts (source null -> neither).
    expect(at({ ...ALL, source: 'neither' })).toBe(7.5)
    expect(at({ ...ALL, source: 'neither', liveOnly: false })).toBe(8)
  })
})
