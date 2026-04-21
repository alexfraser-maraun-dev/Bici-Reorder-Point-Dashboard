'use client'

import { useState } from 'react'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'
import type { SkuLocationRow } from '@/lib/types'
import {
  WritebackStatusBadge,
  NeedsOrderBadge,
  ChangedBadge,
  LockedBadge,
  OverrideBadge,
} from './status-badges'
import {
  Upload,
  Lock,
  Unlock,
  Edit3,
  Package,
  MapPin,
  TrendingUp,
  Clock,
  ShieldCheck,
  History,
} from 'lucide-react'

interface RowDetailDrawerProps {
  row: SkuLocationRow | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onPush: (rowId: string) => Promise<void>
  onToggleLock: (rowId: string) => Promise<void>
  onSaveOverride: (rowId: string, reorderPoint: number, desiredLevel: number) => Promise<void>
}

// Mock audit history
const mockAuditHistory = [
  {
    id: '1',
    action: 'Pushed to Lightspeed',
    user: 'john.doe@company.com',
    timestamp: '2024-01-15T10:30:00Z',
    details: 'Reorder point: 15 → 22',
  },
  {
    id: '2',
    action: 'Override set',
    user: 'jane.smith@company.com',
    timestamp: '2024-01-14T14:15:00Z',
    details: 'Desired level manually set to 45',
  },
  {
    id: '3',
    action: 'Locked',
    user: 'mike.wilson@company.com',
    timestamp: '2024-01-13T09:00:00Z',
    details: 'Locked for review',
  },
  {
    id: '4',
    action: 'Recommendation updated',
    user: 'system',
    timestamp: '2024-01-12T08:00:00Z',
    details: 'Daily run completed',
  },
]

