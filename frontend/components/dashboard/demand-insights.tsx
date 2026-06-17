'use client'

import { useMemo, useState } from 'react'
import { useSeasonalProfiles, useDemandHistory, useCoverage } from '@/lib/hooks'
import type { SeasonalProfile, DemandHistoryPoint, ForecastPoint, CoverageRow } from '@/lib/types'
import { SeasonalProfileChart } from '@/components/charts/seasonal-profile-chart'
import { DemandForecastChart } from '@/components/charts/demand-forecast-chart'
import { CoverageHeatmap } from '@/components/charts/coverage-heatmap'
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
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { TrendingUp, AlertCircle, RefreshCw, LayoutGrid, MapPin } from 'lucide-react'

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

const TOP_LEVEL = 'category_top_level'
const MAX_DEFAULT_SELECTED = 4

// Lightspeed shop ids; '' = all locations pooled.
const LOCATIONS = [
  { value: '', label: 'All locations' },
  { value: '3', label: 'Bici Adanac' },
  { value: '2', label: 'Victoria' },
  { value: '20', label: 'Langford' },
]

function indexFor(profile: SeasonalProfile, monthNumber: number): number {
  return profile.indices[String(monthNumber)] ?? profile.indices[monthNumber] ?? 1
}

// Month label of the highest / lowest index — the category's peak and trough.
function peakTrough(profile: SeasonalProfile) {
  let peakMonth = 1
  let troughMonth = 1
  for (let m = 1; m <= 12; m++) {
    if (indexFor(profile, m) > indexFor(profile, peakMonth)) peakMonth = m
    if (indexFor(profile, m) < indexFor(profile, troughMonth)) troughMonth = m
  }
  return {
    peak: MONTH_LABELS[peakMonth - 1],
    peakIndex: indexFor(profile, peakMonth),
    trough: MONTH_LABELS[troughMonth - 1],
    troughIndex: indexFor(profile, troughMonth),
  }
}

