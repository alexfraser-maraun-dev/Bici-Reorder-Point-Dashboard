'use client'

import { Badge } from '@/components/ui/badge'
import type { WritebackStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface StatusBadgeProps {
  status: WritebackStatus
}

export function WritebackStatusBadge({ status }: StatusBadgeProps) {
  const config = {
    pending: {
      label: 'Pending',
      className: 'bg-amber-100 text-amber-700 border-amber-200',
    },
    success: {
      label: 'Pushed',
      className: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    },
    failed: {
      label: 'Failed',
      className: 'bg-red-100 text-red-700 border-red-200',
    },
    not_pushed: {
      label: 'Not Pushed',
      className: 'bg-secondary text-muted-foreground border-border',
    },
  }

  const { label, className } = config[status]

  return (
    <Badge variant="outline" className={cn('text-[10px] font-medium', className)}>
      {label}
    </Badge>
  )
}

interface BooleanBadgeProps {
  active: boolean
  activeLabel: string
  inactiveLabel?: string
  variant?: 'warning' | 'info' | 'muted' | 'success' | 'destructive'
  showInactive?: boolean
}

export function BooleanBadge({
  active,
  activeLabel,
  inactiveLabel,
  variant = 'info',
  showInactive = false,
}: BooleanBadgeProps) {
  if (!active && !showInactive) return null

  const variantStyles = {
    warning: 'bg-amber-100 text-amber-700 border-amber-200',
    info: 'bg-blue-100 text-blue-700 border-blue-200',
    muted: 'bg-secondary text-muted-foreground border-border',
    success: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    destructive: 'bg-red-100 text-red-700 border-red-200',
  }

  return (
    <Badge
      variant="outline"
      className={cn(
        'text-[10px] font-medium',
        active ? variantStyles[variant] : variantStyles.muted
      )}
    >
      {active ? activeLabel : inactiveLabel}
    </Badge>
  )
}

interface NeedsOrderBadgeProps {
  needsOrder: boolean
}

export function NeedsOrderBadge({ needsOrder }: NeedsOrderBadgeProps) {
  if (!needsOrder) return null

  return (
    <Badge variant="outline" className="bg-amber-100 text-amber-700 border-amber-200 text-[10px] font-medium">
      Order
    </Badge>
  )
}

interface ChangedBadgeProps {
  changed: boolean
}

export function ChangedBadge({ changed }: ChangedBadgeProps) {
  if (!changed) return null

  return (
    <Badge variant="outline" className="bg-blue-100 text-blue-700 border-blue-200 text-[10px] font-medium">
      Changed
    </Badge>
  )
}

interface LockedBadgeProps {
  locked: boolean
}

export function LockedBadge({ locked }: LockedBadgeProps) {
  if (!locked) return null

  return (
    <Badge variant="outline" className="bg-secondary text-muted-foreground border-border text-[10px] font-medium">
      Locked
    </Badge>
  )
}

interface OverrideBadgeProps {
  override: boolean
}

export function OverrideBadge({ override }: OverrideBadgeProps) {
  if (!override) return null

  return (
    <Badge variant="outline" className="bg-orange-100 text-orange-700 border-orange-200 text-[10px] font-medium">
      Override
    </Badge>
  )
}
