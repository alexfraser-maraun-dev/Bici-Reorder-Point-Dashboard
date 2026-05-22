'use client'

import { useState, useMemo, useEffect } from 'react'
import { useSession } from 'next-auth/react'
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { 
  Store, 
  MapPin,
  RefreshCw,
  Search,
  Settings2,
  Calendar,
  ShieldCheck,
  ArrowUpDown,
  Filter,
  TrendingUp,
  Zap,
  CircleCheck,
  CircleAlert,
  Info,
  BookmarkPlus,
  AlertTriangle
} from 'lucide-react'
import { cn } from '@/lib/utils'

export type AdjustmentMode = 'shrink' | 'min_days' | 'cap' | 'raw'
export type DemandWeights = {
  weight14d: number
  weight15To30d: number
  weight31To60d: number
}
type InventoryStatus =
  | 'critical'
  | 'low'
  | 'warning'
  | 'healthy'
  | 'incoming'
  | 'on_target'
  | 'high'
  | 'overstock'
  | 'no_demand'
type MomentumStatus = 'surging' | 'rising' | 'spiky' | 'flat' | 'cooling' | 'insufficient_data'

const DEMAND_WEIGHT_PRESETS: Record<'Stable' | 'Balanced' | 'Reactive', DemandWeights> = {
  Stable: { weight14d: 20, weight15To30d: 40, weight31To60d: 40 },
  Balanced: { weight14d: 40, weight15To30d: 40, weight31To60d: 20 },
  Reactive: { weight14d: 60, weight15To30d: 30, weight31To60d: 10 },
}

const ADJUSTMENT_MODE_LABELS: Record<AdjustmentMode, string> = {
  shrink: 'Shrink',
  min_days: 'Min days',
  cap: '2x cap',
  raw: 'Raw',
}

const INVENTORY_STATUS_DEFINITIONS: Record<InventoryStatus, { label: string; className: string; definition: string }> = {
  critical: {
    label: 'Critical',
    className: 'bg-red-500 text-white',
    definition: 'Inventory position is at or below 50% of the recommended reorder point.',
  },
  low: {
    label: 'Low',
    className: 'bg-orange-500 text-white',
    definition: 'Inventory position is between 50% and 100% of the recommended reorder point.',
  },
  warning: {
    label: 'Warning',
    className: 'bg-amber-500 text-white',
    definition: 'Inventory position is above reorder point, but no more than 115% of it.',
  },
  healthy: {
    label: 'Healthy',
    className: 'bg-blue-500 text-white',
    definition: 'Inventory position is more than 115% of reorder point, but below the target desired-level band.',
  },
  incoming: {
    label: 'Incoming',
    className: 'bg-cyan-500 text-white',
    definition: 'On hand is at or below reorder point, but on hand plus on order covers the target band.',
  },
  on_target: {
    label: 'On Target',
    className: 'bg-emerald-500 text-white',
    definition: 'Inventory position is between 80% and 120% of the recommended desired level.',
  },
  high: {
    label: 'High',
    className: 'bg-violet-500 text-white',
    definition: 'Inventory position is at least 120% of desired level, but below 150%.',
  },
  overstock: {
    label: 'Overstock',
    className: 'bg-fuchsia-600 text-white',
    definition: 'Inventory position is at least 150% of the recommended desired level.',
  },
  no_demand: {
    label: 'No Demand',
    className: 'bg-slate-500 text-white',
    definition: 'Recommended reorder point and desired level are both zero.',
  },
}

const STATUS_FILTERS: InventoryStatus[] = [
  'critical',
  'low',
  'warning',
  'healthy',
  'on_target',
  'incoming',
  'high',
  'overstock',
  'no_demand',
]

const MOMENTUM_DEFINITIONS: Record<MomentumStatus, { label: string; className: string; definition: string }> = {
  surging: {
    label: 'Surging',
    className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    definition: '14d adjusted velocity is sharply higher than both older windows.',
  },
  rising: {
    label: 'Rising',
    className: 'border-blue-200 bg-blue-50 text-blue-700',
    definition: 'Recent adjusted velocity is meaningfully higher than older demand.',
  },
  spiky: {
    label: 'Spiky',
    className: 'border-amber-200 bg-amber-50 text-amber-700',
    definition: 'A small number of recent units is creating a large short-term velocity jump.',
  },
  flat: {
    label: 'Flat',
    className: 'border-slate-200 bg-slate-50 text-slate-700',
    definition: 'Adjusted velocity is broadly steady across demand windows.',
  },
  cooling: {
    label: 'Cooling',
    className: 'border-cyan-200 bg-cyan-50 text-cyan-700',
    definition: 'Recent adjusted velocity is meaningfully lower than older demand.',
  },
  insufficient_data: {
    label: 'Limited',
    className: 'border-zinc-200 bg-zinc-50 text-zinc-600',
    definition: 'There is not enough sales or in-stock evidence to classify momentum confidently.',
  },
}

