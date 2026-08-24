'use client'

import { useState, useEffect } from 'react'

import useSWR from 'swr'

// Generic fetcher for SWR
const fetcher = async (url: string) => {
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch data')
  return res.json()
}

const adminDashboardSWRConfig = {
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
  revalidateIfStale: false,
  refreshInterval: 0,
  dedupingInterval: 900000,
}

export function useReplenishmentData(
  forecastPeriod: number,
  safetyDays: number,
  growthMultiplier: number,
  demandWeights: { weight14d: number; weight15To30d: number; weight31To60d: number },
  adjustmentMode: string,
  enabled: boolean = true
) {
  const baseUrl = '/backend'
  const query = [
    `forecast_period=${forecastPeriod}`,
    `safety_days=${safetyDays}`,
    `growth_multiplier=${growthMultiplier}`,
    `weight_14d=${demandWeights.weight14d / 100}`,
    `weight_15_30d=${demandWeights.weight15To30d / 100}`,
    `weight_31_60d=${demandWeights.weight31To60d / 100}`,
    `adjustment_mode=${adjustmentMode}`,
  ].join('&')
  const url = enabled ? `${baseUrl}/api/replenishment/data?${query}` : null
  
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    dedupingInterval: 600000, // Keep in cache for 10 minutes
  })

  const [isRefetching, setIsRefetching] = useState(false)

  const handleRefetch = async () => {
    if (!url) return
    setIsRefetching(true)
    try {
      const refreshUrl = `${url}&force_refresh=true`
      const newData = await fetcher(refreshUrl)
      mutate(newData, false) // update local data without triggering another fetch
    } catch (e) {
      console.error("Failed to refetch", e)
    } finally {
      setIsRefetching(false)
    }
  }

  return { data, isLoading: isLoading || isRefetching, error, refetch: handleRefetch }
}

// Hook for recommendation runs (History)
export function useRecommendationRuns() {
  const baseUrl = '/backend'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/replenishment/runs`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

// Hook for writeback audit (Audit Logs)
export function useWritebackAudit() {
  const baseUrl = '/backend'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/replenishment/logs`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

// Hook for managed SKUs
export function useManagedSkus() {
  const baseUrl = '/backend'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/skus`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

export function useActiveVendorLeadTimes() {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/replenishment/active-vendor-lead-times`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  const refetch = () => mutate(fetcher(`${url}?force_refresh=true`), false)
  return { data: data || null, isLoading, error, refetch }
}

export function useBrandSourcingRules() {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/replenishment/brand-sourcing-rules`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  const refetch = () => mutate(fetcher(`${url}?force_refresh=true`), false)
  return { data: data || null, isLoading, error, refetch }
}

// Demand & Seasonality: category seasonal profiles for the visualization layer.
export function useSeasonalProfiles(location?: string | number | null, measure: 'units' | 'cogs' | 'revenue' = 'units') {
  const baseUrl = '/backend'
  const params = new URLSearchParams()
  if (location !== undefined && location !== null && location !== '') {
    params.set('location', String(location))
  }
  params.set('measure', measure)
  const qs = params.toString()
  const url = `${baseUrl}/api/forecast/seasonal-profiles${qs ? `?${qs}` : ''}`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  const refetch = () => mutate(fetcher(url), false)
  return { data: data || null, isLoading, error, refetch }
}

// Demand & Seasonality: monthly history + forward forecast for a category or SKU.
// Passing a null id disables the fetch (used while a detail panel is closed).
export function useDemandHistory(
  scope: 'category' | 'sku',
  id: string | null,
  location?: string | number | null,
  measure: 'units' | 'cogs' | 'revenue' = 'units',
) {
  const baseUrl = '/backend'
  let url: string | null = null
  if (id) {
    const params = new URLSearchParams({ scope, id: String(id) })
    if (location !== undefined && location !== null && location !== '') {
      params.set('location', String(location))
    }
    params.set('measure', measure)
    url = `${baseUrl}/api/forecast/history?${params.toString()}`
  }
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data || null, isLoading, error, refetch: () => mutate() }
}

// Demand & Seasonality: forward weeks-of-cover heatmap (soonest stockouts first).
export function useCoverage(location?: string | null, limit: number = 150) {
  const baseUrl = '/backend'
  const params = new URLSearchParams({ limit: String(limit) })
  if (location) params.set('location', location)
  const url = `${baseUrl}/api/forecast/coverage?${params.toString()}`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data || null, isLoading, error, refetch: () => mutate() }
}

export async function saveBrandSourcingRule(rule: {
  brand_name: string
  preferred_vendor_id?: string | null
  preferred_vendor_name?: string | null
  active?: boolean
  notes?: string | null
  updated_by?: string
}) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/replenishment/brand-sourcing-rules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rule),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to save brand sourcing rule')
  }
  return res.json()
}

// Writes the customer-promised ETA back to Shopify (the custom.special_order_eta order
// metafield); `eta: null` clears it. The backend rebuilds its dashboard cache on success, so
// the caller only needs a plain revalidate (no forced Lightspeed re-walk) to see the value.
export async function updateShopifyEta(input: {
  shopify_order_id: string
  eta: string | null
}) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/special-orders/eta`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to update Shopify ETA')
  }
  return res.json()
}

