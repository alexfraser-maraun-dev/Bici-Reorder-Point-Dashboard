'use client'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ShopifyMatch, ShopifyMatchBasis, SpecialOrderSource, SlaSeverity, TriageStage } from '@/lib/types'
import {
  AlertTriangle,
  CircleHelp,
  PackageCheck,
  Inbox,
  FileClock,
  ShoppingCart,
  Store,
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
  shopify: { label: 'Shopify intake', className: 'bg-violet-100 text-violet-700 border-violet-200', icon: Store },
  open_pool: { label: 'Awaiting PO', className: 'bg-secondary text-muted-foreground border-border', icon: Inbox },
  unordered_po: { label: 'Draft PO', className: 'bg-orange-100 text-orange-700 border-orange-200', icon: FileClock },
  ordered: { label: 'In transit', className: 'bg-blue-100 text-blue-700 border-blue-200', icon: ShoppingCart },
  received: { label: 'Arrived', className: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: PackageCheck },
}

// Where the special order came from. Deliberately always rendered (including 'neither') --
// an unattributed SO is a real bucket that needs chasing, not an absence worth hiding.
const sourceConfig: Record<SpecialOrderSource, BadgeConfig> = {
  workorder: { label: 'Workorder', className: 'bg-cyan-100 text-cyan-700 border-cyan-200', icon: Wrench },
  shopify: { label: 'Shopify', className: 'bg-violet-100 text-violet-700 border-violet-200', icon: Store },
  neither: { label: 'Lightspeed direct', className: 'bg-amber-100 text-amber-700 border-amber-200', icon: HelpCircle },
}

export function SourceBadge({
  source,
  href,
  linkLabel,
}: {
  source: SpecialOrderSource | null | undefined
  href?: string | null
  linkLabel?: string
}) {
  const config = sourceConfig[source ?? 'neither'] ?? sourceConfig.neither
  const Icon = config.icon
  const content = (
    <>
      <Icon className="h-3 w-3" />
      {config.label}
    </>
  )

  if (href) {
    return (
      <Badge
        asChild
        variant="outline"
        className={cn('gap-1 text-xs font-medium', config.className)}
      >
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={linkLabel ?? `Open ${config.label} in a new tab`}
          title={linkLabel ?? `Open ${config.label} in a new tab`}
        >
          {content}
        </a>
      </Badge>
    )
  }

  return (
    <Badge variant="outline" className={cn('gap-1 text-xs font-medium', config.className)}>
      {content}
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
      className={cn('gap-1 text-xs font-medium', config.className, muted && 'opacity-50')}
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
    <Badge variant="outline" className={cn('gap-1 text-xs font-medium', config.className)}>
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
      <Badge variant="outline" title={title} className="gap-1 border-violet-200 bg-violet-100 text-xs font-medium text-violet-700">
        <Link2 className="h-3 w-3" />{basis === 'manual' ? 'Linked' : 'Matched'}
      </Badge>
    )
  }
  if (match === 'ambiguous') {
    return (
      <Badge variant="outline" title={title} className="gap-1 border-amber-200 bg-amber-100 text-xs font-medium text-amber-700">
        <CircleHelp className="h-3 w-3" />Ambiguous
      </Badge>
    )
  }
  if (possible) {
    return (
      <Badge variant="outline" title="One or more LS special orders could plausibly claim this order" className="gap-1 border-amber-200 bg-amber-50 text-xs font-medium text-amber-700">
        <CircleHelp className="h-3 w-3" />Possible match
      </Badge>
    )
  }
  // 'none' on an LS row means "no Shopify order"; on a Shopify-only row it reads as Unmatched.
  return (
    <Badge variant="outline" className="gap-1 border-border bg-secondary text-xs font-medium text-muted-foreground">
      <Unlink className="h-3 w-3" />Unmatched
    </Badge>
  )
}

export function SpecialOrderStatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase()
  let className = 'bg-secondary text-muted-foreground border-border'
  if (s.includes('not ordered')) className = 'bg-red-100 text-red-700 border-red-200'
  else if (s.includes('ready')) className = 'bg-emerald-100 text-emerald-700 border-emerald-200'
  else if (s.includes('received')) className = 'bg-blue-100 text-blue-700 border-blue-200'
  else if (s.includes('ordered')) className = 'bg-yellow-100 text-yellow-700 border-yellow-200'
  return (
    <Badge variant="outline" className={cn('text-xs font-medium', className)}>
      {status}
    </Badge>
  )
}
