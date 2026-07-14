'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PriceIntelKpiCards } from '@/components/price-intel/kpi-cards'
import { ScrapeStatusButton } from '@/components/price-intel/scrape-status-button'
import { ScrapeHealthPill } from '@/components/price-intel/scrape-health'
import { TrackedProductsTable } from '@/components/price-intel/tracked-products-table'
import { ChangeFeed } from '@/components/price-intel/change-feed'
import { CompetitorManager } from '@/components/price-intel/competitor-manager'
import { DigestCard } from '@/components/price-intel/digest-card'
import { MatchReview } from '@/components/price-intel/match-review'
import { AdminConsole } from '@/components/price-intel/admin-console'
import {
  usePriceIntelSummary, useTrackedProducts, useChangeFeed, useProductLinks,
} from '@/lib/price-intel/hooks'
import { isMapViolation } from '@/lib/price-intel/format'
import { Badge } from '@/components/ui/badge'
import { Bell, GitMerge, Globe, Settings2, Sparkles, Table2 } from 'lucide-react'
import { useMemo, useState } from 'react'

export function PriceIntelligenceContent() {
  const { summary, isLoading, mutate: mutateSummary } = usePriceIntelSummary()
  const { products, mutate: mutateTracked } = useTrackedProducts()
  const { mutate: mutateChanges } = useChangeFeed()
  const { mutate: mutateLinks } = useProductLinks('pending')
  const [tab, setTab] = useState('tracked')
  const [quickFilter, setQuickFilter] = useState<string | null>(null)

  const mapViolations = useMemo(() => products.filter(isMapViolation).length, [products])

  const onKpiSelect = (key: string) => {
    if (key === 'changes') { setTab('changes'); return }
    setQuickFilter(key)
    setTab('tracked')
  }

  const refreshAll = () => {
    void mutateSummary()
    void mutateTracked()
    void mutateChanges()
    void mutateLinks()
  }

  const unread = summary?.unacknowledged_changes ?? 0
  const pendingLinks = summary?.pending_links ?? 0

  return (
    <div className="space-y-4 p-4 lg:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Price Intelligence</h1>
          <p className="text-sm text-muted-foreground">
            Competitor prices for your top-revenue and pinned products, scraped nightly.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ScrapeHealthPill health={summary?.scrape_health} />
          <ScrapeStatusButton onRunFinished={refreshAll} />
        </div>
      </div>

      <PriceIntelKpiCards summary={summary} isLoading={isLoading}
                          mapViolations={mapViolations} onSelect={onKpiSelect} />

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="tracked" className="gap-1.5">
            <Table2 className="h-4 w-4" /> Tracked Products
          </TabsTrigger>
          <TabsTrigger value="changes" className="gap-1.5">
            <Bell className="h-4 w-4" /> Change Feed
            {unread > 0 && (
              <Badge className="ml-1 h-5 min-w-5 justify-center rounded-full bg-amber-500 px-1.5 text-[11px] text-white hover:bg-amber-500">
                {unread > 99 ? '99+' : unread}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="matching" className="gap-1.5">
            <GitMerge className="h-4 w-4" /> Matching
            {pendingLinks > 0 && (
              <Badge className="ml-1 h-5 min-w-5 justify-center rounded-full bg-sky-500 px-1.5 text-[11px] text-white hover:bg-sky-500">
                {pendingLinks > 99 ? '99+' : pendingLinks}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="competitors" className="gap-1.5">
            <Globe className="h-4 w-4" /> Competitors
          </TabsTrigger>
          <TabsTrigger value="digest" className="gap-1.5">
            <Sparkles className="h-4 w-4" /> Digest
          </TabsTrigger>
          <TabsTrigger value="admin" className="gap-1.5">
            <Settings2 className="h-4 w-4" /> Admin
          </TabsTrigger>
        </TabsList>

        <TabsContent value="tracked" className="mt-4">
          <TrackedProductsTable quickFilter={quickFilter}
                                onClearQuickFilter={() => setQuickFilter(null)} />
        </TabsContent>
        <TabsContent value="changes" className="mt-4">
          <ChangeFeed />
        </TabsContent>
        <TabsContent value="matching" className="mt-4">
          <MatchReview />
        </TabsContent>
        <TabsContent value="competitors" className="mt-4">
          <CompetitorManager />
        </TabsContent>
        <TabsContent value="digest" className="mt-4">
          <DigestCard />
        </TabsContent>
        <TabsContent value="admin" className="mt-4">
          <AdminConsole />
        </TabsContent>
      </Tabs>
    </div>
  )
}
