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
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
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
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/replenishment/runs`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

// Hook for writeback audit (Audit Logs)
export function useWritebackAudit() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/replenishment/logs`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

// Hook for managed SKUs
export function useManagedSkus() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/skus`, fetcher)
  return { data: data || [], isLoading, error, refetch: mutate }
}

export function useActiveVendorLeadTimes() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = `${baseUrl}/api/replenishment/active-vendor-lead-times`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  const refetch = () => mutate(fetcher(`${url}?force_refresh=true`), false)
  return { data: data || null, isLoading, error, refetch }
}

export function useBrandSourcingRules() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = `${baseUrl}/api/replenishment/brand-sourcing-rules`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  const refetch = () => mutate(fetcher(`${url}?force_refresh=true`), false)
  return { data: data || null, isLoading, error, refetch }
}

// Demand & Seasonality: category seasonal profiles for the visualization layer.
export function useSeasonalProfiles(location?: string | number | null) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const params = new URLSearchParams()
  if (location !== undefined && location !== null && location !== '') {
    params.set('location', String(location))
  }
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
) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  let url: string | null = null
  if (id) {
    const params = new URLSearchParams({ scope, id: String(id) })
    if (location !== undefined && location !== null && location !== '') {
      params.set('location', String(location))
    }
    url = `${baseUrl}/api/forecast/history?${params.toString()}`
  }
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data || null, isLoading, error, refetch: () => mutate() }
}

// Demand & Seasonality: forward weeks-of-cover heatmap (soonest stockouts first).
export function useCoverage(location?: string | null, limit: number = 150) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
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
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
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

// ---------------------------------------------------------------------------
// Purchase Orders
// ---------------------------------------------------------------------------

// Lists PO drafts (optionally filtered by status).
export function usePODrafts(status?: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = `${baseUrl}/api/po/drafts${status ? `?status=${status}` : ''}`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data?.data || [], isLoading, error, refetch: mutate }
}

// Fetches a single draft with its line items.
export function usePODraft(draftId: string | null) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = draftId ? `${baseUrl}/api/po/draft/${draftId}` : null
  const { data, error, mutate, isLoading } = useSWR(url, fetcher)
  return { data: data?.data || null, isLoading, error, refetch: mutate }
}

// Reports whether the Lightspeed token can access purchase orders
// (i.e. was re-authorized with the employee:purchase_orders scope).
export function useLightspeedPoAccess() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
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

// Lists open (unsent) Lightspeed POs.
export function useOpenOrders(vendorId?: string, shopId?: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const params = new URLSearchParams()
  if (vendorId) params.set('vendor_id', vendorId)
  if (shopId) params.set('shop_id', shopId)
  const qs = params.toString()
  const url = `${baseUrl}/api/po/open-orders${qs ? `?${qs}` : ''}`
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, adminDashboardSWRConfig)
  return { data: data?.data || [], isLoading, error, refetch: mutate }
}

// Builds drafts from selected recommendation rows (raw backend rec dicts).
export async function createPODraft(recommendations: any[], createdBy?: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const res = await fetch(`${baseUrl}/api/po/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recommendations, created_by: createdBy }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to create PO draft')
  }
  return res.json()
}

// Pushes a draft to Lightspeed.
export async function pushPODraft(draftId: string, pushedBy?: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const res = await fetch(`${baseUrl}/api/po/push/${draftId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pushed_by: pushedBy }),
  })
  if (!res.ok) {
    const errorData = await res.json().catch(() => null)
    throw new Error(errorData?.detail || 'Failed to push PO draft')
  }
  return res.json()
}

// Deletes a draft.
export async function deletePODraft(draftId: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
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

  useEffect(() => {
    const checkHealth = () => {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
      console.log(`[HealthCheck] Pinging backend at: ${baseUrl}`)

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
      
      fetchWithTimeout('lightspeed').then(setLsStatus)
      fetchWithTimeout('bigquery').then(setBqStatus)

      return () => clearTimeout(timeoutId)
    }

    checkHealth()
    const interval = setInterval(checkHealth, 30000)
    return () => clearInterval(interval)
  }, [])

  return { lsStatus, bqStatus }
}

// Live special-order dashboard data (open SOs + derived overdue/aging + summary).
// Mirrors the live PO-draft hooks above. `refetch()` forces a server-side re-fetch
// from Lightspeed (bypasses the backend TTL cache).
export function useSpecialOrders() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = `${baseUrl}/api/special-orders`
  const { data, error, mutate, isLoading } = useSWR<import('./types').SpecialOrderDashboard>(
    url,
    fetcher,
    adminDashboardSWRConfig
  )
  return {
    orders: data?.orders ?? [],
    summary: data?.summary,
    shopifyOnly: data?.shopify_only ?? [],
    fetchedAt: data?.fetched_at,
    isLoading,
    error,
    refetch: () => mutate(fetcher(`${url}?refresh=true`), false),
  }
}
