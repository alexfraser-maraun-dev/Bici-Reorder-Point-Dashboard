'use client'

// SWR hooks for the Price Intelligence page. Kept in their own module so
// lib/hooks.ts (shared by the existing dashboard) is untouched.

import useSWR from 'swr'
import type {
  ChangeEvent,
  ChangeFeedFilters,
  Competitor,
  Digest,
  ItemCompetitorPrice,
  ItemObservation,
  ItemPriceHistory,
  ItemSearchResult,
  PriceIntelSummary,
  PricePushPreview,
  ProductLink,
  ScrapeRun,
  ScrapeStatus,
  TrackedProduct,
  TrackedUrl,
} from './types'

const baseUrl = () => process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

const fetcher = async (url: string) => {
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to fetch data')
  return res.json()
}

const swrConfig = {
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
  dedupingInterval: 300000, // 5 min; scrape completion mutates explicitly
}

export async function apiPost(path: string, body?: unknown, method: string = 'POST') {
  const res = await fetch(`${baseUrl()}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(payload?.detail || `Request failed (${res.status})`)
  }
  return payload
}

export function usePriceIntelSummary() {
  const { data, error, isLoading, mutate } = useSWR<PriceIntelSummary>(
    `${baseUrl()}/api/price-intel/summary`, fetcher, swrConfig
  )
  return { summary: data, error, isLoading, mutate }
}

export function useTrackedProducts() {
  const { data, error, isLoading, mutate } = useSWR<TrackedProduct[]>(
    `${baseUrl()}/api/price-intel/tracked`, fetcher, swrConfig
  )
  return { products: data ?? [], error, isLoading, mutate }
}

export function useCompetitors() {
  const { data, error, isLoading, mutate } = useSWR<Competitor[]>(
    `${baseUrl()}/api/price-intel/competitors`, fetcher, swrConfig
  )
  return { competitors: data ?? [], error, isLoading, mutate }
}

export function useTrackedUrls() {
  const { data, error, isLoading, mutate } = useSWR<TrackedUrl[]>(
    `${baseUrl()}/api/price-intel/urls`, fetcher, swrConfig
  )
  return { urls: data ?? [], error, isLoading, mutate }
}

export function useChangeFeed(filters: ChangeFeedFilters = {}) {
  const params = new URLSearchParams({ days: String(filters.days ?? 14) })
  if (filters.unacknowledgedOnly) params.set('acknowledged', 'false')
  if (filters.competitorId) params.set('competitor_id', filters.competitorId)
  if (filters.eventTypes?.length) params.set('event_type', filters.eventTypes.join(','))
  if (filters.minPct != null && filters.minPct > 0) params.set('min_pct', String(filters.minPct))
  if (filters.brand) params.set('brand', filters.brand)
  const { data, error, isLoading, mutate } = useSWR<ChangeEvent[]>(
    `${baseUrl()}/api/price-intel/changes?${params}`, fetcher, swrConfig
  )
  return { events: data ?? [], error, isLoading, mutate }
}

export function useProductLinks(status: string | null = 'pending') {
  const params = status ? `?status=${encodeURIComponent(status)}` : ''
  const { data, error, isLoading, mutate } = useSWR<ProductLink[]>(
    `${baseUrl()}/api/price-intel/links${params}`, fetcher, swrConfig
  )
  return { links: data ?? [], error, isLoading, mutate }
}

// Lazy: pass null until the row is expanded so only open rows query.
export function useItemCompetitorPrices(itemId: string | null) {
  const { data, error, isLoading } = useSWR<ItemCompetitorPrice[]>(
    itemId
      ? `${baseUrl()}/api/price-intel/tracked/${encodeURIComponent(itemId)}/competitors`
      : null,
    fetcher, swrConfig
  )
  return { prices: data ?? [], error, isLoading }
}

// Polls while a run is active so the "Scrape now" button shows live progress.
export function useScrapeStatus(active: boolean) {
  const { data, mutate } = useSWR<ScrapeStatus>(
    `${baseUrl()}/api/price-intel/scrape/status`, fetcher,
    { refreshInterval: active ? 4000 : 0, revalidateOnFocus: false }
  )
  return { status: data, mutate }
}

export function useScrapeRuns() {
  const { data, error, isLoading, mutate } = useSWR<ScrapeRun[]>(
    `${baseUrl()}/api/price-intel/runs`, fetcher, swrConfig
  )
  return { runs: data ?? [], error, isLoading, mutate }
}

export function useLatestDigest() {
  const { data, error, isLoading, mutate } = useSWR<Digest>(
    `${baseUrl()}/api/price-intel/digest/latest`, fetcher, swrConfig
  )
  return { digest: data, error, isLoading, mutate }
}

export function useItemObservations(itemId: string | null) {
  const { data, error, isLoading } = useSWR<ItemObservation[]>(
    itemId ? `${baseUrl()}/api/price-intel/observations?item_id=${encodeURIComponent(itemId)}` : null,
    fetcher, swrConfig
  )
  return { observations: data ?? [], error, isLoading }
}

// Lazy: change-point-compressed price history (our line + one per competitor).
// Keyed on itemId so only expanded rows fetch; inherits the 5-min SWR dedup.
export function useItemPriceHistory(itemId: string | null) {
  const { data, error, isLoading } = useSWR<ItemPriceHistory>(
    itemId
      ? `${baseUrl()}/api/price-intel/tracked/${encodeURIComponent(itemId)}/price-history`
      : null,
    fetcher, swrConfig
  )
  return { history: data, error, isLoading }
}

export async function searchItems(q: string): Promise<ItemSearchResult[]> {
  const res = await fetch(`${baseUrl()}/api/price-intel/items/search?q=${encodeURIComponent(q)}`)
  if (!res.ok) return []
  return res.json()
}

export async function previewPricePush(itemId: string, newPrice: number): Promise<PricePushPreview> {
  return apiPost('/api/price-intel/push-price/preview', { item_id: itemId, new_price: newPrice })
}

export async function executePricePush(
  itemId: string, newPrice: number, overrideFloor: boolean
): Promise<PricePushPreview & { status: string }> {
  return apiPost('/api/price-intel/push-price', {
    item_id: itemId,
    new_price: newPrice,
    confirm: true,
    override_floor: overrideFloor,
  })
}
