'use client'

// Route-level guard. A page whose feature is switched off never renders its
// content component, so none of that page's hooks mount and none of its
// endpoints are called — the feature really is dormant, not just hidden.
//
// The backend enforces the same rule on its own endpoints (see
// services/access/service.feature_for_path), so this is a UX layer, not the
// security boundary.

import Link from 'next/link'
import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAccess } from '@/lib/access/hooks'
import type { FeatureKey } from '@/lib/access/types'

// Where "take me somewhere that works" goes, in preference order.
const HOME_CANDIDATES: { feature: FeatureKey; href: string; label: string }[] = [
  { feature: 'ordering', href: '/', label: 'Ordering' },
  { feature: 'special_orders', href: '/special-orders', label: 'Special Orders' },
  { feature: 'price_intel', href: '/price-intelligence', label: 'Price Intel' },
  { feature: 'admin', href: '/admin', label: 'Admin' },
]

export function FeatureGate({
  feature,
  children,
}: {
  feature: FeatureKey
  children: React.ReactNode
}) {
  const { isEnabled, isLoading, access } = useAccess()

  // Wait for the real answer before rendering a page's data-fetching content —
  // an optimistic render here would fire exactly the requests being gated.
  if (isLoading && !access) {
    return <div className="p-10 text-sm text-muted-foreground">Loading…</div>
  }

  if (!isEnabled(feature)) {
    const home = HOME_CANDIDATES.find((c) => c.feature !== feature && isEnabled(c.feature))
    // 'admin' is gated on the caller's role rather than a switch, so the generic
    // "an admin turned this off" copy would send them looking for the wrong thing.
    const restricted = feature === 'admin'
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-16 text-center">
        <Lock className="h-6 w-6 text-muted-foreground" />
        <div>
          <p className="text-sm font-medium">
            {restricted ? 'You don’t have access to this page' : 'This page is turned off'}
          </p>
          <p className="text-sm text-muted-foreground">
            {restricted
              ? 'Ask an admin if you need it.'
              : 'An admin can switch it back on from the Admin page.'}
          </p>
        </div>
        {home && (
          <Button asChild variant="outline" size="sm">
            <Link href={home.href}>Go to {home.label}</Link>
          </Button>
        )}
      </div>
    )
  }

  return <>{children}</>
}
