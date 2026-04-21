'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import type { FilterState, RecommendationRun } from '@/lib/types'
import { filterOptions } from '@/lib/mock-data'
import {
  Search,
  MapPin,
  Building2,
  Tag,
  FolderOpen,
  RotateCcw,
  ChevronDown,
  Filter,
} from 'lucide-react'

interface FilterBarProps {
  filters: FilterState
  onFiltersChange: (filters: FilterState) => void
  recommendationRuns: RecommendationRun[]
}

interface MultiSelectFilterProps {
  label: string
  icon: React.ReactNode
  options: string[]
  selected: string[]
  onChange: (selected: string[]) => void
}

function MultiSelectFilter({ label, icon, options, selected, onChange }: MultiSelectFilterProps) {
  const [open, setOpen] = useState(false)

  const handleToggle = (option: string) => {
    if (selected.includes(option)) {
      onChange(selected.filter((s) => s !== option))
    } else {
      onChange([...selected, option])
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
          {icon}
          {label}
          {selected.length > 0 && (
            <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
              {selected.length}
            </Badge>
          )}
          <ChevronDown className="ml-0.5 h-3 w-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-52 p-0" align="start">
        <ScrollArea className="h-64">
          <div className="p-2">
            {options.map((option) => (
              <div
                key={option}
                className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5"
                onClick={() => handleToggle(option)}
              >
                <Checkbox
                  checked={selected.includes(option)}
                  onCheckedChange={() => handleToggle(option)}
                  className="h-3.5 w-3.5"
                />
                <span className="text-sm">{option}</span>
              </div>
            ))}
          </div>
        </ScrollArea>
        {selected.length > 0 && (
          <>
            <Separator />
            <div className="p-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-full text-xs"
                onClick={() => onChange([])}
              >
                Clear selection
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}

export function FilterBar({ filters, onFiltersChange, recommendationRuns }: FilterBarProps) {
  const updateFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => {
    onFiltersChange({ ...filters, [key]: value })
  }

  const resetFilters = () => {
    onFiltersChange({
      search: '',
      locations: [],
      vendors: [],
      brands: [],
      categories: [],
      needsOrderOnly: false,
      changedOnly: false,
      lockedOnly: false,
      overriddenOnly: false,
      writebackFailedOnly: false,
      recommendationRunId: null,
    })
  }

  const hasActiveFilters =
    filters.search ||
    filters.locations.length > 0 ||
    filters.vendors.length > 0 ||
    filters.brands.length > 0 ||
    filters.categories.length > 0 ||
    filters.needsOrderOnly ||
    filters.changedOnly ||
    filters.lockedOnly ||
    filters.overriddenOnly ||
    filters.writebackFailedOnly ||
    filters.recommendationRunId

  return (
    <div className="bg-card rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        {/* Search */}
        <div className="relative min-w-[200px] flex-1 lg:max-w-xs">
          <Search className="text-muted-foreground absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2" />
          <Input
            placeholder="Search SKU / Product..."
            value={filters.search}
            onChange={(e) => updateFilter('search', e.target.value)}
            className="h-8 pl-8 text-sm"
          />
        </div>

        <Separator orientation="vertical" className="mx-1 h-6" />

        {/* Multi-select filters */}
        <MultiSelectFilter
          label="Location"
          icon={<MapPin className="h-3.5 w-3.5" />}
          options={filterOptions.locations}
          selected={filters.locations}
          onChange={(v) => updateFilter('locations', v)}
        />
        <MultiSelectFilter
          label="Vendor"
          icon={<Building2 className="h-3.5 w-3.5" />}
          options={filterOptions.vendors}
          selected={filters.vendors}
          onChange={(v) => updateFilter('vendors', v)}
        />
        <MultiSelectFilter
          label="Brand"
          icon={<Tag className="h-3.5 w-3.5" />}
          options={filterOptions.brands}
          selected={filters.brands}
          onChange={(v) => updateFilter('brands', v)}
        />
        <MultiSelectFilter
          label="Category"
          icon={<FolderOpen className="h-3.5 w-3.5" />}
          options={filterOptions.categories}
          selected={filters.categories}
          onChange={(v) => updateFilter('categories', v)}
        />

        <Separator orientation="vertical" className="mx-1 h-6" />

        {/* Boolean filters */}
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
              <Filter className="h-3.5 w-3.5" />
              Status Filters
              {(filters.needsOrderOnly ||
                filters.changedOnly ||
                filters.lockedOnly ||
                filters.overriddenOnly ||
                filters.writebackFailedOnly) && (
                <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">
                  {[
                    filters.needsOrderOnly,
                    filters.changedOnly,
                    filters.lockedOnly,
                    filters.overriddenOnly,
                    filters.writebackFailedOnly,
                  ].filter(Boolean).length}
                </Badge>
              )}
              <ChevronDown className="ml-0.5 h-3 w-3 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-52 p-2" align="start">
            <div className="space-y-1">
              {[
                { key: 'needsOrderOnly' as const, label: 'Needs Order' },
                { key: 'changedOnly' as const, label: 'Changed' },
                { key: 'lockedOnly' as const, label: 'Locked' },
                { key: 'overriddenOnly' as const, label: 'Overridden' },
                { key: 'writebackFailedOnly' as const, label: 'Writeback Failed' },
              ].map(({ key, label }) => (
                <div
                  key={key}
                  className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5"
                  onClick={() => updateFilter(key, !filters[key])}
                >
                  <Checkbox
                    checked={filters[key]}
                    onCheckedChange={(checked) => updateFilter(key, !!checked)}
                    className="h-3.5 w-3.5"
                  />
                  <span className="text-sm">{label}</span>
                </div>
              ))}
            </div>
          </PopoverContent>
        </Popover>

        {/* Recommendation run selector */}
        <Select
          value={filters.recommendationRunId || 'latest'}
          onValueChange={(v) => updateFilter('recommendationRunId', v === 'latest' ? null : v)}
        >
          <SelectTrigger className="h-8 w-[160px] text-xs">
            <SelectValue placeholder="Select run" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="latest">Latest Run</SelectItem>
            {recommendationRuns.map((run) => (
              <SelectItem key={run.id} value={run.id}>
                {new Date(run.runDate).toLocaleDateString()}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Reset button */}
        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={resetFilters}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </Button>
        )}
      </div>
    </div>
  )
}
