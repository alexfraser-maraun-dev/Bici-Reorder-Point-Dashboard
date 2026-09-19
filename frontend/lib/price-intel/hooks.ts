'use client'

// SWR hooks for the Price Intelligence page. Kept in their own module so
// lib/hooks.ts (shared by the existing dashboard) is untouched.

import useSWR from 'swr'
import type {
  AdminSetting,
  ChangeEvent,
  ChangeFeedFilters,
  ChangeGroup,
  ChangeGroupFilters,
  Competitor,
  CompetitorCrawlSettings,
  Digest,
  ItemCompetitorPrice,
  ItemObservation,
  ItemPriceHistory,
  ItemSearchResult,
  MatrixCoverage,
  MatrixPushPreview,
  MatrixPushResult,
  PriceIntelSummary,
  PricePushPreview,
  ProductLink,
  ScrapeRun,
  ScrapeStatus,
  StockHistory,
  StockIntelligenceRow,
  TrackedMatrix,
  TrackedProduct,
  TrackedUrl,
} from './types'

const baseUrl = () => '/backend'

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

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    const message = typeof detail === 'string'
      ? detail
      : (detail as { message?: string } | null)?.message ?? `Request failed (${status})`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export async function apiPost(path: string, body?: unknown, method: string = 'POST') {
  const res = await fetch(`${baseUrl()}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(res.status, payload?.detail)
  }
  return payload
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`)
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(res.status, payload?.detail)
  }
  return payload as T
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

// Matrix-grain rollup rows for the tracked table's collapsed matrix parents.
export function useTrackedMatrices() {
  const { data, error, isLoading, mutate } = useSWR<TrackedMatrix[]>(
    `${baseUrl()}/api/price-intel/tracked/matrices`, fetcher, swrConfig
  )
  return { matrices: data ?? [], error, isLoading, mutate }
}

