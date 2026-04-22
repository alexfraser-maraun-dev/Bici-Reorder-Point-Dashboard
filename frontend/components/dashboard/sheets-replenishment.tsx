'use client'

import { useState, useMemo } from 'react'
import { useReplenishmentData } from '@/lib/hooks'
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { 
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { 
  Store, 
  AlertCircle, 
  MapPin,
  RefreshCw,
  Search,
  Settings2,
  Calendar,
  ShieldCheck,
  ArrowUpDown,
  Filter,
  TrendingUp,
  TrendingDown,
  Minus,
  ShoppingCart,
  Zap,
  CheckCircle2
} from 'lucide-react'
import { cn } from '@/lib/utils'

export function SheetsReplenishment() {
  const [forecastPeriod, setForecastPeriod] = useState(60)
  const [safetyDays, setSafetyDays] = useState(7)
  const [growthMultiplier, setGrowthMultiplier] = useState(1.0)
  const { data, isLoading, refetch } = useReplenishmentData(forecastPeriod, safetyDays, growthMultiplier)
  
  const [isPushing, setIsPushing] = useState(false)
  const [pushSuccess, setPushSuccess] = useState(false)
  
  const [selectedLocation, setSelectedLocation] = useState('Bici Adanac')
  const [searchQuery, setSearchQuery] = useState('')
  
  // Selection State
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  
  // Filter States
  const [vendorFilter, setVendorFilter] = useState('all')
  const [brandFilter, setBrandFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  
  // Sort State
  const [sortConfig, setSortConfig] = useState<{key: string, direction: 'asc' | 'desc'} | null>(null)

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
        item.description.toLowerCase().includes(lower) || 
        item.sku.toLowerCase().includes(lower) ||
        item.system_id.toLowerCase().includes(lower)
      )
    }
    
    // Filters
    if (vendorFilter !== 'all') items = items.filter((i: any) => i.vendor === vendorFilter)
    if (brandFilter !== 'all') items = items.filter((i: any) => i.brand === brandFilter)
    if (categoryFilter !== 'all') items = items.filter((i: any) => i.category === categoryFilter)
    if (statusFilter !== 'all') items = items.filter((i: any) => String(i.urgency) === statusFilter)
    
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
    const itemsToPush = processedData.filter((i: any) => selectedIds.has(i.system_id))
    if (itemsToPush.length === 0) return alert("Please select at least one SKU to push.")
    
    if (!confirm(`Pushing updates for ${itemsToPush.length} selected SKUs to Lightspeed. Continue?`)) return

    setIsPushing(true)
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'
      const response = await fetch(`${baseUrl}/api/replenishment/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(itemsToPush)
      })
      if (response.ok) {
        setPushSuccess(true)
        setSelectedIds(new Set())
        setTimeout(() => setPushSuccess(false), 5000)
        refetch()
      }
    } catch (error) {
      console.error("Push failed:", error)
    } finally {
      setIsPushing(false)
    }
  }

  const requestSort = (key: string) => {
    let direction: 'asc' | 'desc' = 'asc'
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc'
    }
    setSortConfig({ key, direction })
  }

  const getUrgencyColor = (urgency: number) => {
    switch (urgency) {
      case 5: return 'bg-red-500 text-white'
      case 4: return 'bg-orange-500 text-white'
      case 3: return 'bg-amber-500 text-white'
      case 2: return 'bg-blue-500 text-white'
      default: return 'bg-emerald-500 text-white'
    }
  }

  const getUrgencyLabel = (urgency: number) => {
    switch (urgency) {
      case 5: return 'Critical'
      case 4: return 'Low Stock'
      case 3: return 'Warning'
      case 2: return 'Healthy'
      default: return 'Optimal'
    }
  }

  return (
    <div className="flex h-[calc(100vh-160px)] overflow-hidden gap-4">
      {/* Sidebar */}
      <div className="w-64 flex flex-col gap-2 bg-card rounded-xl border p-4 shadow-sm overflow-y-auto">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
          <MapPin className="w-3 h-3" /> Locations
        </h3>
        <div className="space-y-1 mb-6">
          {['Bici Adanac', 'Victoria', 'Langford'].map((loc) => (
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

        <div className="space-y-4 pt-4 border-t">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
            <Settings2 className="w-3 h-3" /> Controls
          </h3>
          
          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
              <Calendar className="w-3 h-3" /> Forecast Period
            </label>
            <div className="flex items-center gap-3">
              <input 
                type="range" min="7" max="180" step="1"
                value={forecastPeriod} 
                onChange={(e) => setForecastPeriod(parseInt(e.target.value))}
                className="flex-1 accent-blue-600"
              />
              <span className="text-xs font-mono w-8">{forecastPeriod}d</span>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
              <ShieldCheck className="w-3 h-3" /> Safety Days
            </label>
            <div className="flex items-center gap-3">
              <input 
                type="range" min="0" max="30" step="1"
                value={safetyDays} 
                onChange={(e) => setSafetyDays(parseInt(e.target.value))}
                className="flex-1 accent-blue-600"
              />
              <span className="text-xs font-mono w-8">{safetyDays}d</span>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase text-muted-foreground flex items-center gap-1.5">
              <TrendingUp className="w-3 h-3" /> Growth Multiplier
            </label>
            <div className="flex items-center gap-3">
              <Input 
                type="number" step="0.05"
                value={growthMultiplier} 
                onChange={(e) => setGrowthMultiplier(parseFloat(e.target.value) || 1.0)}
                className="h-8 bg-background border-muted text-xs"
              />
              <span className="text-[10px] font-medium text-muted-foreground">x</span>
            </div>
          </div>

          <Button 
            variant="secondary" 
            className="w-full gap-2 text-xs font-semibold border mt-2" 
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className={cn("w-3 h-3", isLoading && "animate-spin")} />
            {isLoading ? "Syncing..." : "Sync Google Sheets"}
          </Button>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col gap-4 min-w-0">
        {/* Filter Bar */}
        <div className="flex flex-col gap-3 bg-card p-3 rounded-xl border shadow-sm">
          <div className="flex items-center justify-between gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input 
                placeholder="Search by Description or SKU..." 
                className="pl-9 bg-muted/50 border-none h-10 focus-visible:ring-1"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          </div>
          
          <div className="flex items-center gap-2 flex-wrap border-t pt-3">
            <Filter className="w-3.5 h-3.5 text-muted-foreground mr-1" />
            
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-8 w-[120px] text-[10px] bg-muted/30">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value="5">Critical Only</SelectItem>
                <SelectItem value="4">Low Stock</SelectItem>
                <SelectItem value="3">Warning</SelectItem>
                <SelectItem value="2">Healthy</SelectItem>
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
        </div>

        {/* Table Area */}
        <div className="flex-1 bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col">
          <div className="overflow-auto flex-1">
            <Table>
              <TableHeader className="bg-muted/50 sticky top-0 z-10 backdrop-blur-sm">
                <TableRow className="hover:bg-transparent border-none">
                  <TableHead className="w-[40px] px-2">
                    <input 
                      type="checkbox" 
                      className="rounded border-muted-foreground/30 accent-blue-600"
                      checked={processedData.length > 0 && selectedIds.size === processedData.length}
                      onChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead className="w-[70px]">
                    <button onClick={() => requestSort('urgency')} className="flex items-center gap-1 hover:text-foreground text-[9px] font-bold uppercase">
                      Status <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button onClick={() => requestSort('description')} className="flex items-center gap-1 hover:text-foreground text-[9px] font-bold uppercase">
                      Product Info <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[80px] text-right bg-blue-50/20 dark:bg-blue-900/10">
                    <button onClick={() => requestSort('forecast_30d')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      30d Sales <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[50px] text-right text-[9px] font-bold uppercase">QOH</TableHead>
                  <TableHead className="w-[50px] text-right text-[9px] font-bold uppercase">QOO</TableHead>
                  <TableHead className="w-[60px] text-right text-[9px] font-bold uppercase">Days</TableHead>
                  <TableHead className="w-[80px] text-right bg-emerald-50/30 dark:bg-emerald-900/10 font-bold">
                    <button onClick={() => requestSort('qty_to_order')} className="flex items-center gap-1 ml-auto hover:text-foreground text-[9px] font-bold uppercase">
                      Order <ArrowUpDown className="w-2.5 h-2.5" />
                    </button>
                  </TableHead>
                  <TableHead className="w-[60px] text-right text-[9px] font-bold uppercase text-blue-600">Rec ROP</TableHead>
                  <TableHead className="w-[60px] text-right text-[9px] font-bold uppercase text-purple-600">Rec DL</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 15 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 10 }).map((_, j) => (
                        <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : processedData.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={10} className="h-64 text-center">
                      <div className="flex flex-col items-center gap-2 text-muted-foreground">
                        <AlertCircle className="w-8 h-8 opacity-20" />
                        <p className="font-semibold">No matches found in Google Sheets.</p>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : (
                  processedData.map((item: any) => (
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
                      <TableCell>
                        <Badge className={cn("px-1 py-0 text-[7px] uppercase font-bold border-none", getUrgencyColor(item.urgency))}>
                          {getUrgencyLabel(item.urgency)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col max-w-[200px]">
                          <span className="font-semibold text-xs leading-tight mb-0.5 truncate">{item.description}</span>
                          <div className="flex items-center gap-1.5 text-[8px] text-muted-foreground">
                            <span className="bg-muted px-1 rounded font-mono truncate">{item.sku}</span>
                            <span className="truncate font-medium text-foreground/70">{item.brand}</span>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums bg-blue-50/5">
                        <div className="flex items-center justify-end gap-1">
                          {item.momentum === 'increasing' && <TrendingUp className="w-2 h-2 text-emerald-500" />}
                          {item.momentum === 'decreasing' && <TrendingDown className="w-2 h-2 text-red-500" />}
                          {item.forecast_30d}
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">
                        <span className={cn(item.on_hand <= item.recommended_reorder_point && item.recommended_reorder_point > 0 && "text-red-500 font-bold")}>
                          {item.on_hand}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-[10px] tabular-nums text-muted-foreground">
                        {item.on_order}
                      </TableCell>
                      <TableCell className="text-right font-mono text-[10px] tabular-nums">
                        <span className={cn(
                          item.days_stock <= 10 && item.daily_sales > 0 ? "text-red-500 font-bold" : "text-muted-foreground"
                        )}>
                          {item.days_stock}d
                        </span>
                      </TableCell>
                      <TableCell className="text-right bg-emerald-50/5">
                        <div className="flex items-center justify-end gap-1">
                          <span className={cn(
                            "font-bold font-mono text-xs",
                            item.qty_to_order > 0 ? "text-emerald-600" : "text-muted-foreground/20"
                          )}>
                            {item.qty_to_order}
                          </span>
                        </div>
                      </TableCell>
                      
                      <TableCell className="text-right">
                        <Input 
                          defaultValue={item.recommended_reorder_point}
                          className="h-5 w-10 ml-auto text-right text-[10px] bg-white border-blue-100 p-0"
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <Input 
                          defaultValue={item.recommended_desired_level}
                          className="h-5 w-10 ml-auto text-right text-[10px] bg-white border-purple-100 p-0"
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
            <div className="flex gap-2">
              <Button 
                size="sm" 
                variant={pushSuccess ? "secondary" : "default"} 
                className={cn(
                  "h-8 text-xs px-6 font-bold shadow-lg transition-all",
                  pushSuccess ? "bg-emerald-500 text-white" : "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700",
                  selectedIds.size === 0 && !pushSuccess && "opacity-50 grayscale cursor-not-allowed"
                )}
                onClick={handlePush}
                disabled={isPushing || pushSuccess || (selectedIds.size === 0 && !pushSuccess)}
              >
                {isPushing ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 mr-2 animate-spin" />
                    Pushing {selectedIds.size} SKUs...
                  </>
                ) : pushSuccess ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5 mr-2" />
                    Pushed Successfully!
                  </>
                ) : (
                  <>
                    <Zap className="w-3.5 h-3.5 mr-2" />
                    Push {selectedIds.size > 0 ? selectedIds.size : 'Selected'} to Lightspeed
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