export function RowDetailDrawer({
  row,
  open,
  onOpenChange,
  onPush,
  onToggleLock,
  onSaveOverride,
}: RowDetailDrawerProps) {
  const [isPushing, setIsPushing] = useState(false)
  const [isLocking, setIsLocking] = useState(false)
  const [isEditingOverride, setIsEditingOverride] = useState(false)
  const [overrideReorderPoint, setOverrideReorderPoint] = useState('')
  const [overrideDesiredLevel, setOverrideDesiredLevel] = useState('')
  const [isSavingOverride, setIsSavingOverride] = useState(false)

  if (!row) return null

  const handlePush = async () => {
    setIsPushing(true)
    try {
      await onPush(row.id)
    } finally {
      setIsPushing(false)
    }
  }

  const handleToggleLock = async () => {
    setIsLocking(true)
    try {
      await onToggleLock(row.id)
    } finally {
      setIsLocking(false)
    }
  }

  const handleStartEditOverride = () => {
    setOverrideReorderPoint(row.recommendedReorderPoint.toString())
    setOverrideDesiredLevel(row.recommendedDesiredLevel.toString())
    setIsEditingOverride(true)
  }

  const handleSaveOverride = async () => {
    setIsSavingOverride(true)
    try {
      await onSaveOverride(
        row.id,
        parseInt(overrideReorderPoint),
        parseInt(overrideDesiredLevel)
      )
      setIsEditingOverride(false)
    } finally {
      setIsSavingOverride(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[450px] sm:max-w-[450px]">
        <SheetHeader className="pb-4">
          <div className="flex items-start justify-between">
            <div>
              <SheetTitle className="flex items-center gap-2">
                <span className="font-mono">{row.sku}</span>
                <WritebackStatusBadge status={row.writebackStatus} />
              </SheetTitle>
              <SheetDescription className="mt-1">{row.product}</SheetDescription>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <NeedsOrderBadge needsOrder={row.needsOrder} />
            <ChangedBadge changed={row.changed} />
            <LockedBadge locked={row.locked} />
            <OverrideBadge override={row.override} />
          </div>
        </SheetHeader>

        <ScrollArea className="h-[calc(100vh-200px)] pr-4">
          <div className="space-y-6">
            {/* Product Info */}
            <Section icon={Package} title="Product Information">
              <InfoRow label="Brand" value={row.brand} />
              <InfoRow label="Vendor" value={row.vendor} />
              <InfoRow label="Category" value={row.category} />
            </Section>

            {/* Location */}
            <Section icon={MapPin} title="Location">
              <InfoRow label="Store" value={row.location} />
            </Section>

            {/* Inventory Status */}
            <Section icon={Package} title="Inventory Status">
              <div className="grid grid-cols-3 gap-4">
                <MetricCard label="On Hand" value={row.onHand} highlight={row.onHand === 0} />
                <MetricCard label="On Order" value={row.onOrder} />
                <MetricCard label="Position" value={row.inventoryPosition} />
              </div>
            </Section>

            {/* Current vs Recommended */}
            <Section icon={TrendingUp} title="Reorder Points">
              <div className="space-y-3">
                <div className="bg-muted/50 grid grid-cols-2 gap-4 rounded-lg p-3">
                  <div>
                    <p className="text-muted-foreground mb-1 text-xs">Current ROP</p>
                    <p className="text-lg font-semibold tabular-nums">{row.currentReorderPoint}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground mb-1 text-xs">Recommended ROP</p>
                    <p
                      className={cn(
                        'text-lg font-semibold tabular-nums',
                        row.currentReorderPoint !== row.recommendedReorderPoint && 'text-blue-600'
                      )}
                    >
                      {row.recommendedReorderPoint}
                    </p>
                  </div>
                </div>
                <div className="bg-muted/50 grid grid-cols-2 gap-4 rounded-lg p-3">
                  <div>
                    <p className="text-muted-foreground mb-1 text-xs">Current Desired</p>
                    <p className="text-lg font-semibold tabular-nums">{row.currentDesiredLevel}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground mb-1 text-xs">Recommended Desired</p>
                    <p
                      className={cn(
                        'text-lg font-semibold tabular-nums',
                        row.currentDesiredLevel !== row.recommendedDesiredLevel && 'text-blue-600'
                      )}
                    >
                      {row.recommendedDesiredLevel}
                    </p>
                  </div>
                </div>
              </div>
            </Section>

            {/* Sales & Lead Time */}
            <Section icon={Clock} title="Sales & Lead Time">
              <div className="grid grid-cols-2 gap-4">
                <InfoRow label="Trailing Units Sold" value={row.trailingUnitsSold.toString()} />
                <InfoRow label="Days Out of Stock" value={row.daysOutOfStock.toString()} />
                <InfoRow label="Avg Daily Sales" value={row.avgDailySales.toFixed(1)} />
                <InfoRow label="Lead Time Days" value={row.leadTimeDays.toString()} />
              </div>
            </Section>

            {/* Safety Stock & Buy Qty */}
            <Section icon={ShieldCheck} title="Safety & Suggested Buy">
              <div className="grid grid-cols-2 gap-4">
                <MetricCard label="Safety Stock" value={row.safetyStock} />
                <MetricCard
                  label="Suggested Buy Qty"
                  value={row.suggestedBuyQty}
                  highlight={row.suggestedBuyQty > 0}
                  highlightColor="amber"
                />
              </div>
            </Section>

            {/* Override Section */}
            {isEditingOverride ? (
              <Section icon={Edit3} title="Edit Override">
                <div className="space-y-3">
                  <div>
                    <Label htmlFor="override-rop" className="text-xs">
                      Override Reorder Point
                    </Label>
                    <Input
                      id="override-rop"
                      type="number"
                      value={overrideReorderPoint}
                      onChange={(e) => setOverrideReorderPoint(e.target.value)}
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <Label htmlFor="override-desired" className="text-xs">
                      Override Desired Level
                    </Label>
                    <Input
                      id="override-desired"
                      type="number"
                      value={overrideDesiredLevel}
                      onChange={(e) => setOverrideDesiredLevel(e.target.value)}
                      className="mt-1"
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={handleSaveOverride}
                      disabled={isSavingOverride}
                      className="flex-1"
                    >
                      {isSavingOverride && <Spinner className="mr-1.5 h-3.5 w-3.5" />}
                      Save Override
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setIsEditingOverride(false)}
                      className="flex-1"
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              </Section>
            ) : null}

            {/* Audit History */}
            <Section icon={History} title="Audit History">
              <div className="space-y-3">
                {mockAuditHistory.map((entry) => (
                  <div key={entry.id} className="border-l-2 border-l-muted pl-3">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium">{entry.action}</p>
                      <Badge variant="secondary" className="text-[10px]">
                        {new Date(entry.timestamp).toLocaleDateString()}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground text-xs">{entry.user}</p>
                    <p className="text-muted-foreground mt-0.5 text-xs">{entry.details}</p>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        </ScrollArea>

        {/* Actions Footer */}
        <div className="border-t pt-4">
          <div className="flex gap-2">
            <Button
              onClick={handlePush}
              disabled={isPushing || row.locked}
              className="flex-1"
            >
              {isPushing ? (
                <Spinner className="mr-1.5 h-4 w-4" />
              ) : (
                <Upload className="mr-1.5 h-4 w-4" />
              )}
              Push to Lightspeed
            </Button>
            <Button
              variant="outline"
              onClick={handleToggleLock}
              disabled={isLocking}
            >
              {isLocking ? (
                <Spinner className="h-4 w-4" />
              ) : row.locked ? (
                <Unlock className="h-4 w-4" />
              ) : (
                <Lock className="h-4 w-4" />
              )}
            </Button>
            <Button variant="outline" onClick={handleStartEditOverride}>
              <Edit3 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ElementType
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <Icon className="text-muted-foreground h-4 w-4" />
        <h4 className="text-sm font-medium">{title}</h4>
      </div>
      <div className="bg-muted/30 rounded-lg p-3">{children}</div>
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  )
}

function MetricCard({
  label,
  value,
  highlight = false,
  highlightColor = 'red',
}: {
  label: string
  value: number
  highlight?: boolean
  highlightColor?: 'red' | 'amber'
}) {
  return (
    <div className="rounded-md bg-background p-2 text-center">
      <p className="text-muted-foreground text-xs">{label}</p>
      <p
        className={cn(
          'text-xl font-semibold tabular-nums',
          highlight && highlightColor === 'red' && 'text-red-600',
          highlight && highlightColor === 'amber' && 'text-amber-600'
        )}
      >
        {value}
      </p>
    </div>
  )
}
