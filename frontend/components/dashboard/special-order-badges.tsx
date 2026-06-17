'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { AgingBucket } from '@/lib/types'
import {
  AlertTriangle,
  Clock,
  CalendarClock,
  CircleHelp,
  PackageCheck,
  Truck,
  CircleCheck,
} from 'lucide-react'

interface AgingConfig {
  label: string
  className: string
  icon: typeof AlertTriangle
}

const agingConfig: Record<AgingBucket, AgingConfig> = {
  overdue: { label: 'Overdue', className: 'bg-red-100 text-red-700 border-red-200', icon: AlertTriangle },
  critical: { label: 'Critical', className: 'bg-red-100 text-red-700 border-red-200', icon: AlertTriangle },
  stale: { label: 'Stale', className: 'bg-red-200 text-red-800 border-red-300', icon: AlertTriangle },
  no_eta: { label: 'No ETA', className: 'bg-amber-100 text-amber-700 border-amber-200', icon: CircleHelp },
  no_po: { label: 'Not Ordered', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: CircleHelp },
  due_soon: { label: 'Due soon', className: 'bg-yellow-100 text-yellow-700 border-yellow-200', icon: CalendarClock },
  on_track: { label: 'On track', className: 'bg-secondary text-muted-foreground border-border', icon: Clock },
  receiving: { label: 'Receiving', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: Truck },
  received: { label: 'Received', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

export function AgingBadge({ bucket, daysOverdue }: { bucket: AgingBucket; daysOverdue: number | null }) {
  const config = agingConfig[bucket] ?? agingConfig.on_track
  const Icon = config.icon
  const showDays = daysOverdue !== null && daysOverdue > 0 && ['overdue', 'critical', 'stale'].includes(bucket)
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px] font-medium', config.className)}>
      <Icon className="h-3 w-3" />
      {config.label}
      {showDays ? ` ${daysOverdue}d` : ''}
    </Badge>
  )
}

// Maps the raw Lightspeed SpecialOrder.status string to red/yellow/green semantics,
// matching the POS mental model (Not Ordered = red, Ordered = yellow, Ready = green).
export function SpecialOrderStatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase()
  let className = 'bg-secondary text-muted-foreground border-border'
  if (s.includes('not ordered')) className = 'bg-red-100 text-red-700 border-red-200'
  else if (s.includes('ready')) className = 'bg-emerald-100 text-emerald-700 border-emerald-200'
  else if (s.includes('received')) className = 'bg-blue-100 text-blue-700 border-blue-200'
  else if (s.includes('ordered')) className = 'bg-yellow-100 text-yellow-700 border-yellow-200'
  return (
    <Badge variant="outline" className={cn('text-[10px] font-medium', className)}>
      {status}
    </Badge>
  )
}

export function ReadyNotCalledBadge({ active }: { active: boolean }) {
  if (!active) return null
  return (
    <Badge variant="outline" className="gap-1 bg-emerald-100 text-emerald-700 border-emerald-200 text-[10px] font-medium">
      <CircleCheck className="h-3 w-3" />
      Ready · not called
    </Badge>
  )
}
