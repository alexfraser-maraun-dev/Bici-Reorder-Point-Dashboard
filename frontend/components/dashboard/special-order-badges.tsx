'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ProcurementStage, SpecialOrderFlag, ShopifyMatch, ShopifyMatchBasis, SpecialOrderSource, SlaSeverity, TriageStage } from '@/lib/types'
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
  ListChecks,
  Link2,
  Unlink,
  Wrench,
  HelpCircle,
  CalendarX,
  Ban,
  Zap,
  Hourglass,
  ShieldQuestion,
} from 'lucide-react'

interface BadgeConfig {
  label: string
  className: string
  icon: typeof AlertTriangle
}

// The triage stage (the "where is it" axis). `shopify` is the leftmost inbound stage.
const stageConfig: Record<TriageStage, BadgeConfig> = {
  shopify: { label: 'Shopify', className: 'bg-violet-100 text-violet-700 border-violet-200', icon: Store },
  // Overlay tile id — no row ever carries this as its real stage, so this badge never renders;
  // the entry only satisfies the Record<TriageStage, …> type.
  recommended_action: { label: 'Recommended Action', className: 'bg-slate-100 text-slate-700 border-slate-200', icon: ListChecks },
  open_pool: { label: 'Open Pool', className: 'bg-secondary text-muted-foreground border-border', icon: Inbox },
  unordered_po: { label: 'Unordered PO', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: FileClock },
  ordered: { label: 'Ordered', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: ShoppingCart },
  received: { label: 'Received', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

// Where the special order came from. Deliberately always rendered (including 'neither') --
// an unattributed SO is a real bucket that needs chasing, not an absence worth hiding.
const sourceConfig: Record<SpecialOrderSource, BadgeConfig> = {
  workorder: { label: 'Workorder', className: 'bg-cyan-100 text-cyan-700 border-cyan-200', icon: Wrench },
  shopify: { label: 'Shopify', className: 'bg-violet-100 text-violet-700 border-violet-200', icon: Store },
  neither: { label: 'Unattributed', className: 'bg-amber-100 text-amber-700 border-amber-200', icon: HelpCircle },
}

export function SourceBadge({ source }: { source: SpecialOrderSource | null | undefined }) {
  const config = sourceConfig[source ?? 'neither'] ?? sourceConfig.neither
  const Icon = config.icon
  return (
    <Badge variant="outline" className={cn('gap-1 text-[10px] font-medium', config.className)}>
      <Icon className="h-3 w-3" />
      {config.label}
    </Badge>
  )
}

// The SLA verdict. Ordered worst-first to match SEVERITY_ORDER in so_sla_service.py.
// 'on_track' and 'closed_out' render nothing — a badge on every healthy row is noise, and the
// point of this column is to make the ~37 that need action findable among ~230.
const severityConfig: Partial<Record<SlaSeverity, BadgeConfig>> = {
  promise_missed: { label: 'Promise missed', className: 'bg-red-600 text-white border-red-700', icon: CalendarX },
  impossible: { label: 'Cannot make ETA', className: 'bg-red-100 text-red-700 border-red-200', icon: Ban },
  order_today: { label: 'Order today', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: Zap },
  stage_stalled: { label: 'Stalled', className: 'bg-amber-100 text-amber-800 border-amber-200', icon: Hourglass },
  at_risk: { label: 'At risk', className: 'bg-yellow-100 text-yellow-800 border-yellow-200', icon: AlertTriangle },
  no_promise: { label: 'No promise', className: 'bg-slate-100 text-slate-600 border-slate-200', icon: ShieldQuestion },
}

export function SeverityBadge({ severity, muted }: { severity: SlaSeverity; muted?: boolean }) {
  const config = severityConfig[severity]
  if (!config) return null
  const Icon = config.icon
  return (
    <Badge
      variant="outline"
      className={cn('gap-1 text-[10px] font-medium', config.className, muted && 'opacity-50')}
    >
      <Icon className="h-3 w-3" />
      {config.label}
    </Badge>
  )
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

// Human wording for how a match was made — shown as a hover title on the badge.
const BASIS_LABEL: Record<ShopifyMatchBasis, string> = {
  email_sku: 'Matched by customer email + SKU',
  phone_sku: 'Matched by customer phone + SKU',
  name_sku: 'Matched by customer name + SKU',
  sku_only: 'Matched by SKU alone (no identity info to compare)',
  sku_conflict: 'Same SKU, but the customer details disagree — needs a human decision',
  manual: 'Linked manually',
}

// Shopify match status — used in the Flag cell for Shopify-only ("Unmatched") rows and as a
// small hint on matched/ambiguous LS rows. `possible` softens Unmatched for Shopify orders
// that are candidates of an ambiguous LS SO.
export function ShopifyMatchBadge({
  match,
  basis,
  possible,
}: {
  match: ShopifyMatch | 'unmatched'
  basis?: ShopifyMatchBasis | null
  possible?: boolean
}) {
  const title = basis ? BASIS_LABEL[basis] : undefined
  if (match === 'matched') {
    return (
      <Badge variant="outline" title={title} className="gap-1 border-violet-200 bg-violet-100 text-[10px] font-medium text-violet-700">
        <Link2 className="h-3 w-3" />{basis === 'manual' ? 'Linked' : 'Matched'}
      </Badge>
    )
  }
  if (match === 'ambiguous') {
    return (
      <Badge variant="outline" title={title} className="gap-1 border-amber-200 bg-amber-100 text-[10px] font-medium text-amber-700">
        <CircleHelp className="h-3 w-3" />Ambiguous
      </Badge>
    )
  }
  if (possible) {
    return (
      <Badge variant="outline" title="One or more LS special orders could plausibly claim this order" className="gap-1 border-amber-200 bg-amber-50 text-[10px] font-medium text-amber-700">
        <CircleHelp className="h-3 w-3" />Possible match
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
  return flag === 'critical' ? 'PO Critically Overdue' : 'PO Overdue'
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
