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

export function useReplenishmentData(
  forecastPeriod: number,
  safetyDays: number,
  growthMultiplier: number,
  recent30dWeight: number,
  adjustmentMode: string
) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const url = `${baseUrl}/api/replenishment/data?forecast_period=${forecastPeriod}&safety_days=${safetyDays}&growth_multiplier=${growthMultiplier}&recent_30d_weight=${recent30dWeight}&adjustment_mode=${adjustmentMode}`
  
  const { data, error, mutate, isLoading } = useSWR(url, fetcher, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    dedupingInterval: 600000, // Keep in cache for 10 minutes
  })

  const [isRefetching, setIsRefetching] = useState(false)

  const handleRefetch = async () => {
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

// Hook for vendor lead times
export function useVendorLeadTimes() {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  const { data, error, mutate, isLoading } = useSWR(`${baseUrl}/api/replenishment/vendor-lead-times`, fetcher)
  return { data: data || null, isLoading, error, refetch: mutate }
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
