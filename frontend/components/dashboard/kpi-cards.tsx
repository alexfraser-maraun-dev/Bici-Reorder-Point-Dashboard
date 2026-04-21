'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import type { KpiSummary } from '@/lib/types'
import {
  Package,
  AlertTriangle,
  RefreshCw,
  Lock,
  Edit3,
  CheckCircle2,
  XCircle,
} from 'lucide-react'

interface KpiCardsProps {
  summary: KpiSummary
  isLoading?: boolean
}

const kpiConfig = [
  {
    key: 'totalManagedRows' as const,
    label: 'Total Managed',
    icon: Package,
    color: 'text-foreground',
    bgColor: 'bg-secondary',
  },
  {
    key: 'needsOrder' as const,
    label: 'Needs Order',
    icon: AlertTriangle,
    color: 'text-amber-600',
    bgColor: 'bg-amber-50',
  },
  {
    key: 'changedRows' as const,
    label: 'Changed',
    icon: RefreshCw,
    color: 'text-blue-600',
    bgColor: 'bg-blue-50',
  },
  {
    key: 'lockedRows' as const,
    label: 'Locked',
    icon: Lock,
    color: 'text-muted-foreground',
    bgColor: 'bg-muted',
  },
  {
    key: 'overrides' as const,
    label: 'Overrides',
    icon: Edit3,
    color: 'text-orange-600',
    bgColor: 'bg-orange-50',
  },
  {
    key: 'readyToPush' as const,
    label: 'Ready to Push',
    icon: CheckCircle2,
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-50',
  },
  {
    key: 'failedWritebacks' as const,
    label: 'Failed',
    icon: XCircle,
    color: 'text-red-600',
    bgColor: 'bg-red-50',
  },
]

export function KpiCards({ summary, isLoading }: KpiCardsProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {kpiConfig.map((kpi) => (
          <Card key={kpi.key} className="py-3">
            <CardContent className="px-4 py-0">
              <Skeleton className="mb-2 h-4 w-16" />
              <Skeleton className="h-7 w-12" />
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
      {kpiConfig.map((kpi) => {
        const Icon = kpi.icon
        const value = summary[kpi.key]
        return (
          <Card key={kpi.key} className="py-3">
            <CardContent className="px-4 py-0">
              <div className="flex items-center gap-2">
                <div className={`rounded-md p-1.5 ${kpi.bgColor}`}>
                  <Icon className={`h-3.5 w-3.5 ${kpi.color}`} />
                </div>
                <span className="text-muted-foreground text-xs font-medium">{kpi.label}</span>
              </div>
              <p className="mt-1.5 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
