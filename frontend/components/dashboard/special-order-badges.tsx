'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ProcurementStage, SpecialOrderFlag, ShopifyMatch, TriageStage } from '@/lib/types'
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
  Store,
  Link2,
  Unlink,
} from 'lucide-react'

interface BadgeConfig {
  label: string
  className: string
  icon: typeof AlertTriangle
}

// The triage stage (the "where is it" axis). `shopify` is the leftmost inbound stage.
const stageConfig: Record<TriageStage, BadgeConfig> = {
  shopify: { label: 'Shopify', className: 'bg-violet-100 text-violet-700 border-violet-200', icon: Store },
  open_pool: { label: 'Open Pool', className: 'bg-secondary text-muted-foreground border-border', icon: Inbox },
  unordered_po: { label: 'Unordered PO', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: FileClock },
  ordered: { label: 'Ordered', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: ShoppingCart },
  received: { label: 'Received', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

export function StageBadge({ stage }: { stage: TriageStage }) {
  const config = stageConfig[stage] ?? stageConfig.open_pool
  const Icon = config.icon
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px] font-medium', config.className)}>
      <Icon className="h-3 w-3" />
      {config.label}
    </Badge>
  )
}

// Shopify match status — used in the Flag cell for Shopify-only ("Unmatched") rows and as a
// small hint on matched/ambiguous LS rows.
export function ShopifyMatchBadge({ match }: { match: ShopifyMatch | 'unmatched' }) {
  if (match === 'matched') {
    return (
      <Badge variant="outline" className="gap-1 border-violet-200 bg-violet-100 text-[10px] font-medium text-violet-700">
        <Link2 className="h-3 w-3" />Matched
      </Badge>
    )
  }
  if (match === 'ambiguous') {
    return (
      <Badge variant="outline" className="gap-1 border-amber-200 bg-amber-100 text-[10px] font-medium text-amber-700">
        <CircleHelp className="h-3 w-3" />Ambiguous
      </Badge>
    )
  }
  // 'none' on an LS row means "no Shopify order"; on a Shopify-only row it reads as Unmatched.
  return (
    <Badge variant="outline" className="gap-1 border-border bg-secondary text-[10px] font-medium text-muted-foreground">
      <Unlink className="h-3 w-3" />Unmatched
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
