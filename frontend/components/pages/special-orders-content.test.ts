import { afterEach, describe, expect, it, vi } from 'vitest'
import { matchesActionFilter, validActionFilter } from './special-orders-content'
import { dwellBand, stageDwellDays } from '@/lib/special-order-triage'
import type { SpecialOrder } from '@/lib/types'

describe('Special Orders action filter', () => {
  it('falls back to all actions for missing or unknown URL values', () => {
    expect(validActionFilter(null)).toBe('all')
    expect(validActionFilter('not-a-real-action')).toBe('all')
    expect(validActionFilter('promise_needed')).toBe('promise_needed')
    expect(validActionFilter('receipt_exception')).toBe('receipt_exception')
  })

  it('uses all queue memberships so parallel actions remain discoverable', () => {
    const order = {
      work_state: 'vendor_followup' as const,
      queue_states: ['in_transit', 'vendor_followup', 'promise_needed'] as const,
    }

    expect(matchesActionFilter(order, 'vendor_followup')).toBe(true)
    expect(matchesActionFilter(order, 'promise_needed')).toBe(true)
    expect(matchesActionFilter(order, 'needs_ordering')).toBe(false)
  })

  it('uses the primary work state for healthy in-transit rows', () => {
    const order = {
      work_state: 'on_track' as const,
      queue_states: ['in_transit'] as const,
    }

    expect(matchesActionFilter(order, 'on_track')).toBe(true)
    expect(matchesActionFilter(order, 'all')).toBe(true)
  })

  it('matches the Start/Done statuses on work_status, not on a queue', () => {
    // These two are how a row LEAVES the active queues, so they can never be answered from
    // queue_states — an in-progress row still sits in whatever queue it was claimed from.
    const started = {
      work_state: 'needs_ordering' as const,
      queue_states: ['needs_ordering'] as const,
      work_status: 'in_progress' as const,
    }

    expect(matchesActionFilter(started, 'in_progress')).toBe(true)
    expect(matchesActionFilter(started, 'done')).toBe(false)
    expect(matchesActionFilter({ ...started, work_status: 'done' }, 'done')).toBe(true)
    expect(matchesActionFilter({ ...started, work_status: null }, 'in_progress')).toBe(false)
    // A cleared row is still findable by the work it was cleared from.
    expect(matchesActionFilter(started, 'needs_ordering')).toBe(true)
    expect(validActionFilter('in_progress')).toBe('in_progress')
    expect(validActionFilter('done')).toBe('done')
  })

  it.each(['po_receiving', 'po_complete_so_unreceived'])(
    'finds the %s receiving exception without changing the primary work state',
    (receivingState) => {
      const order = {
        work_state: 'on_track' as const,
        queue_states: ['in_transit'] as const,
        receiving_state: receivingState,
      }

      expect(matchesActionFilter(order, 'receipt_exception')).toBe(true)
      expect(matchesActionFilter(order, 'on_track')).toBe(false)
    },
  )
})

describe('Stage dwell banding', () => {
  const NOW = new Date('2026-08-20T12:00:00Z')

  afterEach(() => { vi.useRealTimers() })

  function so(overrides: Partial<SpecialOrder>): SpecialOrder {
    return {
      kind: 'ls',
      procurement_stage: 'open_pool',
      created_date: '2026-08-01',
      po_created_date: null,
      ordered_date: null,
      so_received_date: null,
      po_received_date: null,
      ...overrides,
    } as SpecialOrder
  }

  it('measures each stage from its own entry date, not total order age', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)

    // The same 19-day-old special order reads completely differently per stage — which is the
    // point. `order.days_in_stage` collapses to total age for `ordered` and `received`, so an
    // "In transit · 11d+" count built on it would mean "the customer asked 11 days ago".
    expect(stageDwellDays(so({}))).toBe(19)
    expect(stageDwellDays(so({ procurement_stage: 'unordered_po', po_created_date: '2026-08-16' }))).toBe(4)
    expect(stageDwellDays(so({ procurement_stage: 'ordered', ordered_date: '2026-08-18' }))).toBe(2)
    expect(stageDwellDays(so({ procurement_stage: 'received', so_received_date: '2026-08-19' }))).toBe(1)
    // A Shopify-intake pseudo-row has no Lightspeed stage; it dwells from the order date.
    expect(stageDwellDays(so({ kind: 'shopify', created_date: '2026-08-13' }))).toBe(7)
    // Missing stage timestamp falls back to the creation date rather than dropping the row.
    expect(stageDwellDays(so({ procurement_stage: 'ordered', ordered_date: null }))).toBe(19)
  })

  it('bands on inclusive upper bounds, and treats an undatable order as stalled', () => {
    expect(dwellBand(0)).toBe('fresh')
    expect(dwellBand(1)).toBe('fresh')
    expect(dwellBand(2)).toBe('early')
    expect(dwellBand(4)).toBe('early')
    expect(dwellBand(5)).toBe('ageing')
    expect(dwellBand(10)).toBe('ageing')
    expect(dwellBand(11)).toBe('stalled')
    // An order with no usable date is not a fresh one — defaulting it into `<1d` would quietly
    // flatter the bucket it lands in.
    expect(dwellBand(null)).toBe('stalled')
  })
})