// Service workorders do not have a Shopify order metafield and `workorder.etaOut` is the bike's
// completion estimate, not a parts promise. This app-owned promise is audited separately and
// feeds the same SLA/work-queue model without writing a misleading date back to Lightspeed.
export async function updateServicePromise(
  specialOrderId: string,
  promiseDate: string | null,
) {
  const res = await fetch(`/backend/api/special-orders/${specialOrderId}/service-promise`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ promise_date: promiseDate }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to update the service parts promise')
  }
  return res.json() as Promise<{
    status: 'success'
    special_order_id: string
    service_promise_date: string | null
    service_promise_source: 'service_manual' | null
    changed: boolean
  }>
}

// Manually links / unlinks an LS special order and a Shopify order (persisted override;
// unlink also forbids auto-matching from re-proposing the pair). The backend rebuilds its
// dashboard cache on success — follow with a plain revalidate.
async function postSoMatchOverride(
  path: 'match' | 'unmatch',
  input: { special_order_id: string; shopify_order_id: string }
) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/special-orders/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || `Failed to ${path} the special order`)
  }
  return res.json()
}

// Looks up ANY Shopify order by number (or email / customer name) — including fulfilled,
// untagged and old orders the dashboard's `SO`-tagged population never sees. Returns each
// order with its line items so the user can confirm before committing the link.
export async function lookupShopifyOrders(term: string): Promise<import('./types').ShopifyOrderLookup[]> {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/special-orders/shopify-lookup?q=${encodeURIComponent(term)}`)
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Shopify lookup failed')
  }
  const data = await res.json()
  return data?.orders ?? []
}

export const matchSpecialOrder = (input: { special_order_id: string; shopify_order_id: string }) =>
  postSoMatchOverride('match', input)

export const unmatchSpecialOrder = (input: { special_order_id: string; shopify_order_id: string }) =>
  postSoMatchOverride('unmatch', input)

export async function saveSpecialOrderMatchDecisions(
  decisions: { special_order_id: string; shopify_order_id: string; action: 'link' | 'unlink' }[],
) {
  const res = await fetch('/backend/api/special-orders/match-decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to save the matching decisions')
  }
  return res.json() as Promise<{ status: 'success'; saved: number }>
}

// ---------------------------------------------------------------------------
// Purchase Orders
// ---------------------------------------------------------------------------

// Lists PO drafts (optionally filtered by status).
export function usePODrafts(status?: string) {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/po/drafts${status ? `?status=${status}` : ''}`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data?.data || [], isLoading, error, refetch: mutate }
}

// Fetches a single draft with its line items.
export function usePODraft(draftId: string | null) {
  const baseUrl = '/backend'
  const url = draftId ? `${baseUrl}/api/po/drafts/${draftId}` : null
  const { data, error, mutate, isLoading } = useSWR(url, fetcher)
  return { data: data?.data || null, isLoading, error, refetch: mutate }
}