export function DemandInsights() {
  const [location, setLocation] = useState('')
  const { data, isLoading, error, refetch } = useSeasonalProfiles(location)
  const [selected, setSelected] = useState<string[] | null>(null)
  // Category the history+forecast chart drills into (click a table row to set it).
  const [focusOverride, setFocusOverride] = useState<string | null>(null)

  // Top-level categories, highest volume first — these carry the clearest signal.
  const topLevelProfiles: SeasonalProfile[] = useMemo(() => {
    const all: SeasonalProfile[] = data?.data || []
    return all.filter((profile) => profile.level === TOP_LEVEL)
  }, [data])

  // Default selection: the highest-volume top-level categories (pre-selected so the
  // opposite-peak comparison is visible immediately on load).
  const effectiveSelected = useMemo(() => {
    if (selected !== null) return selected
    return topLevelProfiles.slice(0, MAX_DEFAULT_SELECTED).map((p) => p.category_label)
  }, [selected, topLevelProfiles])

  const selectedProfiles = useMemo(
    () => topLevelProfiles.filter((p) => effectiveSelected.includes(p.category_label)),
    [topLevelProfiles, effectiveSelected],
  )

  function toggle(label: string) {
    const current = effectiveSelected
    setSelected(
      current.includes(label)
        ? current.filter((l) => l !== label)
        : [...current, label],
    )
  }

  // The category the forecast chart drills into: the clicked one if it's still
  // selected, otherwise the highest-volume selected category.
  const focusCategory =
    (focusOverride && effectiveSelected.includes(focusOverride) ? focusOverride : null) ??
    selectedProfiles[0]?.category_label ??
    null
  const { data: focusHistory, isLoading: focusLoading } = useDemandHistory('category', focusCategory, location)
  const focusPayload = focusHistory?.data
  const focusHistoryPoints: DemandHistoryPoint[] = focusPayload?.history ?? []
  const focusForecast: ForecastPoint[] = focusPayload?.forecast ?? []
  const focusWindow = focusPayload?.lead_time_window ?? null
  const focusReferenceMonth = focusHistory?.meta?.reference_month ?? new Date().getMonth() + 1

  // Forward coverage heatmap (soonest stockouts first).
  const { data: coverageData, isLoading: coverageLoading } = useCoverage(location)
  const coverageRows: CoverageRow[] = coverageData?.data?.rows ?? []
  const coverageReferenceMonth = coverageData?.meta?.reference_month ?? new Date().getMonth() + 1

  return (
    <div className="animate-in fade-in space-y-3 duration-500">
    <div className="bg-card flex flex-col gap-4 rounded-xl border p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div className="flex items-center gap-3">
          <TrendingUp className="h-5 w-5 text-emerald-600" />
          <div>
            <h2 className="text-lg font-semibold">Seasonal Profiles by Category</h2>
            <p className="text-muted-foreground text-xs">
              Multiplicative monthly index (mean&nbsp;=&nbsp;1.0). Above 1.0 = seasonal peak,
              below = trough. Sparse SKUs borrow their category&apos;s shape.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Select value={location || 'all'} onValueChange={(v) => setLocation(v === 'all' ? '' : v)}>
            <SelectTrigger className="h-9 w-[170px]">
              <MapPin className="mr-1 h-3.5 w-3.5 text-muted-foreground" />
              <SelectValue placeholder="All locations" />
            </SelectTrigger>
            <SelectContent>
              {LOCATIONS.map((loc) => (
                <SelectItem key={loc.value || 'all'} value={loc.value || 'all'}>
                  {loc.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isLoading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-[280px] w-full" />
        </div>
      ) : error ? (
        <div className="text-muted-foreground flex h-64 flex-col items-center justify-center gap-3">
          <AlertCircle className="h-8 w-8 opacity-20" />
          <div className="text-center">
            <p className="text-foreground font-medium">Unable to load seasonal profiles.</p>
            <p className="text-sm">The forecast data source may be unavailable.</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Retry
          </Button>
        </div>
      ) : topLevelProfiles.length === 0 ? (
        <div className="text-muted-foreground flex h-64 flex-col items-center justify-center gap-2">
          <AlertCircle className="h-8 w-8 opacity-20" />
          <p>No seasonal history available yet.</p>
        </div>
      ) : (
        <>
          {/* Category toggles — click to add/remove a category from the overlay. */}
          <div className="flex flex-wrap gap-2">
            {topLevelProfiles.map((profile) => {
              const active = effectiveSelected.includes(profile.category_label)
              return (
                <button
                  key={profile.category_label}
                  type="button"
                  onClick={() => toggle(profile.category_label)}
                  className="focus-visible:ring-ring rounded-full focus-visible:ring-2 focus-visible:outline-none"
                >
                  <Badge
                    variant={active ? 'default' : 'outline'}
                    className="cursor-pointer rounded-full px-3 py-1 text-xs"
                  >
                    {profile.category_label}
                  </Badge>
                </button>
              )
            })}
          </div>

          <SeasonalProfileChart profiles={selectedProfiles} />

          {/* Paired data table — every chart keeps its numbers fully inspectable. */}
          <div className="overflow-auto rounded-lg border">
            <Table>
              <TableHeader className="bg-muted/50">
                <TableRow>
                  <TableHead className="min-w-[160px]">Category <span className="text-muted-foreground font-normal">(click to drill into forecast)</span></TableHead>
                  <TableHead className="text-center">Peak</TableHead>
                  <TableHead className="text-center">Trough</TableHead>
                  <TableHead className="text-right">Units (3yr)</TableHead>
                  {MONTH_LABELS.map((label) => (
                    <TableHead key={label} className="text-center text-[10px]">
                      {label}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {selectedProfiles.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={16} className="text-muted-foreground h-24 text-center text-sm">
                      Select a category above to inspect its monthly indices.
                    </TableCell>
                  </TableRow>
                ) : (
                  selectedProfiles.map((profile) => {
                    const pt = peakTrough(profile)
                    const isFocused = profile.category_label === focusCategory
                    return (
                      <TableRow
                        key={profile.category_label}
                        onClick={() => setFocusOverride(profile.category_label)}
                        className={`cursor-pointer transition-colors ${isFocused ? 'bg-emerald-50 hover:bg-emerald-50' : 'hover:bg-muted/30'}`}
                      >
                        <TableCell className="text-xs font-semibold">
                          <span className={isFocused ? 'text-emerald-700' : ''}>{profile.category_label}</span>
                          {isFocused && <span className="text-muted-foreground ml-1.5 font-normal">• in chart below</span>}
                        </TableCell>
                        <TableCell className="text-center text-xs">
                          <span className="font-medium text-emerald-600">{pt.peak}</span>
                          <span className="text-muted-foreground ml-1 tabular-nums">
                            {pt.peakIndex.toFixed(1)}×
                          </span>
                        </TableCell>
                        <TableCell className="text-center text-xs">
                          <span className="font-medium text-red-600">{pt.trough}</span>
                          <span className="text-muted-foreground ml-1 tabular-nums">
                            {pt.troughIndex.toFixed(1)}×
                          </span>
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          {profile.sample_units.toLocaleString()}
                        </TableCell>
                        {MONTH_LABELS.map((label, monthIndex) => {
                          const value = indexFor(profile, monthIndex + 1)
                          const emphasis =
                            value >= 1.15
                              ? 'text-emerald-600 font-semibold'
                              : value <= 0.85
                                ? 'text-red-600'
                                : 'text-muted-foreground'
                          return (
                            <TableCell
                              key={label}
                              className={`text-center text-[10px] tabular-nums ${emphasis}`}
                            >
                              {value.toFixed(1)}
                            </TableCell>
                          )
                        })}
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>

          {/* Category history + forward forecast for the focused category. */}
          {focusCategory && (
            <div className="border-t pt-4">
              <h3 className="text-sm font-semibold">
                {focusCategory} — history &amp; forecast
              </h3>
              <p className="text-muted-foreground mb-3 text-xs">
                Monthly units sold (bars) with the seasonally-adjusted forward forecast (dashed).
                The shaded band marks the months a PO placed now would cover — buy ahead of the ramp.
              </p>
              <DemandForecastChart
                history={focusHistoryPoints}
                forecast={focusForecast}
                leadTimeWindow={focusWindow}
                referenceMonth={focusReferenceMonth}
                isLoading={focusLoading}
              />
            </div>
          )}
        </>
      )}
    </div>

    {/* Forward coverage heatmap — triage future (often seasonal) stockouts. */}
    <div className="bg-card flex flex-col gap-3 rounded-xl border p-4 shadow-sm">
      <div className="flex items-center gap-3 border-b pb-3">
        <LayoutGrid className="h-5 w-5 text-amber-600" />
        <div>
          <h2 className="text-lg font-semibold">Forward Coverage</h2>
          <p className="text-muted-foreground text-xs">
            Projected weeks of cover per month, seasonally adjusted. Soonest stockouts first —
            spot a seasonal ramp draining stock before it happens.
          </p>
        </div>
      </div>
      <CoverageHeatmap
        rows={coverageRows}
        referenceMonth={coverageReferenceMonth}
        isLoading={coverageLoading}
      />
    </div>
    </div>
  )
}
