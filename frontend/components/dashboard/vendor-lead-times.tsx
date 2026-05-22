'use client'

import { useMemo, useState } from 'react'
import { useActiveVendorLeadTimes } from '@/lib/hooks'
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Truck, AlertCircle, Search, RefreshCw } from 'lucide-react'

const LOCATION_COLUMNS = [
  { id: 3, key: 'adanac', label: 'Adanac' },
  { id: 20, key: 'langford', label: 'Langford' },
  { id: 2, key: 'victoria', label: 'Victoria' },
]

function formatDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function getLocationLead(vendor: any, locationId: number) {
  return (vendor.location_lead_times || []).find((entry: any) => Number(entry.location_id) === locationId)
}

export function VendorLeadTimes() {
  const { data, isLoading, error, refetch } = useActiveVendorLeadTimes()
  const [search, setSearch] = useState('')

  const leadTimes = data?.data || []
  const meta = data?.meta
  const filteredLeadTimes = useMemo(() => {
    const searchLower = search.trim().toLowerCase()
    if (!searchLower) return leadTimes
    return leadTimes.filter((vendor: any) => {
      const configuredBrands = (vendor.configured_brands || []).join(' ').toLowerCase()
      return (
        String(vendor.vendor_name || '').toLowerCase().includes(searchLower) ||
        String(vendor.vendor_id || '').toLowerCase().includes(searchLower) ||
        configuredBrands.includes(searchLower)
      )
    })
  }, [leadTimes, search])

  return (
    <div className="bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col h-full animate-in fade-in duration-500">
      <div className="p-4 border-b bg-muted/20 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Truck className="w-5 h-5 text-blue-600" />
          <div>
            <h2 className="font-semibold text-lg">Active Vendor Lead Times</h2>
            <p className="text-xs text-muted-foreground">
              Vendors with a received lead-time sample in the past 90 days
            </p>
          </div>
        </div>
        <div className="relative w-full max-w-sm">
          <Search className="text-muted-foreground absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search vendor, ID, or mapped brand..."
            className="h-9 pl-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isLoading}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>
      {meta?.warnings?.length > 0 && (
        <div className="border-b bg-amber-50 px-4 py-2 text-xs text-amber-800">
          Vendor data loaded with {meta.warnings.length} warning{meta.warnings.length === 1 ? '' : 's'}.
        </div>
      )}
      
      <div className="overflow-auto flex-1">
        <Table>
          <TableHeader className="bg-muted/50 sticky top-0 z-10 backdrop-blur-sm">
            <TableRow>
              <TableHead className="min-w-[230px]">Vendor</TableHead>
              <TableHead className="text-center">Active POs</TableHead>
              <TableHead className="text-center">Last PO</TableHead>
              {LOCATION_COLUMNS.map((location) => (
                <TableHead key={`${location.key}-lead`} className="text-center">
                  {location.label} Lead
                </TableHead>
              ))}
              {LOCATION_COLUMNS.map((location) => (
                <TableHead key={`${location.key}-pos`} className="text-center">
                  {location.label} Samples
                </TableHead>
              ))}
              <TableHead className="min-w-[260px]">Brands Mapped To Vendor</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 15 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 12 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : error ? (
              <TableRow>
                <TableCell colSpan={12} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-3">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <div>
                      <p className="font-medium text-foreground">Unable to load active vendors.</p>
                      <p className="text-sm">Try refreshing the dashboard.</p>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => refetch()}>
                      <RefreshCw className="mr-2 h-4 w-4" />
                      Retry
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ) : filteredLeadTimes.length === 0 ? (
              <TableRow>
                <TableCell colSpan={12} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-2">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <p>
                      {leadTimes.length === 0
                        ? 'No vendors with received lead-time samples in the past 90 days.'
                        : 'No vendors match the current search.'}
                    </p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              filteredLeadTimes.map((vendor: any) => (
                <TableRow key={vendor.vendor_id} className="hover:bg-muted/30 transition-colors">
                  <TableCell className="font-semibold text-xs py-3 border-r">
                    <div>{vendor.vendor_name}</div>
                    <div className="text-[10px] font-mono text-muted-foreground">ID {vendor.vendor_id}</div>
                  </TableCell>
                  <TableCell className="text-center tabular-nums text-xs font-semibold">
                    {vendor.active_po_count}
                  </TableCell>
                  <TableCell className="text-center tabular-nums text-xs text-muted-foreground">
                    {formatDate(vendor.last_po_ordered_at)}
                  </TableCell>
                  {LOCATION_COLUMNS.map((location) => {
                    const lead = getLocationLead(vendor, location.id)
                    return (
                      <TableCell key={`${vendor.vendor_id}-${location.key}-lead`} className="text-center tabular-nums text-xs font-bold">
                        {lead?.lead_time_days ? `${lead.lead_time_days}d` : '—'}
                      </TableCell>
                    )
                  })}
                  {LOCATION_COLUMNS.map((location) => {
                    const lead = getLocationLead(vendor, location.id)
                    return (
                      <TableCell key={`${vendor.vendor_id}-${location.key}-pos`} className="text-center tabular-nums text-[10px] text-muted-foreground">
                        {lead?.po_count ?? '—'}
                      </TableCell>
                    )
                  })}
                  <TableCell>
                    {(vendor.configured_brands || []).length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {(vendor.configured_brands || []).map((brand: string) => (
                          <Badge key={brand} variant="secondary" className="rounded-sm text-[10px]">
                            {brand}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">No configured brands</span>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
