'use client'

import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  useActiveVendorLeadTimes,
  useBrandSourcingRules,
  saveBrandSourcingRule,
} from '@/lib/hooks'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { AlertCircle, RefreshCw, Save, Search, SlidersHorizontal, X } from 'lucide-react'

type FilterMode = 'all' | 'mapped' | 'unmapped'

interface BrandDraft {
  preferredVendorId: string
  notes: string
}

function buildDrafts(brands: any[]) {
  return brands.reduce((acc: Record<string, BrandDraft>, brand: any) => {
    acc[brand.brand_name] = {
      preferredVendorId: brand.active && brand.preferred_vendor_id ? String(brand.preferred_vendor_id) : '',
      notes: brand.notes || '',
    }
    return acc
  }, {})
}

function getVendorName(vendors: any[], vendorId: string) {
  const vendor = vendors.find((entry: any) => String(entry.vendor_id) === String(vendorId))
  return vendor?.vendor_name || null
}

export function BrandConfiguration() {
  const { data: brandData, isLoading: brandsLoading, error: brandsError, refetch: refetchBrands } = useBrandSourcingRules()
  const { data: vendorData, isLoading: vendorsLoading, error: vendorsError, refetch: refetchVendors } = useActiveVendorLeadTimes()
  const [search, setSearch] = useState('')
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
  const [vendorFilter, setVendorFilter] = useState('all')
  const [drafts, setDrafts] = useState<Record<string, BrandDraft>>({})
  const [savingBrand, setSavingBrand] = useState<string | null>(null)

  const brands = brandData?.data || []
  const vendors = vendorData?.data || []
  const isLoading = brandsLoading
  const hasLoadError = brandsError

  useEffect(() => {
    if (brands.length > 0) {
      setDrafts(buildDrafts(brands))
    }
  }, [brands])

  const filteredBrands = useMemo(() => {
    const searchLower = search.trim().toLowerCase()
    return brands.filter((brand: any) => {
      const draft = drafts[brand.brand_name]
      const vendorId = draft?.preferredVendorId || ''
      const vendorName = getVendorName(vendors, vendorId) || brand.preferred_vendor_name || ''
      const isMapped = Boolean(vendorId)

      if (filterMode === 'mapped' && !isMapped) return false
      if (filterMode === 'unmapped' && isMapped) return false
      if (vendorFilter !== 'all' && vendorId !== vendorFilter) return false
      if (!searchLower) return true

      return (
        String(brand.brand_name || '').toLowerCase().includes(searchLower) ||
        String(vendorName || '').toLowerCase().includes(searchLower) ||
        String(vendorId || '').toLowerCase().includes(searchLower)
      )
    })
  }, [brands, drafts, filterMode, search, vendorFilter, vendors])

  const setDraftVendor = (brandName: string, preferredVendorId: string) => {
    setDrafts((previous) => ({
      ...previous,
      [brandName]: {
        preferredVendorId,
        notes: previous[brandName]?.notes || '',
      },
    }))
  }

  const setDraftNotes = (brandName: string, notes: string) => {
    setDrafts((previous) => ({
      ...previous,
      [brandName]: {
        preferredVendorId: previous[brandName]?.preferredVendorId || '',
        notes,
      },
    }))
  }

  const handleSave = async (brand: any) => {
    const draft = drafts[brand.brand_name] || { preferredVendorId: '', notes: '' }
    const preferredVendorName = draft.preferredVendorId
      ? getVendorName(vendors, draft.preferredVendorId)
      : null

    setSavingBrand(brand.brand_name)
    try {
      await saveBrandSourcingRule({
        brand_name: brand.brand_name,
        preferred_vendor_id: draft.preferredVendorId || null,
        preferred_vendor_name: preferredVendorName,
        active: Boolean(draft.preferredVendorId),
        notes: draft.notes || null,
        updated_by: 'Dashboard',
      })
      toast.success(`${brand.brand_name} sourcing rule saved`)
      await Promise.all([refetchBrands(), refetchVendors()])
    } catch (error: any) {
      toast.error(error.message || 'Failed to save sourcing rule')
    } finally {
      setSavingBrand(null)
    }
  }

  return (
    <div className="bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col h-full animate-in fade-in duration-500">
      <div className="p-4 border-b bg-muted/20 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <SlidersHorizontal className="w-5 h-5 text-emerald-600" />
            <div>
              <h2 className="font-semibold text-lg">Brand Configuration</h2>
              <p className="text-xs text-muted-foreground">
                Assign preferred sourcing vendors by brand for a future lead-time policy
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={filterMode} onValueChange={(value) => setFilterMode(value as FilterMode)}>
              <SelectTrigger className="h-9 w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Brands</SelectItem>
                <SelectItem value="mapped">Mapped</SelectItem>
                <SelectItem value="unmapped">Unmapped</SelectItem>
              </SelectContent>
            </Select>
            <Select value={vendorFilter} onValueChange={setVendorFilter}>
              <SelectTrigger className="h-9 w-[220px]">
                <SelectValue placeholder="All Vendors" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Vendors</SelectItem>
                {vendors.map((vendor: any) => (
                  <SelectItem key={vendor.vendor_id} value={String(vendor.vendor_id)}>
                    {vendor.vendor_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="relative w-[280px]">
              <Search className="text-muted-foreground absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search brand or vendor..."
                className="h-9 pl-9"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => Promise.all([refetchBrands(), refetchVendors()])}
              disabled={isLoading}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </div>
        {vendorsError && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            Vendor options could not be loaded. Brand rows are still available, but preferred vendor changes need the vendor list.
          </div>
        )}
      </div>

      <div className="overflow-auto flex-1">
        <Table>
          <TableHeader className="bg-muted/50 sticky top-0 z-10 backdrop-blur-sm">
            <TableRow>
              <TableHead className="min-w-[220px]">Brand</TableHead>
              <TableHead className="text-center">Items</TableHead>
              <TableHead className="min-w-[300px]">Preferred Vendor</TableHead>
              <TableHead className="min-w-[260px]">Notes</TableHead>
              <TableHead className="text-center">Status</TableHead>
              <TableHead className="w-[120px] text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 15 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 6 }).map((_, j) => (
                    <TableCell key={j}><Skeleton className="h-4 w-full" /></TableCell>
                  ))}
                </TableRow>
              ))
            ) : hasLoadError ? (
              <TableRow>
                <TableCell colSpan={6} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-3">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <div>
                      <p className="font-medium text-foreground">Unable to load brand configuration.</p>
                      <p className="text-sm">Try refreshing the dashboard.</p>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => Promise.all([refetchBrands(), refetchVendors()])}>
                      <RefreshCw className="mr-2 h-4 w-4" />
                      Retry
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ) : filteredBrands.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-64 text-center text-muted-foreground">
                  <div className="flex flex-col items-center gap-2">
                    <AlertCircle className="w-8 h-8 opacity-20" />
                    <p>No brands found.</p>
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              filteredBrands.map((brand: any) => {
                const draft = drafts[brand.brand_name] || { preferredVendorId: '', notes: '' }
                const isMapped = Boolean(draft.preferredVendorId)
                return (
                  <TableRow key={brand.brand_name} className="hover:bg-muted/30 transition-colors">
                    <TableCell className="py-3">
                      <div className="font-semibold text-sm">{brand.brand_name}</div>
                    </TableCell>
                    <TableCell className="text-center tabular-nums text-xs text-muted-foreground">
                      {brand.item_count}
                    </TableCell>
                    <TableCell>
                      <Select
                        value={draft.preferredVendorId}
                        onValueChange={(value) => setDraftVendor(brand.brand_name, value)}
                        disabled={Boolean(vendorsError) || vendorsLoading || vendors.length === 0}
                      >
                        <SelectTrigger className="h-9 w-full">
                          <SelectValue placeholder={vendorsError ? 'Vendor options unavailable' : 'Select preferred vendor'} />
                        </SelectTrigger>
                        <SelectContent>
                          {vendors.map((vendor: any) => (
                            <SelectItem key={vendor.vendor_id} value={String(vendor.vendor_id)}>
                              {vendor.vendor_name} · ID {vendor.vendor_id}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        value={draft.notes}
                        onChange={(event) => setDraftNotes(brand.brand_name, event.target.value)}
                        placeholder="Optional notes"
                        className="h-9"
                      />
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={isMapped ? 'default' : 'secondary'} className="rounded-sm">
                        {isMapped ? 'Mapped' : 'Unmapped'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        {isMapped && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => setDraftVendor(brand.brand_name, '')}
                            title="Clear preferred vendor"
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        )}
                        <Button
                          size="sm"
                          className="h-8"
                          onClick={() => handleSave(brand)}
                          disabled={savingBrand === brand.brand_name}
                        >
                          <Save className="mr-1.5 h-3.5 w-3.5" />
                          Save
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