// Reports whether the Lightspeed token can access purchase orders
// (i.e. was re-authorized with the employee:purchase_orders scope).
export function useLightspeedPoAccess() {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/health/lightspeed-po`
  // Don't throw on 503 — treat a non-ok response as "no access".
  const poFetcher = async (u: string) => {
    const res = await fetch(u)
    return { poAccess: res.ok }
  }
  const { data, isLoading, mutate } = useSWR(url, poFetcher, {
    revalidateOnFocus: false,
    refreshInterval: 0,
  })
  return { poAccess: data?.poAccess ?? null, isLoading, refetch: mutate }
}

// Filters the backend's shared complete Lightspeed PO snapshot by vendor/shop.
export function useOpenOrders(vendorId?: string, shopId?: string) {
  const baseUrl = '/backend'
  const poFetcher = async (requestUrl: string) => {
    const response = await fetch(requestUrl)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail || 'Complete Lightspeed PO snapshot unavailable')
    }
    return response.json()
  }
  const params = new URLSearchParams()
  if (vendorId) params.set('vendor_id', vendorId)
  if (shopId) params.set('shop_id', shopId)
  const qs = params.toString()
  const url = `${baseUrl}/api/po/open-orders${qs ? `?${qs}` : ''}`
  const { data, error, mutate, isLoading } = useSWR(url, poFetcher, adminDashboardSWRConfig)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const refetch = async (forceRefresh: boolean = false) => {
    setIsRefreshing(true)
    try {
      const refreshUrl = forceRefresh
        ? `${url}${url.includes('?') ? '&' : '?'}refresh=true`
        : url
      const fresh = await poFetcher(refreshUrl)
      await mutate(fresh, { revalidate: false })
      return fresh
    } finally {
      setIsRefreshing(false)
    }
  }
  return {
    data: data?.data || [],
    meta: data?.meta || null,
    isLoading,
    isRefreshing,
    error,
    refetch,
  }
}

// PO Tracker: ordered/partially-received POs triaged against expected arrival.
// The backend caches the Lightspeed walk for ~5 min; refetch() forces a re-walk.
export function usePoWatch(orderedWithinDays: number) {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/po/watch?ordered_within_days=${orderedWithinDays}`
  const { data, error, mutate, isLoading } = useSWR<import('./types').PoWatchResponse>(
    url, fetcher, adminDashboardSWRConfig
  )
  const [isRefreshing, setIsRefreshing] = useState(false)
  const refetch = async () => {
    setIsRefreshing(true)
    try {
      const fresh = await fetcher(`${url}&force_refresh=true`)
      await mutate(fresh, { revalidate: false })
    } finally {
      setIsRefreshing(false)
    }
  }
  // Plain re-GET (backend cache is fine) — used after ack/unack writes.
  const revalidate = async () => {
    const fresh = await fetcher(url)
    await mutate(fresh, { revalidate: false })
  }
  return {
    orders: data?.orders ?? [],
    summary: data?.summary,
    meta: data?.meta,
    isLoading,
    isRefreshing,
    error,
    refetch,
    revalidate,
  }
}

// Line-level detail for one PO (fetched when a tracker row is expanded).
export async function fetchPoWatchLines(orderId: string): Promise<import('./types').PoWatchLine[]> {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/po/watch/${orderId}/lines`)
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to load PO lines')
  }
  const payload = await res.json()
  return payload?.data?.lines ?? []
}

// Acknowledge/snooze a late-PO alert. Passing the row's current expected_date pins
// the ack to it, so an ETA change in Lightspeed re-arms the alert automatically.
export async function ackPoWatchAlert(input: {
  order_id: string
  expected_date: string | null
  snooze_days?: number | null
  note?: string
  acked_by?: string
}) {
  const baseUrl = '/backend'
  const { order_id, ...body } = input
  const res = await fetch(`${baseUrl}/api/po/watch/${order_id}/ack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to acknowledge the PO alert')
  }
  return res.json()
}

