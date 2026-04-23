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
  CircleHelp,
  LogOut,
} from 'lucide-react'
import { signOut } from 'next-auth/react'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useState, useEffect } from 'react'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Recommendation Runs', href: '/runs', icon: History },
  { name: 'Writeback Audit', href: '/audit', icon: FileText },
  { name: 'Managed SKUs', href: '/managed-skus', icon: Package },
  { name: 'How it Works', href: '/how-to-use', icon: CircleHelp },
]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [lsStatus, setLsStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking')

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
        const response = await fetch(`${baseUrl}/api/health/lightspeed`)
        if (response.ok) {
          setLsStatus('connected')
        } else {
          setLsStatus('disconnected')
        }
      } catch (error) {
        setLsStatus('disconnected')
      }
    }
    
    checkHealth()
    const interval = setInterval(checkHealth, 30000)
    return () => clearInterval(interval)
  }, [])

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

          {/* Right side */}
          <div className="ml-auto flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className={cn(
                "hidden text-xs lg:inline-block transition-colors font-medium",
                lsStatus === 'connected' ? "text-muted-foreground" : 
                lsStatus === 'checking' ? "text-muted-foreground/60" : "text-red-500 font-bold"
              )}>
                {lsStatus === 'connected' ? 'Connected to Lightspeed' : 
                 lsStatus === 'checking' ? 'Checking connection...' : 
                 'Lightspeed Disconnected'}
              </span>
              <div 
                className={cn(
                  "h-2.5 w-2.5 rounded-full transition-colors",
                  lsStatus === 'connected' ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]" : 
                  lsStatus === 'checking' ? "bg-yellow-500 animate-pulse" : 
                  "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]"
                )} 
                title={lsStatus === 'connected' ? 'Connected' : 'Disconnected'} 
              />
            </div>
            <div className="h-4 w-[1px] bg-border hidden sm:block" />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => signOut({ callbackUrl: '/' })}
              className="text-muted-foreground hover:text-foreground h-8 px-2 flex items-center"
              title="Sign Out"
            >
              <LogOut className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline-block text-xs font-medium">Sign Out</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="p-4 lg:p-6">{children}</main>
    </div>
  )
}
