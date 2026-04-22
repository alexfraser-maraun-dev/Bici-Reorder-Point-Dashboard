'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  History,
  FileText,
  Settings,
  Package,
  Menu,
} from 'lucide-react'
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
]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
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
              <svg 
                viewBox="0 0 160 40" 
                className="h-5 w-auto fill-primary" 
                aria-label="Bici Logo"
              >
                <path d="M0 0h14.5v30c0 5.5 4.5 10 10 10h10.5v-13h-6c-2.8 0-5-2.2-5-5V0H0zm50.5 0h14.5v40H50.5V0zM85 10c0-5.5 4.5-10 10-10h28.5c5.5 0 10 4.5 10 10v20c0 5.5-4.5 10-10 10H95c-5.5 0-10-4.5-10-10V10zm14.5 2h19v16h-19V12zM150 0h14.5v40H150V0z" />
                <circle cx="57.75" cy="5" r="5" className="fill-primary" />
                <circle cx="157.25" cy="5" r="5" className="fill-primary" />
              </svg>
            </div>
            <div className="h-6 w-[1px] bg-muted mx-1 hidden sm:block" />
            <span className="hidden font-semibold text-foreground/80 tracking-tight sm:inline-block">
              Inventory Replenishment
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

          {/* Right side */}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-muted-foreground hidden text-xs lg:inline-block">
              Connected to Lightspeed R-Series
            </span>
            <div className="h-2 w-2 rounded-full bg-emerald-500" title="Connected" />
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="p-4 lg:p-6">{children}</main>
    </div>
  )
}