export async function unackPoWatchAlert(orderId: string) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/po/watch/${orderId}/ack`, { method: 'DELETE' })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to clear the acknowledgement')
  }
  return res.json()
}

async function planningMutation(path: string, method: 'POST' | 'PATCH', body?: unknown) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    const detail = errorData?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Planning request failed')
  }
  return res.json()
}

export function createPlanningRun(input: {
  horizon_weeks?: number
  location_ids?: string[]
  scope_type?: import('./types').PlanningScope
  scope_value?: string
  item_ids?: string[]
  config?: Partial<import('./types').PlanningConfig>
} = {}) {
  return planningMutation('/api/planning/runs', 'POST', input)
}

export function useLatestPlanningRun() {
  const baseUrl = '/backend'
  const url = `${baseUrl}/api/planning/runs/latest`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data?.data || null, isLoading, error, refetch: mutate }
}

export function usePlanningModels() {
  const baseUrl = '/backend'
  const { data, error, isLoading } = useSWR(`${baseUrl}/api/planning/models`, fetcher, adminDashboardSWRConfig)
  return { data: data?.data || {}, isLoading, error }
}

export function createPODraft(runId: string, recommendationIds: string[], createdBy?: string) {
  return planningMutation('/api/po/drafts', 'POST', {
    run_id: runId,
    recommendation_ids: recommendationIds,
    created_by: createdBy,
  })
}

export function updatePODraft(draftId: string, expectedVersion: number, lines: unknown[]) {
  return planningMutation(`/api/po/drafts/${draftId}`, 'PATCH', {
    expected_version: expectedVersion,
    lines,
  })
}

export function setPODraftTarget(draftId: string, expectedVersion: number, orderId: string | null) {
  return planningMutation(`/api/po/drafts/${draftId}/target-order`, 'POST', {
    expected_version: expectedVersion,
    order_id: orderId,
  })
}

export function transitionPODraft(draftId: string, expectedVersion: number, status: string) {
  return planningMutation(`/api/po/drafts/${draftId}/transition`, 'POST', {
    expected_version: expectedVersion,
    status,
  })
}

export function reconcilePODraft(draftId: string) {
  return planningMutation(`/api/po/drafts/${draftId}/reconcile`, 'POST')
}

export function previewPODraft(draftId: string) {
  return planningMutation(`/api/po/drafts/${draftId}/preview`, 'POST')
}

// Deletes a draft.
export async function deletePODraft(draftId: string) {
  const baseUrl = '/backend'
  const res = await fetch(`${baseUrl}/api/po/draft/${draftId}`, { method: 'DELETE' })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to delete PO draft')
  }
  return res.json()
}

export function useConnectionStatus() {
  type ConnectionState = 'checking' | 'connected' | 'disconnected'

  const [lsStatus, setLsStatus] = useState<ConnectionState>('checking')
  const [bqStatus, setBqStatus] = useState<ConnectionState>('checking')
  const [shopifyStatus, setShopifyStatus] = useState<ConnectionState>('checking')

  useEffect(() => {
    const checkHealth = () => {
      const baseUrl = '/backend'

      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 8000) // 8s timeout

      const fetchWithTimeout = (endpoint: string): Promise<ConnectionState> => {
        return fetch(`${baseUrl}/api/health/${endpoint}`, { signal: controller.signal })
          .then(res => res.ok ? 'connected' : 'disconnected')
          .catch(err => {
            console.error(`[HealthCheck] ${endpoint} failed:`, err)
            return 'disconnected'
          })
      }
      
      activeController = controller
      activeTimeout = timeoutId

      // When all three settle, cancel the 8s abort timer so it doesn't linger.
      Promise.allSettled([
        fetchWithTimeout('lightspeed').then(setLsStatus),
        fetchWithTimeout('bigquery').then(setBqStatus),
        fetchWithTimeout('shopify').then(setShopifyStatus),
      ]).finally(() => clearTimeout(timeoutId))
    }

    // Track the in-flight cycle so unmount can abort it and clear its timer.
    let activeController: AbortController | null = null
    let activeTimeout: ReturnType<typeof setTimeout> | null = null

    checkHealth()
    // The backend caches each probe for ~2 min, so polling faster than this only
    // costs proxy round-trips and re-serves the same answer.
    const interval = setInterval(checkHealth, 60000)
    return () => {
      clearInterval(interval)
      if (activeTimeout) clearTimeout(activeTimeout)
      activeController?.abort()
    }
  }, [])

  return { lsStatus, bqStatus, shopifyStatus }
}

// Live special-order dashboard data (open SOs + derived overdue/aging + summary).
// Mirrors the live PO-draft hooks above. `refetch()` forces a server-side re-fetch
// from Lightspeed (bypasses the backend TTL cache).
/** Park a special order until a check-back date. Both a reason code and a date are required —
 *  the backend rejects an open-ended dismissal, because an un-categorised snooze is exactly how
 *  an order gets parked rather than worked. */
export async function ackSpecialOrder(
  specialOrderId: string,
  input: {
    // Omit for the reason-coded park (the server default). 'in_progress' and 'done' are the
    // one-click Start/Done actions and need no reason and no check-back date.
    work_status?: import('./types').SoWorkStatus
    reason_code?: string
    note?: string
    checkback_days?: number
    checkback_date?: string
  },
) {
  const res = await fetch(`/backend/api/special-orders/${specialOrderId}/ack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || 'Failed to acknowledge special order')
  }
  return res.json()
}

