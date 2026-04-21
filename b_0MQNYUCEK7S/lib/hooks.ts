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

// Hook for recommendation runs
export function useRecommendationRuns() {
  const [data, setData] = useState<RecommendationRun[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true)
      try {
        await simulateDelay(400)
        setData(generateMockRecommendationRuns())
      } catch (err) {
        setError(err as Error)
      } finally {
        setIsLoading(false)
      }
    }
    fetchData()
  }, [])

  return { data, isLoading, error }
}

// Hook for writeback audit
export function useWritebackAudit() {
  const [data, setData] = useState<WritebackAuditEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true)
      try {
        await simulateDelay(400)
        setData(generateMockWritebackAudit())
      } catch (err) {
        setError(err as Error)
      } finally {
        setIsLoading(false)
      }
    }
    fetchData()
  }, [])

  return { data, isLoading, error }
}

// Hook for managed SKUs
export function useManagedSkus() {
  const [data, setData] = useState<ManagedSku[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true)
      try {
        await simulateDelay(400)
        setData(generateMockManagedSkus())
      } catch (err) {
        setError(err as Error)
      } finally {
        setIsLoading(false)
      }
    }
    fetchData()
  }, [])

  return { data, isLoading, error }
}

// Placeholder API functions for future backend integration
export async function pushToLightspeed(rowIds: string[]): Promise<{ success: boolean; message: string }> {
  await simulateDelay(1000)
  // Simulate 95% success rate
  if (Math.random() < 0.95) {
    return { success: true, message: `Successfully pushed ${rowIds.length} row(s) to Lightspeed` }
  }
  return { success: false, message: 'Lightspeed API error - please try again' }
}

export async function lockRows(rowIds: string[]): Promise<{ success: boolean }> {
  await simulateDelay(300)
  return { success: true }
}

export async function unlockRows(rowIds: string[]): Promise<{ success: boolean }> {
  await simulateDelay(300)
  return { success: true }
}

export async function updateOverride(
  rowId: string,
  reorderPoint?: number,
  desiredLevel?: number
): Promise<{ success: boolean }> {
  await simulateDelay(300)
  return { success: true }
}
