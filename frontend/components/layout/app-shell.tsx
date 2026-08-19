'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  PackageSearch,
  Menu,
  CircleHelp,
  LogOut,
  TrendingUp,
  ShieldCheck,
} from 'lucide-react'
import { PriceIntelNavBadge } from '@/components/price-intel/nav-badge'
import { useAccess } from '@/lib/access/hooks'
import type { FeatureKey } from '@/lib/access/types'
import { signOut, useSession } from 'next-auth/react'
import { APP_VERSION, APP_VERSION_SUMMARY, APP_GIT_SHA, APP_GIT_DATE } from '@/lib/version'
import { ConnectionIndicators } from '@/components/layout/connection-indicators'
import { BrandMark } from '@/components/layout/brand-mark'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useState } from 'react'

// Every entry is gated on its feature key (see lib/access). A page whose feature
// is off never renders a link and its route redirects, so nothing behind it loads.
const navigation: { name: string; href: string; icon: typeof LayoutDashboard; feature: FeatureKey }[] = [
  { name: 'Ordering', href: '/', icon: LayoutDashboard, feature: 'ordering' },
  { name: 'Special Orders', href: '/special-orders', icon: PackageSearch, feature: 'special_orders' },
  // Also requires NEXT_PUBLIC_PRICE_INTEL_ENABLED at build time; the Admin page
  // can only hide it further, never turn on a build that shipped without it.
  ...(process.env.NEXT_PUBLIC_PRICE_INTEL_ENABLED === 'true'
    ? [{ name: 'Price Intel', href: '/price-intelligence', icon: TrendingUp, feature: 'price_intel' as FeatureKey }]
    : []),
  { name: 'How it Works', href: '/how-to-use', icon: CircleHelp, feature: 'how_to_use' },
  { name: 'Admin', href: '/admin', icon: ShieldCheck, feature: 'admin' },
]

interface AppShellProps {
  children: React.ReactNode
  headerActions?: React.ReactNode
  mainClassName?: string
}

export function AppShell({ children, headerActions, mainClassName }: AppShellProps) {
  const pathname = usePathname()
  const { data: session } = useSession()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const { isEnabled, isAdmin } = useAccess()
  // isEnabled is optimistic while access loads (better than flashing an empty
  // nav at everyone), but Admin is the one link that must not appear to a
  // non-admin even for a moment, so it waits for the confirmed answer.
  const visibleNavigation = navigation.filter((item) =>
    item.feature === 'admin' ? isAdmin : isEnabled(item.feature)
  )

  return (
    <div className="bg-background min-h-screen">
      {/* Header */}
      <header className="bg-card sticky top-0 z-50 border-b">
        <div className="flex h-14 items-center gap-4 px-4 lg:px-6">
          {/* Mobile menu button */}
          <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden">
                <Menu className="h-5 w-5" />
                <span className="sr-only">Open menu</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 p-0">
              <div className="flex h-14 items-center border-b px-4">
                <span className="text-lg font-semibold">SKU Automation</span>
              </div>
              <nav className="space-y-1 p-2">
                {visibleNavigation.map((item) => {
                  const isActive = pathname === item.href
                  return (
                    <Link
                      key={item.name}
                      href={item.href}
                      onClick={() => setMobileMenuOpen(false)}
                      className={cn(
                        'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                        isActive
                          ? 'bg-primary text-primary-foreground'
                          : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                      )}
                    >
                      <item.icon className="h-4 w-4" />
                      {item.name}
                      {item.href === '/price-intelligence' && <PriceIntelNavBadge />}
                    </Link>
                  )
                })}
              </nav>
            </SheetContent>
          </Sheet>

          {/* Logo */}
          <div className="flex items-center gap-4">
            <div className="flex w-9 items-center">
              <BrandMark
                animated
                bColor="var(--color-foreground)"
                lineColor="var(--color-signal)"
                className="pulse-beat"
              />
            </div>
            <div className="h-6 w-[1px] bg-muted mx-1 hidden sm:block" />
            <div className="hidden items-baseline gap-2 sm:flex">
              <span className="font-semibold text-foreground/80 tracking-tight">
                BICI Pulse
              </span>
              <span
                className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground"
                title={APP_GIT_DATE ? `Built from ${APP_GIT_SHA} · ${APP_GIT_DATE}` : undefined}
              >
                v{APP_VERSION}{APP_GIT_SHA && ` · ${APP_GIT_SHA}`}
              </span>
              {APP_VERSION_SUMMARY && (
                <span
                  className="hidden max-w-[300px] truncate text-[11px] italic text-muted-foreground/80 xl:inline"
                  title={APP_VERSION_SUMMARY}
                >
                  {APP_VERSION_SUMMARY}
                </span>
              )}
            </div>
          </div>

          {/* Desktop navigation */}
          <nav className="ml-8 hidden items-center gap-1 lg:flex">
            {visibleNavigation.map((item) => {
              const isActive = pathname === item.href
              return (
                <Link
                  key={item.name}
                  href={item.href}
                  className={cn(
                    'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-muted text-foreground'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  <item.icon className="h-4 w-4" />
                  {item.name}
                  {item.href === '/price-intelligence' && <PriceIntelNavBadge />}
                </Link>
              )
            })}
          </nav>
          {/* Right side (User Profile) */}
          <div className="ml-auto flex min-w-0 items-center gap-3">
            {headerActions && (
              <div className="hidden min-w-0 items-center justify-end gap-3 lg:flex">
                {headerActions}
              </div>
            )}
            <ConnectionIndicators />
            <div className="h-4 w-[1px] bg-border hidden sm:block" />

            {/* User Profile */}
            <div className="flex min-w-0 items-center gap-2">
              <span className="hidden max-w-[180px] truncate text-[10px] font-medium text-muted-foreground md:inline">
                {session?.user?.email || 'not signed in'}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => signOut({ callbackUrl: '/' })}
                className="text-muted-foreground hover:text-foreground h-8 w-8 p-0"
                title="Sign Out"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
        {headerActions && (
          <div className="flex flex-wrap items-center gap-3 border-t px-4 py-2 lg:hidden">
            {headerActions}
          </div>
        )}
      </header>

      {/* Main content */}
      <main className={cn("p-4 lg:p-6", mainClassName)}>{children}</main>
    </div>
  )
}