// Lazy: per-competitor coverage for one matrix; pass null until the matrix row
// is expanded so only open matrices query.
export function useMatrixCoverage(matrixId: string | null) {
  const { data, error, isLoading } = useSWR<MatrixCoverage[]>(
    matrixId
      ? `${baseUrl()}/api/price-intel/tracked/matrices/${encodeURIComponent(matrixId)}/coverage`
      : null,
    fetcher, swrConfig
  )
  return { coverage: data ?? [], error, isLoading }
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

// Price drops/increases grouped per product × store (see ChangeGroup). The
// query reads up to 3,000 events, so callers pass enabled=false until the
// grouped view is actually shown.
export function useChangeGroups(filters: ChangeGroupFilters = {}, enabled = true) {
  const params = new URLSearchParams({ days: String(filters.days ?? 14) })
  if (filters.unacknowledgedOnly) params.set('acknowledged', 'false')
  if (filters.competitorId) params.set('competitor_id', filters.competitorId)
  if (filters.minPct != null && filters.minPct > 0) params.set('min_pct', String(filters.minPct))
  if (filters.brand) params.set('brand', filters.brand)
  if (filters.direction && filters.direction !== 'both') params.set('direction', filters.direction)
  if (filters.sort && filters.sort !== 'recent') params.set('sort', filters.sort)
  const { data, error, isLoading, mutate } = useSWR<{
    groups: ChangeGroup[]; event_count: number; truncated: boolean
  }>(enabled ? `${baseUrl()}/api/price-intel/changes/grouped?${params}` : null,
     fetcher, swrConfig)
  return {
    groups: data?.groups ?? [],
    eventCount: data?.event_count ?? 0,
    truncated: data?.truncated ?? false,
    error, isLoading, mutate,
  }
}

// Partial settings_json change for one store, merged on the server — null
// deletes a key. Used by switches that flip a single flag, so two quick
// toggles can't race each other on a stale copy of the row.
export function patchCompetitorSettings(
  competitorId: string,
  patch: Partial<Record<keyof CompetitorCrawlSettings, unknown>>,
): Promise<{ status: string; competitor: Competitor }> {
  return apiPost(
    `/api/price-intel/competitors/${encodeURIComponent(competitorId)}/settings`,
    { patch },
    'PATCH',
  )
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
  const { data, error, isLoading, mutate } = useSWR<ItemCompetitorPrice[]>(
    itemId
      ? `${baseUrl()}/api/price-intel/tracked/${encodeURIComponent(itemId)}/competitors`
      : null,
    fetcher, swrConfig
  )
  return { prices: data ?? [], error, isLoading, mutate }
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

// Lazy: change-point-compressed price history (our line + one per competitor),
// including one baseline point per series before the window so the chart can
// forward-fill from the left edge. Keyed on itemId so only expanded rows fetch;
// inherits the 5-min SWR dedup.
export function useItemPriceHistory(itemId: string | null, days: number = 60) {
  const { data, error, isLoading } = useSWR<ItemPriceHistory>(
    itemId
      ? `${baseUrl()}/api/price-intel/tracked/${encodeURIComponent(itemId)}/price-history?days=${days}`
      : null,
    fetcher, swrConfig
  )
  return { history: data, error, isLoading }
}

// Mounted only while the Stock tab is active, so its historical BigQuery
// aggregation never delays the normal Price Intelligence page.
export function useStockIntelligence() {
  const { data, error, isLoading, mutate } = useSWR<StockIntelligenceRow[]>(
    `${baseUrl()}/api/price-intel/stock`, fetcher, swrConfig
  )
  return { rows: data ?? [], error, isLoading, mutate }
}

// Lazy per-row history: pass null until the row is expanded.
export function useStockHistory(
  itemId: string | null,
  competitorKey: string | null,
  days: number = 90,
) {
  const { data, error, isLoading } = useSWR<StockHistory>(
    itemId && competitorKey
      ? `${baseUrl()}/api/price-intel/stock/history?item_id=${encodeURIComponent(itemId)}`
        + `&competitor_key=${encodeURIComponent(competitorKey)}&days=${days}`
      : null,
    fetcher,
    swrConfig,
  )
  return { history: data, error, isLoading }
}

export function usePriceIntelSettings() {
  const { data, error, isLoading, mutate } = useSWR<{ settings: AdminSetting[] }>(
    `${baseUrl()}/api/price-intel/settings`, fetcher, swrConfig
  )
  return { settings: data?.settings ?? [], error, isLoading, mutate }
}

// value null clears the override (revert to the env-var default).
export async function updatePriceIntelSettings(
  changes: Record<string, string | number | boolean | null>
): Promise<{ settings: AdminSetting[] }> {
  return apiPost('/api/price-intel/settings', { changes }, 'PUT')
}

const itemSearchCache = new Map<string, { at: number; results: ItemSearchResult[] }>()
const ITEM_SEARCH_CACHE_MS = 5 * 60 * 1000
const ITEM_SEARCH_CACHE_MAX = 100

export function clearItemSearchCache() {
  itemSearchCache.clear()
}

export async function searchItems(q: string): Promise<ItemSearchResult[]> {
  const key = q.trim().toLowerCase()
  const cached = itemSearchCache.get(key)
  if (cached && Date.now() - cached.at < ITEM_SEARCH_CACHE_MS) {
    itemSearchCache.delete(key)
    itemSearchCache.set(key, cached)
    return cached.results
  }
  if (cached) itemSearchCache.delete(key)

  const res = await fetch(`${baseUrl()}/api/price-intel/items/search?q=${encodeURIComponent(q)}`)
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, payload?.detail)
  const results = payload as ItemSearchResult[]
  itemSearchCache.set(key, { at: Date.now(), results })
  while (itemSearchCache.size > ITEM_SEARCH_CACHE_MAX) {
    const oldest = itemSearchCache.keys().next().value
    if (oldest === undefined) break
    itemSearchCache.delete(oldest)
  }
  return results
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

export async function previewMatrixPush(
  itemId: string, newPrice: number
): Promise<MatrixPushPreview> {
  return apiPost('/api/price-intel/push-price/matrix/preview', {
    item_id: itemId,
    new_price: newPrice,
  })
}

// expectedItemIds is the exact variant list the user reviewed in the preview —
// the server rejects the push if Lightspeed's live list no longer matches it.
export async function executeMatrixPush(
  itemId: string, newPrice: number, overrideFloor: boolean, expectedItemIds: string[]
): Promise<MatrixPushResult> {
  return apiPost('/api/price-intel/push-price/matrix', {
    item_id: itemId,
    new_price: newPrice,
    confirm: true,
    override_floor: overrideFloor,
    expected_item_ids: expectedItemIds,
  })
}
