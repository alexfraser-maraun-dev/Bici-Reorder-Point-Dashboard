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

  const startScrape = async () => {
    setStarting(true)
    try {
      await apiPost('/api/price-intel/scrape')
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
      <Button size="sm" onClick={startScrape} disabled={running || starting}>
        <RefreshCw className={running || starting ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
        {running ? 'Scraping…' : 'Scrape now'}
      </Button>
    </div>
  )
}
