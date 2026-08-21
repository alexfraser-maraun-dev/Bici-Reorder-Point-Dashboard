import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

import { useSpecialOrders } from './hooks'

const payload = {
  orders: [],
  shopify_only: [],
  summary: {
    by_severity: {},
    by_owner: {},
    missing_promise_by_owner: {},
    actionable: 0,
    acked: 0,
    checkback_due: 0,
    escalated: 0,
    missing_promise: 0,
  },
  fetched_at: '2026-08-20T20:00:00Z',
  meta: { live_only_days: 365, total_before_window: 0, sources: {} },
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {children}
    </SWRConfig>
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useSpecialOrders live scope', () => {
  it.each([
    [true, 'live_only_days=365'],
    [false, 'live_only_days=0'],
  ])('puts the selected scope in the request key', async (liveOnly, expectedQuery) => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useSpecialOrders({ liveOnly }), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(expectedQuery),
    )
  })

  it('preserves the selected scope when forcing a refresh', async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useSpecialOrders({ liveOnly: false }), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    await result.current.refetch()

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('live_only_days=0&refresh=true'),
    )
  })
})
