'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ProcurementStage, SpecialOrderFlag } from '@/lib/types'
import { subTriageLabel } from '@/lib/special-order-triage'
import {
  AlertTriangle,
  Clock,
  CircleHelp,
  CircleCheck,
  PackageCheck,
  Inbox,
  FileClock,
  ShoppingCart,
} from 'lucide-react'

interface BadgeConfig {
  label: string
  className: string
  icon: typeof AlertTriangle
}

// The procurement-flow stage (the "where is it" axis).
const stageConfig: Record<ProcurementStage, BadgeConfig> = {
  open_pool: { label: 'Open Pool', className: 'bg-secondary text-muted-foreground border-border', icon: Inbox },
  unordered_po: { label: 'Unordered PO', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: FileClock },
  ordered: { label: 'Ordered', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: ShoppingCart },
  received: { label: 'Received', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

export function StageBadge({ stage }: { stage: ProcurementStage }) {
  const config = stageConfig[stage] ?? stageConfig.open_pool
  const Icon = config.icon
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px] font-medium', config.className)}>
      <Icon className="h-3 w-3" />
      {config.label}
    </Badge>
  )
}

// The within-stage attention flag (the "what needs doing" axis). Colour + icon are keyed
// off the flag; the text comes from the shared sub-triage labels so it matches the tiles.
const flagStyle: Record<SpecialOrderFlag, { className: string; icon: typeof AlertTriangle }> = {
  none: { className: 'bg-secondary text-muted-foreground border-border', icon: CircleCheck },
  aged: { className: 'bg-amber-100 text-amber-700 border-amber-200', icon: Clock },
  overdue: { className: 'bg-red-100 text-red-700 border-red-200', icon: AlertTriangle },
  critical: { className: 'bg-red-200 text-red-800 border-red-300', icon: AlertTriangle },
  no_eta: { className: 'bg-amber-100 text-amber-700 border-amber-200', icon: CircleHelp },
  ready_not_called: { className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

export function FlagBadge({
  stage,
  flag,
  daysOverdue,
}: {
  stage: ProcurementStage
  flag: SpecialOrderFlag
  daysOverdue?: number | null
}) {
  const { className, icon: Icon } = flagStyle[flag]
  // For an ordered SO that's late, show the actual day count alongside the label.
  const showDays = daysOverdue != null && daysOverdue > 0 && (flag === 'overdue' || flag === 'critical')
  const label = showDays
    ? `${flag === 'critical' ? 'Critical' : 'Overdue'} · ${daysOverdue}d`
    : subTriageLabel(stage, flag)
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px] font-medium', className)}>
      <Icon className="h-3 w-3" />
      {label}
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