function InventoryStatusBadge({ item }: { item: any }) {
  const status = (item.inventory_status || 'no_demand') as InventoryStatus
  const config = INVENTORY_STATUS_DEFINITIONS[status] || INVENTORY_STATUS_DEFINITIONS.no_demand

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge className={cn('px-1 py-0 text-[7px] uppercase font-bold border-none cursor-help', config.className)}>
          {item.inventory_status_label || config.label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-72 space-y-1.5 p-3 text-left">
        <div className="font-bold">{item.inventory_status_label || config.label}</div>
        <div>{config.definition}</div>
        {item.inventory_status_reason && (
          <div className="text-background/80">{item.inventory_status_reason}</div>
        )}
        <div className="border-t border-background/20 pt-1 font-mono text-[10px]">
          On hand + on order = inventory position
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function MomentumBadge({ item }: { item: any }) {
  const status = (item.momentum_status || item.momentum || 'insufficient_data') as MomentumStatus
  const config = MOMENTUM_DEFINITIONS[status] || MOMENTUM_DEFINITIONS.insufficient_data

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className={cn('h-4 px-1.5 text-[7px] uppercase font-bold cursor-help', config.className)}>
          {item.momentum_label || config.label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-72 space-y-1.5 p-3 text-left">
        <div className="font-bold">{item.momentum_label || config.label}</div>
        <div>{config.definition}</div>
        {item.momentum_reason && (
          <div className="text-background/80">{item.momentum_reason}</div>
        )}
        <div className="border-t border-background/20 pt-1 font-mono text-[10px]">
          Compares adjusted daily velocity across 14d, 15-30d, and 31-60d.
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function AdjustedDemandTooltip({ period }: { period: '14d' | '30d' | '60d' }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="inline-flex cursor-help items-center"
          onClick={(event) => event.stopPropagation()}
        >
          <Info className="w-2.5 h-2.5 text-blue-500" />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-80 space-y-1.5 p-3 text-left">
        <div className="font-bold">{period} adjusted demand</div>
        <div>
          The main value is raw units sold in the last {period}. The smaller blue value is the same demand adjusted for days the item was out of stock.
        </div>
        <div className="text-background/80">
          The stockout adjustment mode controls each window before the demand weights feed reorder point, desired level, and order quantity.
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function DemandCell({
  item,
  rawKey,
  adjustedKey,
  period,
  activeKey,
  oosKey,
  distinctSaleDaysKey,
}: {
  item: any
  rawKey: string
  adjustedKey: string
  period: '14d' | '30d' | '60d'
  activeKey: string
  oosKey: string
  distinctSaleDaysKey: string
}) {
  const rawValue = item[rawKey] ?? 0
  const adjustedValue = item[adjustedKey] ?? 0
  const activeDays = item[activeKey]
  const oosDays = item[oosKey]
  const distinctSaleDays = item[distinctSaleDaysKey]

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex cursor-help flex-col items-end leading-tight">
          <span className="text-[11px]">{rawValue}</span>
          <span className="text-[9px] text-blue-500">{adjustedValue} adj</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-80 space-y-1.5 p-3 text-left">
        <div className="font-bold">{period} demand context</div>
        <div>Raw sales: <span className="font-mono">{rawValue}</span></div>
        <div>Adjusted demand: <span className="font-mono">{adjustedValue}</span></div>
        {activeDays !== undefined && (
          <div>Effective active days: <span className="font-mono">{activeDays}</span></div>
        )}
        {oosDays !== undefined && (
          <div>QOH OOS days: <span className="font-mono">{oosDays}</span></div>
        )}
        {distinctSaleDays !== undefined && (
          <div>Distinct sale days: <span className="font-mono">{distinctSaleDays}</span></div>
        )}
        <div className="border-t border-background/20 pt-1 text-background/80">
          Effective active days are capped by in-stock days, distinct sale days, and a 3-day minimum so negative inventory does not overinflate adjusted demand.
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

interface SheetsReplenishmentProps {
  data: any
  isLoading: boolean
  refetch: () => void
  forecastPeriod: number
  setForecastPeriod: (value: number) => void
  safetyDays: number
  setSafetyDays: (value: number) => void
  growthMultiplier: number
  setGrowthMultiplier: (value: number) => void
  demandWeights: DemandWeights
  setDemandWeights: (value: DemandWeights | ((previous: DemandWeights) => DemandWeights)) => void
  demandWeightTotal: number
  isDemandWeightValid: boolean
  adjustmentMode: AdjustmentMode
  setAdjustmentMode: (value: AdjustmentMode) => void
}

export function SheetsReplenishment({
  data,
  isLoading,
  refetch,
  forecastPeriod,
  setForecastPeriod,
  safetyDays,
  setSafetyDays,
  growthMultiplier,
  setGrowthMultiplier,
  demandWeights,
  setDemandWeights,
  demandWeightTotal,
  isDemandWeightValid,
  adjustmentMode,
  setAdjustmentMode,
}: SheetsReplenishmentProps) {

  const { data: session } = useSession()
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
  
  const [isPushing, setIsPushing] = useState(false)
  const [pushResult, setPushResult] = useState<{status: 'success'|'warning'|'error', msg: string} | null>(null)
  
  const [selectedLocation, setSelectedLocation] = useState('Bici Adanac')
  const [searchQuery, setSearchQuery] = useState('')
  
  // Selection State
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  
  // Filter States
  const [vendorFilter, setVendorFilter] = useState('all')
  const [brandFilter, setBrandFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  
  // Sort State
  const [sortConfig, setSortConfig] = useState<{ key: string, direction: 'asc' | 'desc' } | null>({ key: 'inventory_status_rank', direction: 'asc' })
  const [currentPage, setCurrentPage] = useState(1)
  const itemsPerPage = 50

  const locations = useMemo(() => {
    return data?.locations?.length ? data.locations : ['Bici Adanac', 'Victoria', 'Langford']
  }, [data])

  useEffect(() => {
    if (locations.length > 0 && !locations.includes(selectedLocation)) {
      setSelectedLocation(locations[0])
      setSelectedIds(new Set())
    }
  }, [locations, selectedLocation])

  // Manual Overrides State
  const [overrides, setOverrides] = useState<Record<string, {rop?: number, dl?: number}>>({})

  const allItems = useMemo(() => {
    if (!data || !data.data || !data.data[selectedLocation]) return []
    return data.data[selectedLocation]
  }, [data, selectedLocation])

  // Get unique values for filters
  const filterOptions = useMemo(() => {
    const vendors = new Set<string>()
    const brands = new Set<string>()
    const categories = new Set<string>()
    
    allItems.forEach((item: any) => {
      if (item.vendor) vendors.add(item.vendor)
      if (item.brand) brands.add(item.brand)
      if (item.category) categories.add(item.category)
    })
    
    return {
      vendors: Array.from(vendors).sort(),
      brands: Array.from(brands).sort(),
      categories: Array.from(categories).sort()
    }
  }, [allItems])

  const processedData = useMemo(() => {
    let items = [...allItems]
    
    // Search
    if (searchQuery) {
      const lower = searchQuery.toLowerCase()
      items = items.filter((item: any) => 
        String(item.description ?? '').toLowerCase().includes(lower) ||
        String(item.sku ?? '').toLowerCase().includes(lower) ||
        String(item.system_id ?? '').toLowerCase().includes(lower)
      )
    }
    
    // Filters
    if (vendorFilter !== 'all') items = items.filter((i: any) => i.vendor === vendorFilter)
    if (brandFilter !== 'all') items = items.filter((i: any) => i.brand === brandFilter)
    if (categoryFilter !== 'all') items = items.filter((i: any) => i.category === categoryFilter)
    if (statusFilter !== 'all') items = items.filter((i: any) => i.inventory_status === statusFilter)
    
    // Sorting
    if (sortConfig) {
      items.sort((a, b) => {
        let aVal = a[sortConfig.key]
        let bVal = b[sortConfig.key]
        
        if (typeof aVal === 'string') aVal = aVal.toLowerCase()
        if (typeof bVal === 'string') bVal = bVal.toLowerCase()
        
        if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1
        if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1
        return 0
      })
    }
    
    return items
  }, [allItems, searchQuery, vendorFilter, brandFilter, categoryFilter, statusFilter, sortConfig])

  // Pagination Logic
  const totalPages = Math.ceil(processedData.length / itemsPerPage)
  const paginatedData = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage
    return processedData.slice(start, start + itemsPerPage)
  }, [processedData, currentPage])

  // Reset to page 1 when filters change
  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, vendorFilter, brandFilter, categoryFilter, statusFilter, sortConfig])

  const toggleSelectAll = () => {
    if (selectedIds.size === processedData.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(processedData.map(i => i.system_id)))
    }
  }

  const toggleSelectItem = (id: string) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelectedIds(next)
  }

  const handlePush = async () => {
    const selectedItems = processedData.filter((i: any) => selectedIds.has(i.system_id))
    
    // Apply manual overrides to selected items
    const itemsToPush = selectedItems.map(item => {
      const override = overrides[item.system_id]
      return {
        ...item,
        recommended_reorder_point: override?.rop ?? item.recommended_reorder_point,
        recommended_desired_level: override?.dl ?? item.recommended_desired_level
      }
    })

    if (itemsToPush.length === 0) return alert("Please select at least one SKU to push.")
    
    if (!confirm(`Pushing updates for ${itemsToPush.length} selected SKUs to Lightspeed. Continue?`)) return

    setIsPushing(true)
    setPushResult(null)
    try {
      const response = await fetch(`${baseUrl}/api/replenishment/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(itemsToPush.map(item => ({
          ...item,
          pushed_by: session?.user?.email ?? session?.user?.name ?? 'Unknown User'
        })))
      })
      if (response.ok) {
        const data = await response.json()
        const results = data.results || []
        const failedCount = results.filter((r: any) => !r.success).length
        const total = itemsToPush.length
        
        if (failedCount === 0) {
          setPushResult({ status: 'success', msg: `Successfully Pushed ${total} SKUs` })
          setSelectedIds(new Set())
        } else if (failedCount === total) {
          setPushResult({ status: 'error', msg: `All ${total} Pushes Failed` })
        } else {
          setPushResult({ status: 'warning', msg: `${failedCount} of ${total} Pushes Failed` })
        }
        
        setTimeout(() => setPushResult(null), 6000)
        refetch()
      } else {
        setPushResult({ status: 'error', msg: `Server Error: ${response.status}` })
        setTimeout(() => setPushResult(null), 6000)
      }
    } catch (error) {
      console.error("Push failed:", error)
      setPushResult({ status: 'error', msg: "Network Error" })
      setTimeout(() => setPushResult(null), 6000)
    } finally {
      setIsPushing(false)
    }
  }

  const [isAddingToManaged, setIsAddingToManaged] = useState(false)
  const handleAddToManaged = async () => {
    const selectedItems = processedData.filter((i: any) => selectedIds.has(i.system_id))
    if (selectedItems.length === 0) return alert("Please select at least one SKU to add to the managed list.")
    
    setIsAddingToManaged(true)
    try {
      const itemsToAdd = selectedItems.map((item: any) => ({
        system_id: item.system_id,
        sku: item.sku,
        description: item.description,
        brand: item.brand,
        vendor: item.vendor,
        category: item.category,
        added_by: session?.user?.email ?? session?.user?.name ?? 'Unknown User'
      }))

      const response = await fetch(`${baseUrl}/api/skus/add-bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(itemsToAdd)
      })

      if (response.ok) {
        const data = await response.json()
        toast.success(`Added ${data.added} SKUs to Managed List`)
        setSelectedIds(new Set())
      } else {
        toast.error(`Server Error: ${response.status}`)
      }
    } catch (error) {
      console.error("Add to managed failed:", error)
      toast.error("Network Error")
    } finally {
      setIsAddingToManaged(false)
    }
  }

  const requestSort = (key: string) => {
    let direction: 'asc' | 'desc' = 'asc'
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc'
    }
    setSortConfig({ key, direction })
  }

  return (
    <div className="flex h-[calc(100vh-96px)] overflow-hidden gap-3">
      {/* Sidebar */}
      <div className="w-64 flex flex-col gap-2 bg-card rounded-xl border p-4 shadow-sm overflow-y-auto">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
          <MapPin className="w-3 h-3" /> Locations
        </h3>
        <div className="space-y-1 mb-6">
          {locations.map((loc: string) => (
            <Button
              key={loc}
              variant={selectedLocation === loc ? "default" : "ghost"}
              className={cn(
                "w-full justify-start gap-3 h-11 transition-all duration-200",
                selectedLocation === loc ? "shadow-md scale-[1.02]" : "hover:bg-accent"
              )}
              onClick={() => {
                setSelectedLocation(loc)
                setSelectedIds(new Set())
              }}
            >
              <Store className="w-4 h-4" />
              {loc}
              {data?.data?.[loc]?.length > 0 && (
                <span className="ml-auto text-[10px] bg-muted px-1.5 py-0.5 rounded-full text-muted-foreground">
                  {data.data[loc].length}
                </span>
              )}
            </Button>
          ))}
        </div>

        <div className="space-y-3 pt-4 border-t">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
            <Settings2 className="w-3 h-3" /> Controls
          </h3>
          
          <div className="rounded-lg border bg-card/60 p-3 space-y-2.5">
            <div className="flex items-start justify-between gap-3">
              <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
                <Calendar className="w-3 h-3" /> Forecast Period
              </label>
              <span className="text-xs font-mono font-semibold">{forecastPeriod}d</span>
            </div>
            <div className="flex items-center gap-3">
              <input 
                type="range" min="7" max="180" step="1"
                value={forecastPeriod} 
                onChange={(e) => setForecastPeriod(parseInt(e.target.value))}
                className="flex-1 accent-blue-600"
              />
            </div>
            <p className="text-[10px] leading-snug text-muted-foreground">
              Target days of inventory for desired level.
            </p>
          </div>

          <div className="rounded-lg border bg-card/60 p-3 space-y-2.5">
            <div className="flex items-start justify-between gap-3">
              <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
                <ShieldCheck className="w-3 h-3" /> Safety Days
              </label>
              <span className="text-xs font-mono font-semibold">{safetyDays}d</span>
            </div>
            <div className="flex items-center gap-3">
              <input 
                type="range" min="0" max="30" step="1"
                value={safetyDays} 
                onChange={(e) => setSafetyDays(parseInt(e.target.value))}
                className="flex-1 accent-blue-600"
              />
            </div>
            <p className="text-[10px] leading-snug text-muted-foreground">
              Extra demand buffer added to reorder point.
            </p>
          </div>

          <div className="rounded-lg border bg-card/60 p-3 space-y-2.5">
            <div className="flex items-start justify-between gap-3">
              <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
                <TrendingUp className="w-3 h-3" /> Growth Multiplier
              </label>
              <span className="text-xs font-mono font-semibold">{growthMultiplier.toFixed(2)}x</span>
            </div>
            <div className="flex items-center gap-2">
              <Input 
                type="number" step="0.05"
                value={growthMultiplier} 
                onChange={(e) => setGrowthMultiplier(parseFloat(e.target.value) || 1.0)}
                className="h-8 bg-background border-muted text-xs"
              />
            </div>
            <p className="text-[10px] leading-snug text-muted-foreground">
              Scales forward-looking ROP, DL, and order quantity.
            </p>
          </div>

          <div className="rounded-lg border bg-card/60 p-3 space-y-2.5">
            <div className="flex items-start justify-between gap-3">
              <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
                <TrendingUp className="w-3 h-3" /> Demand Weighting
              </label>
              <span className={cn(
                "text-xs font-mono font-semibold",
                !isDemandWeightValid && "text-red-600"
              )}>
                {demandWeightTotal}%
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1">
              {Object.entries(DEMAND_WEIGHT_PRESETS).map(([label, weights]) => (
                <Button
                  key={label}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 px-1 text-[9px]"
                  onClick={() => setDemandWeights(weights)}
                >
                  {label}
                </Button>
              ))}
            </div>

            <div className="space-y-2">
              {[
                ['weight14d', 'Last 14d', demandWeights.weight14d],
                ['weight15To30d', 'Days 15-30', demandWeights.weight15To30d],
                ['weight31To60d', 'Days 31-60', demandWeights.weight31To60d],
              ].map(([key, label, value]) => (
                <div key={key as string} className="space-y-1">
                  <div className="flex items-center justify-between gap-2 text-[10px] font-medium">
                    <span>{label}</span>
                    <Input
                      type="number"
                      min="0"
                      max="100"
                      step="5"
                      value={value as number}
                      onChange={(event) => {
                        const nextValue = Math.max(0, Math.min(100, parseInt(event.target.value) || 0))
                        setDemandWeights((previous) => ({
                          ...previous,
                          [key as keyof DemandWeights]: nextValue,
                        }))
                      }}
                      className="h-6 w-14 px-1 text-right text-[10px] font-mono"
                    />
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="5"
                    value={value as number}
                    onChange={(event) => {
                      const nextValue = parseInt(event.target.value)
                      setDemandWeights((previous) => ({
                        ...previous,
                        [key as keyof DemandWeights]: nextValue,
                      }))
                    }}
                    className="w-full accent-blue-600"
                  />
                </div>
              ))}
            </div>

            {!isDemandWeightValid && (
              <div className="rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-[10px] font-medium text-red-700">
                Weights must total 100% before recommendations refresh.
              </div>
            )}
            <p className="text-[10px] leading-snug text-muted-foreground">
              Blends adjusted 14d, 15-30d, and 31-60d velocity.
            </p>
          </div>

          <div className="rounded-lg border bg-card/60 p-3 space-y-2.5">
            <div className="flex items-start justify-between gap-3">
              <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
                <Info className="w-3 h-3" /> Stockout Adjustment
              </label>
              <span className="text-xs font-mono font-semibold">{ADJUSTMENT_MODE_LABELS[adjustmentMode]}</span>
            </div>
            <Select
              value={adjustmentMode}
              onValueChange={(value) => setAdjustmentMode(value as AdjustmentMode)}
            >
              <SelectTrigger className="h-8 text-xs bg-background border-muted">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="shrink">Shrink adjustment</SelectItem>
                <SelectItem value="min_days">Minimum days rule</SelectItem>
                <SelectItem value="cap">Hard cap multiplier</SelectItem>
                <SelectItem value="raw">Raw adjustment</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[10px] leading-snug text-muted-foreground">
              Controls the smaller adjusted demand value and the recommendation math.
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 flex flex-col gap-2 min-w-0">
        {/* Filter Bar */}
        <div className="flex items-center gap-2 bg-card p-2 rounded-xl border shadow-sm flex-wrap">
          <div className="relative w-full sm:w-[360px] xl:w-[430px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input 
              placeholder="Search by Description or SKU..." 
              className="pl-9 bg-muted/50 border-none h-9 focus-visible:ring-1"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <Filter className="w-3.5 h-3.5 text-muted-foreground mx-1 hidden sm:block" />

            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-8 w-[140px] text-[10px] bg-muted/30">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                {STATUS_FILTERS.map((status) => (
                  <SelectItem key={status} value={status}>
                    {INVENTORY_STATUS_DEFINITIONS[status].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={vendorFilter} onValueChange={setVendorFilter}>
              <SelectTrigger className="h-8 w-[140px] text-[10px] bg-muted/30">
                <SelectValue placeholder="Vendor" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Vendors</SelectItem>
                {filterOptions.vendors.map(v => <SelectItem key={v} value={v}>{v}</SelectItem>)}
              </SelectContent>
            </Select>

            <Select value={brandFilter} onValueChange={setBrandFilter}>
              <SelectTrigger className="h-8 w-[120px] text-[10px] bg-muted/30">
                <SelectValue placeholder="Brand" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Brands</SelectItem>
                {filterOptions.brands.map(b => <SelectItem key={b} value={b}>{b}</SelectItem>)}
              </SelectContent>
            </Select>

            <Select value={categoryFilter} onValueChange={setCategoryFilter}>
              <SelectTrigger className="h-8 w-[140px] text-[10px] bg-muted/30">
                <SelectValue placeholder="Category" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Categories</SelectItem>
                {filterOptions.categories.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>

            {(statusFilter !== 'all' || vendorFilter !== 'all' || brandFilter !== 'all' || categoryFilter !== 'all') && (
              <Button 
                variant="ghost" 
                size="sm" 
                className="h-8 text-[10px] px-2 text-red-500 hover:text-red-600 hover:bg-red-50"
                onClick={() => {
                  setStatusFilter('all'); setVendorFilter('all'); setBrandFilter('all'); setCategoryFilter('all');
                }}
              >
                Clear Filters
              </Button>
            )}
        </div>

        {/* Actions Bar */}
        <div className="flex items-center justify-between bg-card rounded-xl border px-3 py-2 shadow-sm mb-1">
          <div className="flex items-center gap-3">
            <Badge variant="outline" className="px-3 py-1 text-sm font-semibold border-blue-200 bg-blue-50 text-blue-700">
              {selectedIds.size} / {processedData.length} Selected
            </Badge>
            <span className="text-xs text-muted-foreground font-medium">
               Ready to push to Lightspeed
            </span>
          </div>
          <div className="flex items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              className={cn(
                "h-9 text-sm px-4 font-semibold transition-all rounded-full",
                selectedIds.size === 0 && "opacity-50 grayscale cursor-not-allowed"
              )}
              onClick={handleAddToManaged}
              disabled={isAddingToManaged || selectedIds.size === 0}
            >
              {isAddingToManaged ? (
                <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <BookmarkPlus className="w-4 h-4 mr-2 text-indigo-600" />
              )}
              Add to Managed
            </Button>
            
            <Button 
              size="sm" 
              variant={pushResult?.status === 'success' ? "secondary" : "default"} 
              className={cn(
                "h-9 text-sm px-6 font-bold shadow-md transition-all rounded-full",
                pushResult?.status === 'success' && "bg-emerald-500 text-white hover:bg-emerald-600",
                pushResult?.status === 'warning' && "bg-amber-500 text-white hover:bg-amber-600",
                pushResult?.status === 'error' && "bg-red-500 text-white hover:bg-red-600",
                !pushResult && "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 hover:shadow-lg",
                selectedIds.size === 0 && !pushResult && "opacity-50 grayscale cursor-not-allowed"
              )}
              onClick={handlePush}
              disabled={isPushing || pushResult?.status === 'success' || (selectedIds.size === 0 && !pushResult)}
            >
              {isPushing ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  Pushing {selectedIds.size} SKUs...
                </>
              ) : pushResult ? (
                <>
                  {pushResult.status === 'success' && <CircleCheck className="w-4 h-4 mr-2" />}
                  {pushResult.status !== 'success' && <CircleAlert className="w-4 h-4 mr-2" />}
                  {pushResult.msg}
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 mr-2 fill-current" />
                  Push to Lightspeed ({selectedIds.size})
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Table Area */}
        <div className="flex-1 bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col">
          <div className="overflow-auto flex-1">
            <Table wrapperClassName="overflow-visible">
              <TableHeader className="bg-muted/95 sticky top-0 z-10 backdrop-blur border-b shadow-sm">
                <TableRow className="hover:bg-transparent border-none">
                  <TableHead className="w-[34px] px-2">
                    <input 
                      type="checkbox" 
                      className="rounded border-muted-foreground/30 accent-blue-600"
                      checked={processedData.length > 0 && selectedIds.size === processedData.length}
                      onChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead className="w-[54px]">
                    <button onClick={() => requestSort('lead_time')} className="flex items-center gap-1 hover:text-foreground text-[9px] font-bold uppercase">
                      Lead <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[66px]">
                    <button onClick={() => requestSort('inventory_status_rank')} className="flex items-center gap-1 hover:text-foreground text-[9px] font-bold uppercase">
                      Status <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[330px]">
                    <button onClick={() => requestSort('description')} className="flex items-center gap-1 hover:text-foreground text-[9px] font-bold uppercase">
                      Item Description <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[62px] text-right bg-blue-50/20">
                    <button
                      onClick={() => requestSort('raw_units_sold_14d')}
                      className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase"
                    >
                      14d <AdjustedDemandTooltip period="14d" /> <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[62px] text-right bg-blue-50/20">
                    <button
                      onClick={() => requestSort('raw_units_sold_30d')}
                      className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase"
                    >
                      30d <AdjustedDemandTooltip period="30d" /> <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[62px] text-right bg-blue-50/20 border-r">
                    <button
                      onClick={() => requestSort('raw_units_sold_60d')}
                      className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase"
                    >
                      60d <AdjustedDemandTooltip period="60d" /> <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[38px] text-right">
                    <button onClick={() => requestSort('on_hand')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      QOH <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[38px] text-right">
                    <button onClick={() => requestSort('on_order')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      QOO <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[48px] text-right">
                    <button onClick={() => requestSort('days_stock')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      Cover <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[74px] text-right bg-emerald-50/30 dark:bg-emerald-900/10 font-bold border-x">
                    <button onClick={() => requestSort('qty_to_order')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      Order <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[48px] text-right text-blue-600">
                    <button onClick={() => requestSort('recommended_reorder_point')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase text-wrap leading-[1]">
                      ROP <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[48px] text-right text-purple-600">
                    <button onClick={() => requestSort('recommended_desired_level')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase text-wrap leading-[1]">
                      DL <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 15 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 13 }).map((_, j) => (
                        <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : paginatedData.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={13} className="h-64 text-center">
                      <div className="flex flex-col items-center gap-2 text-muted-foreground">
                        <CircleAlert className="w-8 h-8 opacity-20" />
                        <p className="font-semibold">No matches found in synced product data.</p>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : (
                  paginatedData.map((item: any) => (
                    <TableRow 
                      key={`${item.system_id}-${item.location}`} 
                      className={cn(
                        "group hover:bg-muted/30 transition-colors border-b h-12",
                        selectedIds.has(item.system_id) && "bg-blue-50/30"
                      )}
                    >
                      <TableCell className="px-2">
                        <input 
                          type="checkbox" 
                          className="rounded border-muted-foreground/30 accent-blue-600"
                          checked={selectedIds.has(item.system_id)}
                          onChange={() => toggleSelectItem(item.system_id)}
                        />
                      </TableCell>
                      <TableCell className="text-[10px] font-mono font-medium text-muted-foreground">
                        {item.lead_time}d
                      </TableCell>
                      <TableCell>
                        <InventoryStatusBadge item={item} />
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col w-[330px]">
                          <a 
                            href={`${baseUrl}/api/replenishment/ls-link/${item.lightspeed_item_id ?? item.system_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-semibold text-xs leading-tight mb-0.5 hover:text-blue-600 hover:underline transition-all truncate"
                            title={item.description}
                          >
                            {item.description}
                          </a>
                          <div className="flex items-center gap-1.5 text-[8px] text-muted-foreground">
                            <span className="bg-muted px-1 rounded font-mono truncate">{item.sku}</span>
                            <span className="font-medium text-foreground/70 truncate">{item.vendor}</span>
                            <MomentumBadge item={item} />
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums bg-blue-50/5">
                        <DemandCell
                          item={item}
                          rawKey="raw_units_sold_14d"
                          adjustedKey="forecast_14d"
                          period="14d"
                          activeKey="active_days_14"
                          oosKey="days_out_of_stock_14"
                          distinctSaleDaysKey="distinct_sale_days_14"
                        />
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums bg-blue-50/5">
                        <DemandCell
                          item={item}
                          rawKey="raw_units_sold_30d"
                          adjustedKey="forecast_30d"
                          period="30d"
                          activeKey="active_days_30"
                          oosKey="days_out_of_stock_30"
                          distinctSaleDaysKey="distinct_sale_days_30"
                        />
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums bg-blue-50/5 border-r">
                        <DemandCell
                          item={item}
                          rawKey="raw_units_sold_60d"
                          adjustedKey="forecast_60d"
                          period="60d"
                          activeKey="active_days_60"
                          oosKey="days_out_of_stock_60"
                          distinctSaleDaysKey="distinct_sale_days_60"
                        />
                      </TableCell>
                      <TableCell className="text-right font-mono text-[11px] tabular-nums">
                        <span className={cn(item.on_hand <= item.recommended_reorder_point && item.recommended_reorder_point > 0 && "text-red-500 font-bold")}>
                          {item.on_hand}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-[11px] tabular-nums text-muted-foreground">
                        {item.on_order}
                      </TableCell>
                      <TableCell className="text-right font-mono text-[11px] tabular-nums">
                        <span className={cn(
                          item.days_stock <= 10 && item.daily_sales > 0 ? "text-red-500 font-bold" : "text-muted-foreground"
                        )}>
                          {item.days_stock}d
                        </span>
                      </TableCell>
                      <TableCell className={cn(
                        "text-right border-x transition-colors duration-200",
                        item.qty_to_order > 0 && (item.on_hand + item.on_order) > item.recommended_reorder_point 
                          ? "bg-orange-50/20" 
                          : "bg-emerald-50/5"
                      )}>
                        <div className="flex items-center justify-end gap-1 relative group">
                          {item.qty_to_order > 0 && (item.on_hand + item.on_order) > item.recommended_reorder_point && (
                            <div title="Pipeline Alert: On-hand + On-order is greater than the Reorder Point, so Lightspeed will not automatically reorder this item. However, there is a deficit against your Desired Level.">
                              <AlertTriangle className="w-3.5 h-3.5 text-orange-500 animate-pulse" />
                            </div>
                          )}
                          <span className={cn(
                            "font-bold font-mono text-[11px] tabular-nums",
                            item.qty_to_order > 0 && (item.on_hand + item.on_order) > item.recommended_reorder_point 
                              ? "text-orange-600" 
                              : item.qty_to_order > 0 
                                ? "text-emerald-600" 
                                : "text-muted-foreground/20"
                          )}>
                            {item.qty_to_order}
                          </span>
                        </div>
                      </TableCell>
                      
                      <TableCell className="text-right">
                        <Input 
                          value={overrides[item.system_id]?.rop ?? item.recommended_reorder_point}
                          onChange={(e) => {
                            const val = parseInt(e.target.value) || 0
                            setOverrides(prev => ({
                              ...prev,
                              [item.system_id]: { ...prev[item.system_id], rop: val }
                            }))
                          }}
                          className={cn(
                            "h-5 w-12 ml-auto text-right text-[11px] font-mono tabular-nums bg-white border-blue-100 p-0 px-1",
                            overrides[item.system_id]?.rop !== undefined && "bg-amber-50 border-amber-400 font-bold"
                          )}
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <Input 
                          value={overrides[item.system_id]?.dl ?? item.recommended_desired_level}
                          onChange={(e) => {
                            const val = parseInt(e.target.value) || 0
                            setOverrides(prev => ({
                              ...prev,
                              [item.system_id]: { ...prev[item.system_id], dl: val }
                            }))
                          }}
                          className={cn(
                            "h-5 w-12 ml-auto text-right text-[11px] font-mono tabular-nums bg-white border-purple-100 p-0 px-1",
                            overrides[item.system_id]?.dl !== undefined && "bg-amber-50 border-amber-400 font-bold"
                          )}
                        />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          
          <div className="p-3 bg-muted/20 border-t flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-[10px] text-muted-foreground uppercase font-bold">
                {selectedLocation} • {processedData.length} Items Total
              </span>
              <span className="text-[9px] text-muted-foreground/60 italic">
                {selectedIds.size} Items Selected for Push
              </span>
            </div>
            
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 px-2"
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
              >
                Previous
              </Button>
              <div className="text-[10px] font-bold text-muted-foreground min-w-[80px] text-center">
                Page {currentPage} of {totalPages || 1}
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-8 px-2"
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages || totalPages === 0}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
