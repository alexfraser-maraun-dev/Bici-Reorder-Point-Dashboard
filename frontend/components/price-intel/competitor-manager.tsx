'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { apiPost, useCompetitors, useTrackedUrls } from '@/lib/price-intel/hooks'
import { itemIdentity, lightspeedItemUrl } from '@/lib/price-intel/format'
import type { TrackedUrl } from '@/lib/price-intel/types'
import { ItemSearchPicker } from './item-search-picker'
import { ExternalLink, Globe, Link2, Plus, Trash2 } from 'lucide-react'

const CONNECTOR_LABEL: Record<string, { label: string; tone: string }> = {
  shopify_json: { label: 'Shopify (catalog)', tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  shopify_html: { label: 'Shopify (pages)', tone: 'bg-sky-50 text-sky-700 border-sky-200' },
  unknown: { label: 'URL-only', tone: 'bg-slate-100 text-slate-600 border-slate-200' },
}

function ConnectorBadge({ type }: { type: string | null }) {
  if (!type) return <Badge variant="outline" className="bg-slate-50 text-slate-500">detecting…</Badge>
  const meta = CONNECTOR_LABEL[type] ?? CONNECTOR_LABEL.unknown
  return <Badge variant="outline" className={meta.tone}>{meta.label}</Badge>
}

export function CompetitorManager() {
  const { competitors, isLoading, mutate } = useCompetitors()
  const { urls, isLoading: urlsLoading, mutate: mutateUrls } = useTrackedUrls()
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [newUrl, setNewUrl] = useState('')
  const [newUrlLabel, setNewUrlLabel] = useState('')
  const [newUrlCompetitor, setNewUrlCompetitor] = useState<string>('none')
  const [saving, setSaving] = useState(false)
  const [linkTarget, setLinkTarget] = useState<TrackedUrl | null>(null)

  const linkItem = async (itemId: string, itemTitle: string | null) => {
    if (!linkTarget) return
    try {
      await apiPost(`/api/price-intel/urls/${linkTarget.url_id}`, { item_id: itemId }, 'PUT')
      toast.success(`Linked to ${itemTitle ?? itemId} — fetching its price now`)
      setLinkTarget(null)
      await mutateUrls()
      setTimeout(() => { void mutateUrls() }, 12000)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to link item')
    }
  }

  const addCompetitor = async () => {
    if (!name.trim() || !baseUrl.trim()) {
      toast.error('Name and base URL are required')
      return
    }
    setSaving(true)
    try {
      await apiPost('/api/price-intel/competitors', { name: name.trim(), base_url: baseUrl.trim() })
      toast.success(`Added ${name.trim()} — detecting its catalog type in the background`)
      setName(''); setBaseUrl('')
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add competitor')
    } finally {
      setSaving(false)
    }
  }

  const removeCompetitor = async (id: string, label: string) => {
    try {
      await apiPost(`/api/price-intel/competitors/${id}`, undefined, 'DELETE')
      toast.success(`Disabled ${label}`)
      await mutate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to disable competitor')
    }
  }

  const addUrl = async () => {
    if (!/^https?:\/\//.test(newUrl.trim())) {
      toast.error('Enter a full product URL (https://…)')
      return
    }
    setSaving(true)
    try {
      await apiPost('/api/price-intel/urls', {
        url: newUrl.trim(),
        label: newUrlLabel.trim() || null,
        competitor_id: newUrlCompetitor === 'none' ? null : newUrlCompetitor,
      })
      toast.success('URL registered — it will be checked on the next scrape')
      setNewUrl(''); setNewUrlLabel('')
      await mutateUrls()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to register URL')
    } finally {
      setSaving(false)
    }
  }

  const removeUrl = async (id: string) => {
    try {
      await apiPost(`/api/price-intel/urls/${id}`, undefined, 'DELETE')
      await mutateUrls()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to remove URL')
    }
  }

  const enabledCompetitors = competitors.filter((c) => c.enabled)

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">Competitors</h3>
            <span className="text-xs text-muted-foreground">
              whole-store catalog scraping, nightly
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input placeholder="Name (e.g. Competitor Cycles)" value={name}
                   onChange={(e) => setName(e.target.value)} className="w-56" />
            <Input placeholder="https://store.example.com" value={baseUrl}
                   onChange={(e) => setBaseUrl(e.target.value)} className="w-72" />
            <Button size="sm" onClick={addCompetitor} disabled={saving}>
              <Plus className="h-4 w-4" /> Add competitor
            </Button>
          </div>
          {isLoading ? (
            <Skeleton className="h-32 rounded-lg" />
          ) : enabledCompetitors.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No competitors yet. Add a store above — its catalog type is detected automatically.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Store</TableHead>
                  <TableHead>Connector</TableHead>
                  <TableHead>Last scraped</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {enabledCompetitors.map((c) => (
                  <TableRow key={c.competitor_id}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell>
                      <a href={c.base_url} target="_blank" rel="noopener noreferrer"
                         className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                        {c.base_url.replace(/^https?:\/\//, '')}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    </TableCell>
                    <TableCell><ConnectorBadge type={c.connector_type} /></TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {c.last_scraped_at ? new Date(c.last_scraped_at).toLocaleString() : 'never'}
                    </TableCell>
                    <TableCell className="max-w-40 truncate text-xs text-muted-foreground"
                               title={c.last_scrape_status ?? undefined}>
                      {c.last_scrape_status ?? '—'}
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" title="Disable"
                              onClick={() => removeCompetitor(c.competitor_id, c.name)}>
                        <Trash2 className="h-4 w-4 text-muted-foreground" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold">Tracked URLs</h3>
            <span className="text-xs text-muted-foreground">
              specific product pages, flagged on any price change
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input placeholder="https://store.example.com/products/…" value={newUrl}
                   onChange={(e) => setNewUrl(e.target.value)} className="w-96" />
            <Input placeholder="Label (optional)" value={newUrlLabel}
                   onChange={(e) => setNewUrlLabel(e.target.value)} className="w-44" />
            <Select value={newUrlCompetitor} onValueChange={setNewUrlCompetitor}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Competitor (optional)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No competitor link</SelectItem>
                {enabledCompetitors.map((c) => (
                  <SelectItem key={c.competitor_id} value={c.competitor_id}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" onClick={addUrl} disabled={saving}>
              <Plus className="h-4 w-4" /> Track URL
            </Button>
          </div>
          {urlsLoading ? (
            <Skeleton className="h-24 rounded-lg" />
          ) : urls.filter((u) => u.enabled).length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No tracked URLs yet. Paste a competitor product page to watch its price.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>URL</TableHead>
                  <TableHead>Label</TableHead>
                  <TableHead>Linked item</TableHead>
                  <TableHead>Last checked</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {urls.filter((u) => u.enabled).map((u) => (
                  <TableRow key={u.url_id}>
                    <TableCell className="max-w-96">
                      <a href={u.url} target="_blank" rel="noopener noreferrer"
                         className="inline-flex max-w-full items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                        <span className="truncate">{u.url.replace(/^https?:\/\//, '')}</span>
                        <ExternalLink className="h-3 w-3 shrink-0" />
                      </a>
                    </TableCell>
                    <TableCell className="text-sm">{u.label ?? '—'}</TableCell>
                    <TableCell className="max-w-56">
                      {u.item_id ? (
                        <div>
                          <a href={lightspeedItemUrl(u.item_id)} target="_blank"
                             rel="noopener noreferrer" title="Open in Lightspeed"
                             className="block truncate text-sm font-medium hover:underline">
                            {u.item_title ?? u.label ?? 'tracked item'}
                          </a>
                          <button className="block max-w-full truncate text-left text-xs text-muted-foreground"
                                  title="Change linked item" onClick={() => setLinkTarget(u)}>
                            {itemIdentity({ brand: u.item_brand, upc: u.item_upc, systemSku: u.item_system_sku })}
                          </button>
                        </div>
                      ) : (
                        <Button variant="outline" size="sm" className="h-7 text-xs"
                                onClick={() => setLinkTarget(u)}>
                          <Link2 className="h-3.5 w-3.5" /> Link item
                        </Button>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {u.last_scraped_at ? new Date(u.last_scraped_at).toLocaleString() : 'never'}
                    </TableCell>
                    <TableCell className="max-w-40 truncate text-xs text-muted-foreground"
                               title={u.last_status ?? undefined}>
                      {u.last_status ?? '—'}
                    </TableCell>
                    <TableCell>
                      <Button variant="ghost" size="sm" title="Stop tracking"
                              onClick={() => removeUrl(u.url_id)}>
                        <Trash2 className="h-4 w-4 text-muted-foreground" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={linkTarget !== null} onOpenChange={(open) => !open && setLinkTarget(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Link URL to a catalog item</DialogTitle>
          </DialogHeader>
          <p className="truncate text-xs text-muted-foreground">{linkTarget?.url}</p>
          <ItemSearchPicker
            actionLabel="Link"
            placeholder="Search our catalog (name / SKU / id)…"
            onSelect={(item) => linkItem(item.item_id, item.title)}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}
