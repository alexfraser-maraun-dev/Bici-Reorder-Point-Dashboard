'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import type {
  SkuLocationRow,
  RecommendationRun,
  WritebackAuditEntry,
  ManagedSku,
  FilterState,
  KpiSummary,
} from './types'
import {
  generateMockSkuData,
  generateMockRecommendationRuns,
  generateMockWritebackAudit,
  generateMockManagedSkus,
} from './mock-data'

// Simulated API delay
const simulateDelay = (ms: number = 500) => new Promise(resolve => setTimeout(resolve, ms))

// Hook for SKU data with filtering
export function useSkuData(filters: FilterState) {
  const [data, setData] = useState<SkuLocationRow[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const refetch = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      await simulateDelay(300)
      const mockData = generateMockSkuData(150)
      setData(mockData)
    } catch (err) {
      setError(err as Error)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
  }, [refetch])

  // Apply filters
  const filteredData = useMemo(() => {
    return data.filter(row => {
      // Search filter
      if (filters.search) {
        const searchLower = filters.search.toLowerCase()
        if (
          !row.sku.toLowerCase().includes(searchLower) &&
          !row.product.toLowerCase().includes(searchLower)
        ) {
          return false
        }
      }

      // Location filter
      if (filters.locations.length > 0 && !filters.locations.includes(row.location)) {
        return false
      }

      // Vendor filter
      if (filters.vendors.length > 0 && !filters.vendors.includes(row.vendor)) {
        return false
      }

      // Brand filter
      if (filters.brands.length > 0 && !filters.brands.includes(row.brand)) {
        return false
      }

      // Category filter
      if (filters.categories.length > 0 && !filters.categories.includes(row.category)) {
        return false
      }

      // Boolean filters
      if (filters.needsOrderOnly && !row.needsOrder) return false
      if (filters.changedOnly && !row.changed) return false
      if (filters.lockedOnly && !row.locked) return false
      if (filters.overriddenOnly && !row.override) return false
      if (filters.writebackFailedOnly && row.writebackStatus !== 'failed') return false

      return true
    })
  }, [data, filters])

  return { data: filteredData, allData: data, isLoading, error, refetch }
}

// Hook for KPI summary
export function useKpiSummary(data: SkuLocationRow[]): KpiSummary {
  return useMemo(() => {
    return {
      totalManagedRows: data.length,
      needsOrder: data.filter(r => r.needsOrder).length,
      changedRows: data.filter(r => r.changed).length,
      lockedRows: data.filter(r => r.locked).length,
      overrides: data.filter(r => r.override).length,
      readyToPush: data.filter(r => r.changed && !r.locked && r.writebackStatus !== 'pending').length,
      failedWritebacks: data.filter(r => r.writebackStatus === 'failed').length,
    }
  }, [data])
}

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
  updated_by?: string
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

// Manually links / unlinks an LS special order and a Shopify order (persisted override;
// unlink also forbids auto-matching from re-proposing the pair). The backend rebuilds its
// dashboard cache on success — follow with a plain revalidate.
async function postSoMatchOverride(
  path: 'match' | 'unmatch',
  input: { special_order_id: string; shopify_order_id: string; updated_by?: string }
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

export const matchSpecialOrder = (input: { special_order_id: string; shopify_order_id: string; updated_by?: string }) =>
  postSoMatchOverride('match', input)

export const unmatchSpecialOrder = (input: { special_order_id: string; shopify_order_id: string; updated_by?: string }) =>
  postSoMatchOverride('unmatch', input)

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
    const interval = setInterval(checkHealth, 30000)
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
  input: { reason_code: string; note?: string; checkback_days?: number; checkback_date?: string },
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

/** Return a parked special order to the active queue immediately. */
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

/** Open POs at one store, for overriding a recommendation. Lazy on shop id. */
export function useCandidatePos(shopId: string | null) {
  const { data, error, isLoading } = useSWR<{ orders: import('./types').CandidatePo[] }>(
    shopId ? `/backend/api/special-orders/candidate-pos?shop_id=${shopId}` : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 300000 },
  )
  return { candidates: data?.orders ?? [], isLoading, error }
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

export function useSpecialOrders() {
  const baseUrl = '/backend'
  // The escalations endpoint is a strict superset of /api/special-orders: the same rows plus
  // each one's SLA verdict, with acknowledgements merged fresh per request (they must not be
  // served from the 5-minute dashboard cache, or an ack would appear to do nothing).
  const url = `${baseUrl}/api/special-orders/escalations`
  const { data, error, mutate, isLoading } = useSWR<import('./types').SpecialOrderDashboard>(
    url,
    fetcher,
    adminDashboardSWRConfig
  )

  const [isRefreshing, setIsRefreshing] = useState(false)

  // Force a server-side re-fetch from Lightspeed (bypasses the backend TTL cache).
  // Awaits the result before writing it so the caller can show progress and catch
  // failures, rather than firing a promise into mutate and hoping (the live walk
  // can be slow or fail, and a fire-and-forget mutate swallows both).
  const refetch = async () => {
    setIsRefreshing(true)
    try {
      const fresh = await fetcher(`${url}?refresh=true`)
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
    sla: data?.summary as unknown as import('./types').SpecialOrderSummarySla | undefined,
    thresholds: (data as { meta?: { thresholds?: import('./special-order-triage').TriageThresholds } } | undefined)
      ?.meta?.thresholds,
    reasonCodes: data?.reason_codes ?? [],
    shopifyOnly: data?.shopify_only ?? [],
    fetchedAt: data?.fetched_at,
    isLoading,
    isRefreshing,
    error,
    refetch,
    revalidate,
  }
}
