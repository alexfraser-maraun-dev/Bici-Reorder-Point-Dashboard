'use client'

import { useState, useMemo, useRef } from 'react'
import { useManagedSkus } from '@/lib/hooks'
import { toast } from 'sonner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Search,
  Upload,
  Download,
  MoreHorizontal,
  CheckCircle2,
  XCircle,
  Plus,
  Trash2,
} from 'lucide-react'
import { cn } from '@/lib/utils'

export function ManagedSkusContent() {
  const { data: skus, isLoading, refetch } = useManagedSkus()
  const [search, setSearch] = useState('')
  const [selectedSkus, setSelectedSkus] = useState<string[]>([])
  const [showActiveOnly, setShowActiveOnly] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const filteredSkus = useMemo(() => {
    return skus.filter((sku) => {
      if (search) {
        const searchLower = search.toLowerCase()
        if (
          !sku.sku.toLowerCase().includes(searchLower) &&
          !sku.product.toLowerCase().includes(searchLower)
        ) {
          return false
        }
      }
      if (showActiveOnly && !sku.active) {
        return false
      }
      return true
    })
  }, [skus, search, showActiveOnly])

  const handleToggleAll = (checked: boolean) => {
    if (checked) {
      setSelectedSkus(filteredSkus.map((s) => s.id))
    } else {
      setSelectedSkus([])
    }
  }

  const handleToggleSku = (skuId: string) => {
    if (selectedSkus.includes(skuId)) {
      setSelectedSkus(selectedSkus.filter((id) => id !== skuId))
    } else {
      setSelectedSkus([...selectedSkus, skuId])
    }
  }

  const handleBulkImport = () => {
    fileInputRef.current?.click()
  }
  
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    
    setIsUploading(true)
    const toastId = toast.loading(`Uploading ${file.name}...`)
    
    try {
      const formData = new FormData()
      formData.append('file', file)
      
      const response = await fetch('http://127.0.0.1:8000/api/skus/upload', {
        method: 'POST',
        body: formData,
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => null)
        throw new Error(errorData?.detail || 'Failed to upload file')
      }
      
      const data = await response.json()
      toast.success(`Import successful: ${data.added} added, ${data.updated} updated`, { id: toastId })
      
      // Refresh the table
      if (refetch) refetch()
    } catch (error: any) {
      toast.error(`Upload failed: ${error.message}`, { id: toastId })
    } finally {
      setIsUploading(false)
      // Reset input so the same file can be selected again if needed
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleExport = () => {
    toast.success('Exported managed SKUs to CSV')
  }

  const handleToggleActive = (skuId: string, active: boolean) => {
    toast.success(`SKU ${active ? 'activated' : 'deactivated'}`)
  }

  const handleRemove = (skuId: string) => {
    toast.success('SKU removed from managed list')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Managed SKUs</h1>
          <p className="text-muted-foreground text-sm">
            SKUs included in reorder point and desired inventory automation
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleExport}>
            <Download className="mr-2 h-4 w-4" />
            Export
          </Button>
          <Button variant="outline" onClick={() => window.open('http://127.0.0.1:8000/api/skus/template', '_blank')}>
            <Download className="mr-2 h-4 w-4" />
            Template
          </Button>
          <input
            type="file"
            accept=".csv"
            className="hidden"
            ref={fileInputRef}
            onChange={handleFileUpload}
          />
          <Button variant="outline" onClick={handleBulkImport} disabled={isUploading}>
            <Upload className="mr-2 h-4 w-4" />
            {isUploading ? 'Uploading...' : 'Bulk Import'}
          </Button>
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            Add SKU
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">SKU List</CardTitle>
              <CardDescription>
                {filteredSkus.length} of {skus.length} SKUs
                {selectedSkus.length > 0 && ` · ${selectedSkus.length} selected`}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* Filters */}
          <div className="mb-4 flex items-center gap-3">
            <div className="relative max-w-sm flex-1">
              <Search className="text-muted-foreground absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2" />
              <Input
                placeholder="Search SKU / Product..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="active-only"
                checked={showActiveOnly}
                onCheckedChange={(checked) => setShowActiveOnly(!!checked)}
              />
              <label htmlFor="active-only" className="text-sm">
                Active only
              </label>
            </div>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 10 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <Checkbox
                        checked={
                          filteredSkus.length > 0 &&
                          selectedSkus.length === filteredSkus.length
                        }
                        onCheckedChange={handleToggleAll}
                      />
                    </TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>Brand</TableHead>
                    <TableHead>Vendor</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Added</TableHead>
                    <TableHead className="w-10"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredSkus.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={9} className="h-24 text-center">
                        <div className="text-muted-foreground">
                          <p className="font-medium">No SKUs found</p>
                          <p className="text-sm">Try adjusting your search or filters</p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredSkus.map((sku) => (
                      <TableRow
                        key={sku.id}
                        className={cn(!sku.active && 'opacity-60')}
                      >
                        <TableCell>
                          <Checkbox
                            checked={selectedSkus.includes(sku.id)}
                            onCheckedChange={() => handleToggleSku(sku.id)}
                          />
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-xs font-medium">{sku.sku}</span>
                        </TableCell>
                        <TableCell>
                          <span className="max-w-[150px] truncate text-sm">{sku.product}</span>
                        </TableCell>
                        <TableCell>
                          <span className="text-sm">{sku.brand}</span>
                        </TableCell>
                        <TableCell>
                          <span className="text-muted-foreground max-w-[120px] truncate text-sm">
                            {sku.vendor}
                          </span>
                        </TableCell>
                        <TableCell>
                          <span className="text-sm">{sku.category}</span>
                        </TableCell>
                        <TableCell>
                          {sku.active ? (
                            <Badge
                              variant="outline"
                              className="border-emerald-200 bg-emerald-100 text-emerald-700"
                            >
                              <CheckCircle2 className="mr-1 h-3 w-3" />
                              Active
                            </Badge>
                          ) : (
                            <Badge
                              variant="outline"
                              className="border-border bg-secondary text-muted-foreground"
                            >
                              <XCircle className="mr-1 h-3 w-3" />
                              Inactive
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <div>
                            <p className="text-sm">
                              {new Date(sku.addedAt).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                year: 'numeric',
                              })}
                            </p>
                            <p className="text-muted-foreground max-w-[100px] truncate text-xs">
                              {sku.addedBy}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-8 w-8">
                                <MoreHorizontal className="h-4 w-4" />
                                <span className="sr-only">Open menu</span>
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={() => handleToggleActive(sku.id, !sku.active)}
                              >
                                {sku.active ? 'Deactivate' : 'Activate'}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onClick={() => handleRemove(sku.id)}
                                className="text-red-600"
                              >
                                <Trash2 className="mr-2 h-4 w-4" />
                                Remove
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Bulk action bar */}
          {selectedSkus.length > 0 && (
            <div className="mt-4 flex items-center justify-between rounded-lg border bg-muted/50 p-3">
              <span className="text-sm font-medium">
                {selectedSkus.length} SKU{selectedSkus.length !== 1 ? 's' : ''} selected
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    toast.success('Activated selected SKUs')
                    setSelectedSkus([])
                  }}
                >
                  Activate
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    toast.success('Deactivated selected SKUs')
                    setSelectedSkus([])
                  }}
                >
                  Deactivate
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-red-600"
                  onClick={() => {
                    toast.success('Removed selected SKUs')
                    setSelectedSkus([])
                  }}
                >
                  Remove
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
