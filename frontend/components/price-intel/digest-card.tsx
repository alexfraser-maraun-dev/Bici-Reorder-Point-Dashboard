'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { apiPost, useLatestDigest } from '@/lib/price-intel/hooks'
import { RefreshCw, Sparkles } from 'lucide-react'

// Minimal markdown rendering (headings, bold, lists) — the digest is trusted
// model output stored by our own backend, but we still render as text nodes,
// never raw HTML.
function DigestMarkdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/)
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {blocks.map((block, i) => {
        const lines = block.split('\n')
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {lines.map((line, j) => (
                <li key={j}>{renderInline(line.replace(/^\s*([-*]|\d+\.)\s+/, ''))}</li>
              ))}
            </ul>
          )
        }
        const heading = block.match(/^(#{1,4})\s+(.*)$/)
        if (heading) {
          return <h4 key={i} className="font-semibold">{renderInline(heading[2])}</h4>
        }
        return <p key={i}>{renderInline(block)}</p>
      })}
    </div>
  )
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : <span key={i}>{part}</span>
  )
}

export function DigestCard() {
  const { digest, isLoading, mutate } = useLatestDigest()
  const [regenerating, setRegenerating] = useState(false)

  const regenerate = async () => {
    setRegenerating(true)
    const prevCreatedAt = digest?.created_at ?? null
    try {
      await apiPost('/api/price-intel/digest/generate')
      toast.success('Regenerating digest from the latest run — this takes a moment')
      // Generation runs in a server-side background task, so an immediate
      // refetch would just re-show the old digest. Poll until a newer one
      // lands (keeping the button disabled so a second click can't queue a
      // duplicate generation), giving up quietly after ~1 minute.
      for (let i = 0; i < 15; i++) {
        await new Promise((resolve) => setTimeout(resolve, 4000))
        const fresh = await mutate()
        if (fresh?.created_at && fresh.created_at !== prevCreatedAt) return
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to regenerate digest')
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-600" />
            <h3 className="text-sm font-semibold">Market digest</h3>
            {digest?.created_at && (
              <span className="text-xs text-muted-foreground">
                {new Date(digest.created_at).toLocaleString()}
                {digest.model ? ` · ${digest.model}` : ''}
              </span>
            )}
          </div>
          <Button variant="outline" size="sm" onClick={() => void regenerate()}
                  disabled={regenerating}>
            <RefreshCw className={regenerating ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} /> Regenerate
          </Button>
        </div>
        {isLoading ? (
          <Skeleton className="h-40 rounded-lg" />
        ) : digest?.digest_md ? (
          <DigestMarkdown text={digest.digest_md} />
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No digest yet — one is generated automatically after each nightly scrape.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
