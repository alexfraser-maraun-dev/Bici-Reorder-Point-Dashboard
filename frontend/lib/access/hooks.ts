'use client'

// Access state for the app shell. One SWR key for the whole app, so the nav, the
// tab strips, and every route guard read the same cached answer rather than each
// asking the backend.

import useSWR from 'swr'
import type {
  AccessState, FeatureDefinition, FeatureKey, UserAccessRecord,
} from './types'

const ACCESS_URL = '/backend/api/admin/access'

const fetcher = async (url: string) => {
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to load access settings')
  return res.json()
}

// Access rarely changes and gates every render, so cache it hard for the session
// and revalidate only when something explicitly mutates it (the Admin page).
const accessSWRConfig = {
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
  revalidateIfStale: false,
  dedupingInterval: 600000, // 10 min
}

export function useAccess() {
  const { data, error, isLoading, mutate } = useSWR(ACCESS_URL, fetcher, accessSWRConfig)
  const access: AccessState | undefined = data?.data

  // Until the answer arrives, treat everything as visible. The nav renders from
  // this, and briefly showing a tab the user has is far better than flashing an
  // empty shell at every user on every page load.
  const isEnabled = (key: FeatureKey): boolean =>
    access ? access.features[key] !== false : true

  return {
    access,
    isEnabled,
    isAdmin: access?.is_admin ?? false,
    bootstrapMode: access?.bootstrap_mode ?? false,
    // The access call itself failed (backend down, not deployed, 401). Callers
    // use this to avoid hiding things merely because the answer never arrived.
    accessUnavailable: Boolean(error) && !access,
    defaultOrderingTab: access?.default_ordering_tab ?? 'ordering.po_tracker',
    isLoading,
    error,
    mutate,
  }
}

export function useAdminFeatures() {
  const { data, error, isLoading, mutate } = useSWR(
    '/backend/api/admin/features', fetcher, { revalidateOnFocus: false }
  )
  return {
    features: (data?.data?.features ?? []) as FeatureDefinition[],
    isLoading,
    error,
    mutate,
  }
}

export function useAdminUsers() {
  const { data, error, isLoading, mutate } = useSWR(
    '/backend/api/admin/users', fetcher, { revalidateOnFocus: false }
  )
  return {
    users: (data?.data?.users ?? []) as UserAccessRecord[],
    bootstrapMode: Boolean(data?.data?.bootstrap_mode),
    isLoading,
    error,
    mutate,
  }
}

async function send(path: string, method: string, body?: unknown) {
  const res = await fetch(`/backend${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(payload?.detail || `Request failed (${res.status})`)
  return payload
}

/** Partial update. `null` for a key clears the override and restores the default. */
export function updateFeatures(features: Record<string, boolean | null>) {
  return send('/api/admin/features', 'PUT', { features })
}

export function saveUserAccess(user: {
  email: string
  role: string
  overrides: Record<string, boolean>
}) {
  return send('/api/admin/users', 'PUT', user)
}

export function deleteUserAccess(email: string) {
  return send(`/api/admin/users/${encodeURIComponent(email)}`, 'DELETE')
}
