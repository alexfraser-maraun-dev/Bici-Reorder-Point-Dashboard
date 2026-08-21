import { describe, expect, it } from 'vitest'
import { matchesActionFilter, validActionFilter } from './special-orders-content'

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
