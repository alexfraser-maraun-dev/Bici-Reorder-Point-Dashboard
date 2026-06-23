'use client'

import { useConnectionStatus } from '@/lib/hooks'
import { cn } from '@/lib/utils'

type ConnectionState = 'checking' | 'connected' | 'disconnected'

function StatusIndicator({ label, status }: { label: string; status: ConnectionState }) {
  return (
    <div className="flex items-center gap-2 whitespace-nowrap" title={`${label}: ${status}`}>
      <div className={cn(
        "h-1.5 w-1.5 rounded-full",
        status === 'connected' ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.3)]" :
        status === 'checking' ? "bg-yellow-500 animate-pulse" : "bg-red-500"
      )} />
      <span className="text-[10px] font-semibold text-foreground/70">{label}</span>
    </div>
  )
}

// Live backend connection dots, rendered globally in the app header so every page shows the
// same Lightspeed / BigQuery / Shopify status in the same place.
export function ConnectionIndicators() {
  const { lsStatus, bqStatus, shopifyStatus } = useConnectionStatus()
  return (
    <div className="hidden items-center gap-3 sm:flex">
      <StatusIndicator label="Lightspeed" status={lsStatus} />
      <StatusIndicator label="BigQuery" status={bqStatus} />
      <StatusIndicator label="Shopify" status={shopifyStatus} />
    </div>
  )
}
