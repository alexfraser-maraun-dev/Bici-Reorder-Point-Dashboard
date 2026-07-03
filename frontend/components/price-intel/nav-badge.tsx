'use client'

import { usePriceIntelSummary } from '@/lib/price-intel/hooks'

// Unread-changes count chip for the "Price Intel" nav entry. Shares the SWR
// cache with the page itself, so it costs at most one summary request per
// deduping window.
export function PriceIntelNavBadge() {
  const { summary } = usePriceIntelSummary()
  const count = summary?.unacknowledged_changes ?? 0
  if (count <= 0) return null
  return (
    <span className="ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-none text-white">
      {count > 99 ? '99+' : count}
    </span>
  )
}
