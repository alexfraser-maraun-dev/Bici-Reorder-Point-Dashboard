'use client'

import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { apiPost, useScrapeStatus } from '@/lib/price-intel/hooks'
import { RefreshCw } from 'lucide-react'

export function ScrapeStatusButton({ onRunFinished }: { onRunFinished?: () => void }) {
  const [starting, setStarting] = useState(false)
  const { status, mutate } = useScrapeStatus(true)
  const running = status?.status === 'running'
  const wasRunning = useRef(false)

  useEffect(() => {
    if (wasRunning.current && !running && status?.status) {
      if (status.status === 'success') toast.success('Scrape finished')
      else if (status.status === 'partial') toast.warning('Scrape finished with some errors')
      else if (status.status === 'failed') toast.error('Scrape failed — see run history')
      onRunFinished?.()
    }
    wasRunning.current = !!running
  }, [running, status?.status, onRunFinished])

  const startScrape = async (full: boolean) => {
    setStarting(true)
    try {
      await apiPost(`/api/price-intel/scrape${full ? '?full=true' : ''}`)
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to start scrape')
    } finally {
      setStarting(false)
    }
  }

  const progress = running
    ? [
        status?.phase,
        status?.competitors_total ? `${status.competitors_done}/${status.competitors_total} stores` : null,
        status?.links_total ? `${status.links_done}/${status.links_total} links` : null,
        status?.urls_total ? `${status.urls_done}/${status.urls_total} URLs` : null,
      ].filter(Boolean).join(' · ')
    : null

  return (
    <div className="flex items-center gap-3">
      {progress && (
        <span className="hidden text-xs text-muted-foreground sm:inline" title={progress}>
          {progress}
        </span>
      )}
      <Button variant="outline" size="sm" onClick={() => startScrape(true)}
              disabled={running || starting}
              title="Crawl the competitors' whole catalogs to find matches for new items">
        Full scan
      </Button>
      <Button size="sm" onClick={() => startScrape(false)} disabled={running || starting}
              title="Re-check confirmed match URLs (full crawl only if new items need discovery)">
        <RefreshCw className={running || starting ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
        {running ? 'Scraping…' : 'Scrape now'}
      </Button>
    </div>
  )
}
