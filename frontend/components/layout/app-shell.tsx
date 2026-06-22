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
} from 'lucide-react'
import { signOut, useSession } from 'next-auth/react'
import { APP_VERSION, APP_VERSION_SUMMARY } from '@/lib/version'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useState } from 'react'

const navigation = [
  { name: 'Ordering', href: '/', icon: LayoutDashboard },
  { name: 'Special Orders', href: '/special-orders', icon: PackageSearch },
  { name: 'How it Works', href: '/how-to-use', icon: CircleHelp },
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
                {navigation.map((item) => {
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
                    </Link>
                  )
                })}
              </nav>
            </SheetContent>
          </Sheet>

          {/* Logo */}
          <div className="flex items-center gap-4">
            <div className="flex items-center">
              <img
                src="/logo.svg"
                alt="Bici Logo"
                className="h-5 w-auto"
              />
            </div>
            <div className="h-6 w-[1px] bg-muted mx-1 hidden sm:block" />
            <div className="hidden items-baseline gap-2 sm:flex">
              <span className="font-semibold text-foreground/80 tracking-tight">
                Procurement Tool
              </span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
                v{APP_VERSION}
              </span>
              <span
                className="hidden max-w-[300px] truncate text-[11px] italic text-muted-foreground/80 xl:inline"
                title={APP_VERSION_SUMMARY}
              >
                {APP_VERSION_SUMMARY}
              </span>
            </div>
          </div>

          {/* Desktop navigation */}
          <nav className="ml-8 hidden items-center gap-1 lg:flex">
            {navigation.map((item) => {
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