/** Clear the human decision on a special order, returning it to the active queue now.
 *  One undo for all three statuses: un-park, un-claim (Start), and reopen (Done). */
export async function unackSpecialOrder(specialOrderId: string) {
  const res = await fetch(`/backend/api/special-orders/${specialOrderId}/ack`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(err?.detail || 'Failed to un-acknowledge special order')
  }
  return res.json()
}

/** The PO recommendation for one special order. Lazy: pass null until the panel is opened.
 *  The backend caches its shared lookups for 5 minutes, so the first call in a window is slow
 *  and the rest are effectively free. */
export function useSoRecommendation(specialOrderId: string | null) {
  const { data, error, isLoading } = useSWR<import('./types').PoRecommendation>(
    specialOrderId ? `/backend/api/special-orders/${specialOrderId}/po-recommendation` : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300000 },
  )
  return { recommendation: data, isLoading, error }
}

/** Lazy operational timeline for the selected drawer row. */
export function useSoActivity(specialOrderId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<{
    special_order_id: string
    activity: import('./types').SpecialOrderActivityEvent[]
  }>(
    specialOrderId ? `/backend/api/special-orders/${specialOrderId}/activity` : null,
    fetcher,
    { revalidateOnFocus: true, dedupingInterval: 30000 },
  )
  return {
    activity: data?.activity ?? [],
    isLoading,
    error,
    revalidate: mutate,
  }
}

/** Scoreboard metrics. Lazy — pass false until the panel is opened, since the history half
 *  costs a BigQuery round-trip. */
export function useSoScoreboard(enabled: boolean) {
  const { data, error, isLoading } = useSWR<import('./types').SoScoreboard>(
    enabled ? '/backend/api/special-orders/scoreboard' : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300000 },
  )
  return { scoreboard: data, isLoading, error }
}

export function useSpecialOrders({ liveOnly = true }: { liveOnly?: boolean } = {}) {
  const baseUrl = '/backend'
  // The escalations endpoint is a strict superset of /api/special-orders: the same rows plus
  // each one's SLA verdict, with acknowledgements merged fresh per request (they must not be
  // served from the 5-minute dashboard cache, or an ack would appear to do nothing).
  // `live_only_days=0` is meaningful: it asks the server for the historical close-out backlog.
  // Keeping it in the SWR key prevents the old bug where the client unchecked "Live SOs" but
  // could only filter the already-truncated 365-day response it had received.
  const liveOnlyDays = liveOnly ? 365 : 0
  const url = `${baseUrl}/api/special-orders/escalations?live_only_days=${liveOnlyDays}`
  const { data, error, mutate, isLoading } = useSWR<import('./types').SpecialOrderWorklistResponse>(
    url,
    fetcher,
    {
      ...adminDashboardSWRConfig,
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
      revalidateIfStale: true,
      refreshInterval: 180000,
      dedupingInterval: 30000,
    },
  )

  const [isRefreshing, setIsRefreshing] = useState(false)

  // Force a server-side re-fetch from Lightspeed (bypasses the backend TTL cache).
  // Awaits the result before writing it so the caller can show progress and catch
  // failures, rather than firing a promise into mutate and hoping (the live walk
  // can be slow or fail, and a fire-and-forget mutate swallows both).
  const refetch = async () => {
    setIsRefreshing(true)
    try {
      const fresh = await fetcher(`${url}&refresh=true`)
      await mutate(fresh, { revalidate: false })
    } finally {
      setIsRefreshing(false)
    }
  }

  // Plain re-GET (serves the backend's cache). Enough after ETA/match writes, since the
  // backend rebuilds its cached payload as part of the POST — no Lightspeed re-walk needed.
  const revalidate = async () => {
    const fresh = await fetcher(url)
    await mutate(fresh, { revalidate: false })
  }

  return {
    orders: data?.orders ?? [],
    summary: data?.summary,
    sla: data?.summary,
    reasonCodes: data?.reason_codes ?? [],
    shopifyOnly: data?.shopify_only ?? [],
    fetchedAt: data?.fetched_at,
    meta: data?.meta,
    sourceHealth: data?.meta?.sources,
    isLoading,
    isRefreshing,
    error,
    refetch,
    revalidate,
  }
}
