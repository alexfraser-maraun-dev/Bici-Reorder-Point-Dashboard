'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ProcurementStage, SpecialOrderFlag, ShopifyMatch, TriageStage } from '@/lib/types'
import { subTriageLabel } from '@/lib/special-order-triage'
import {
  AlertTriangle,
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

// The within-stage attention flag (the "what needs doing" axis). Colour + icon are keyed off
// the flag; lateness escalates 1-2d -> 3-7d -> 8+d for a progressively more dramatic highlight.
const flagStyle: Record<SpecialOrderFlag, { className: string; icon: typeof AlertTriangle }> = {
  none: { className: 'bg-secondary text-muted-foreground border-border', icon: CircleCheck },
  overdue: { className: 'bg-red-100 text-red-700 border-red-200', icon: AlertTriangle },
  overdue_mid: { className: 'bg-red-300 text-red-900 border-red-400', icon: AlertTriangle },
  critical: { className: 'border-red-700 bg-red-600 text-white', icon: AlertTriangle },
  no_eta: { className: 'bg-amber-100 text-amber-700 border-amber-200', icon: CircleHelp },
  ready_not_called: { className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

const LATE_FLAGS: SpecialOrderFlag[] = ['overdue', 'overdue_mid', 'critical']

// The badge word per stage — Ordered SOs are "Overdue/Critical" against a date; the pre-order
// stages read in their own language ("Open Order" / "Unordered") whether the day count is days
// past the Shopify ETA or days sitting in stage.
function lateWord(stage: ProcurementStage, flag: SpecialOrderFlag): string {
  if (stage === 'open_pool') return 'Open Order'
  if (stage === 'unordered_po') return 'Unordered'
  return flag === 'critical' ? 'Critical' : 'Overdue'
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

  let label: string
  if (LATE_FLAGS.includes(flag)) {
    const word = lateWord(stage, flag)
    label = daysOverdue != null && daysOverdue > 0 ? `${word} · ${daysOverdue}d` : word
  } else if (flag === 'none') {
    label = 'Healthy'
  } else {
    label = subTriageLabel(stage, flag)
  }

  const bold = flag === 'critical' || flag === 'overdue_mid'
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px]', bold ? 'font-semibold' : 'font-medium', className)}>
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
