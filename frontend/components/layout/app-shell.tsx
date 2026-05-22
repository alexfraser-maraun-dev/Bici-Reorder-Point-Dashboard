'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  History,
  FileText,
  Package,
  Menu,
  CircleHelp,
  LogOut,
} from 'lucide-react'
import { signOut, useSession } from 'next-auth/react'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useState } from 'react'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Recommendation Runs', href: '/runs', icon: History },
  { name: 'Writeback Audit', href: '/audit', icon: FileText },
  { name: 'Managed SKUs', href: '/managed-skus', icon: Package },
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
            <span className="hidden font-semibold text-foreground/80 tracking-tight sm:inline-block">
              Reorder Point Config Tool
            </span>
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
            <div className="flex items-center gap-3">
              <div className="flex flex-col items-end">
                <span className="text-xs font-semibold text-foreground/90">
                  {session?.user?.name || 'Authorized User'}
                </span>
                <span className="text-[10px] text-muted-foreground font-medium">
                  {session?.user?.email || 'not signed in'}
                </span>
              </div>
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
