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
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, apiPost, useCompetitors, useTrackedUrls } from '@/lib/price-intel/hooks'
import { itemIdentity, lightspeedItemUrl } from '@/lib/price-intel/format'
import type {
  Competitor, CompetitorCrawlSettings, TrackedUrl, VariantCandidate,
  VariantSelectionRequired,
} from '@/lib/price-intel/types'
import { ItemSearchPicker } from './item-search-picker'
import { BellOff, ExternalLink, Globe, Link2, Plus, Settings2, Trash2 } from 'lucide-react'

const CONNECTOR_LABEL: Record<string, { label: string; tone: string }> = {
  shopify_json: { label: 'Shopify (catalog)', tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  shopify_html: { label: 'Shopify (pages)', tone: 'bg-sky-50 text-sky-700 border-sky-200' },
  sitemap_html: { label: 'Sitemap (variant-aware)', tone: 'bg-violet-50 text-violet-700 border-violet-200' },
  unknown: { label: 'URL-only', tone: 'bg-slate-100 text-slate-600 border-slate-200' },
  benchmark: { label: 'Reference feed', tone: 'bg-sky-50 text-sky-700 border-sky-200' },
}

// Synthetic sources (the Google Merchant Center reference prices) are pulled from
// an API, not crawled: there's no catalog coverage to report, and disabling them
// belongs in Admin (the schedule toggle), not behind a per-store delete.
const BENCHMARK_CONNECTOR = 'benchmark'

function ConnectorBadge({ type }: { type: string | null }) {
  if (!type) return <Badge variant="outline" className="bg-slate-50 text-slate-500">detecting…</Badge>
  const meta = CONNECTOR_LABEL[type] ?? CONNECTOR_LABEL.unknown
  return <Badge variant="outline" className={meta.tone}>{meta.label}</Badge>
}

// Last catalog-crawl coverage from crawl_state_json. `rotating` = the store is
// larger than the nightly page budget, so each night crawls the next slice.
function crawlCoverage(json: string | null | undefined):
    { products: number; rotating: boolean } | null {
  if (!json) return null
  try {
    const s = JSON.parse(json)
    if (typeof s?.products_seen !== 'number') return null
    return { products: s.products_seen, rotating: !!s.cap_hit }
  } catch {
    return null
  }
}

function parseSettings(json: string | null | undefined): CompetitorCrawlSettings {
  if (!json) return {}
  try {
    const parsed = JSON.parse(json)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

// Muting is invisible until you open the dialog, so the row says so — otherwise
// a store that stopped appearing in the feed just looks broken.
function MutedBadge({ settings }: { settings: CompetitorCrawlSettings }) {
  const muted = [
    settings.mute_price_alerts && 'price',
    settings.mute_stock_alerts && 'stock',
    settings.mute_map_alerts && 'MAP',
  ].filter(Boolean) as string[]
  if (muted.length === 0) return null
  const label = muted.length > 1
    ? `${muted.slice(0, -1).join(', ')} & ${muted[muted.length - 1]}`
    : muted[0]
  return (
    <Badge variant="outline"
           className="gap-1 border-slate-200 bg-slate-50 px-1.5 py-0 text-[11px] text-slate-500"
           title={`${label} alerts are muted for this store — they stay out of the change feed and Slack`}>
      <BellOff className="h-3 w-3" />
      {label} muted
    </Badge>
  )
}

/**
 * Per-competitor settings: notification mutes plus crawl overrides. Every field
 * is optional and blank/off means "use the global default", so the dialog only
 * ever sends the keys that were actually set — a store with nothing set behaves
 * exactly as it did before.
 */
function CompetitorSettingsDialog(
  { competitor, onClose, onSaved }:
  { competitor: Competitor | null; onClose: () => void; onSaved: () => Promise<unknown> },
) {
  const [form, setForm] = useState<CompetitorCrawlSettings>({})
  const [saving, setSaving] = useState(false)
  // Re-seed the form whenever a different competitor's dialog is opened.
  const [seededFor, setSeededFor] = useState<string | null>(null)
  if (competitor && seededFor !== competitor.competitor_id) {
    setSeededFor(competitor.competitor_id)
    setForm(parseSettings(competitor.settings_json))
  }

  const set = <K extends keyof CompetitorCrawlSettings>(
    key: K, value: CompetitorCrawlSettings[K],
  ) => setForm((prev) => {
    const next = { ...prev }
    // An emptied field must delete the key, not store '' — the backend treats a
    // present key as an override and a missing one as "fall back to global".
    if (value === undefined || value === '' || value === null) delete next[key]
    else next[key] = value
    return next
  })

  const numeric = (key: keyof CompetitorCrawlSettings) => (raw: string) => {
    const value = raw.trim()
    if (!value) return set(key, undefined)
    const parsed = Number(value)
    if (Number.isFinite(parsed) && parsed > 0) set(key, parsed as never)
  }

  const save = async () => {
    if (!competitor) return
    setSaving(true)
    try {
      await apiPost('/api/price-intel/competitors', {
        ...competitor,
        settings: form,
      })
      toast.success(`Settings saved for ${competitor.name}`)
      onClose()
      await onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={competitor !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Settings — {competitor?.name}</DialogTitle>
          <DialogDescription>
            Notification mutes apply everywhere; crawl fields left blank use the
            global default and only affect the nightly catalog crawl.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Notifications
            </p>
            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="pr-3">
                <Label className="text-xs">Price change alerts</Label>
                <p className="text-[11px] text-muted-foreground">
                  Off hides this store&apos;s price drops and increases from the
                  change feed, the unread badge and the Slack digest.
                </p>
              </div>
              <Switch checked={form.mute_price_alerts !== true}
                      onCheckedChange={(on) =>
                        set('mute_price_alerts', on ? undefined : true)} />
            </div>
            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="pr-3">
                <Label className="text-xs">Stock change alerts</Label>
                <p className="text-[11px] text-muted-foreground">
                  Off hides this store&apos;s in-stock / out-of-stock changes.
                </p>
              </div>
              <Switch checked={form.mute_stock_alerts !== true}
                      onCheckedChange={(on) =>
                        set('mute_stock_alerts', on ? undefined : true)} />
            </div>
            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="pr-3">
                <Label className="text-xs">MAP violation alerts</Label>
                <p className="text-[11px] text-muted-foreground">
                  Off hides this store&apos;s MAP violations from the change feed
                  and stops its red Slack pings.
                </p>
              </div>
              <Switch checked={form.mute_map_alerts !== true}
                      onCheckedChange={(on) =>
                        set('mute_map_alerts', on ? undefined : true)} />
            </div>
            <p className="text-[11px] text-muted-foreground">
              Muting only hides events — they keep being recorded, so turning an
              alert back on restores this store&apos;s history. Muting MAP never
              hides the MAP violation badge or KPI tile on tracked products: those
              are read live from the market min, not from alerts.
            </p>
          </div>

          <p className="border-t pt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Crawl
          </p>

          <div className="space-y-1.5">
            <Label className="text-xs">Brand filter</Label>
            <Select value={form.brand_filter ?? 'auto'}
                    onValueChange={(v) => set('brand_filter',
                      v === 'auto' ? undefined : (v as 'on' | 'off'))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (recommended)</SelectItem>
                <SelectItem value="on">Always filter to tracked brands</SelectItem>
                <SelectItem value="off">Never filter on brand</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[11px] text-muted-foreground">
              Auto drops the brand filter on stores that don&apos;t put brand names in
              their product URLs — on those, filtering would discard the whole catalog.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Only crawl paths matching</Label>
            <Input placeholder="/product/" value={form.url_allow_pattern ?? ''}
                   onChange={(e) => set('url_allow_pattern', e.target.value)} />
            <p className="text-[11px] text-muted-foreground">
              Regex against the URL path. Narrows the sitemap to real product pages
              so the nightly page budget isn&apos;t spent on anything else.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Never crawl paths matching</Label>
            <Input placeholder="/gift-card|/rental" value={form.url_deny_pattern ?? ''}
                   onChange={(e) => set('url_deny_pattern', e.target.value)} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Seconds between requests</Label>
              <Input inputMode="decimal" placeholder="1.0"
                     defaultValue={form.request_interval_seconds ?? ''}
                     onChange={(e) => numeric('request_interval_seconds')(e.target.value)} />
              <p className="text-[11px] text-muted-foreground">
                Lower crawls more of the catalog each night. A 429 automatically
                backs this store off for the rest of the run.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Max product pages / night</Label>
              <Input inputMode="numeric" placeholder="300"
                     defaultValue={form.max_product_pages ?? ''}
                     onChange={(e) => numeric('max_product_pages')(e.target.value)} />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Sitemap URLs (one per line)</Label>
            <Textarea rows={2} className="text-xs"
                      placeholder="https://store.example.com/sitemap_products_1.xml"
                      defaultValue={(form.sitemap_urls ?? []).join('\n')}
                      onChange={(e) => {
                        const lines = e.target.value.split('\n').map((l) => l.trim()).filter(Boolean)
                        set('sitemap_urls', lines.length ? lines : undefined)
                      }} />
            <p className="text-[11px] text-muted-foreground">
              Only needed when robots.txt and /sitemap.xml don&apos;t reveal the catalog.
            </p>
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <Label className="text-xs">Stay on this domain</Label>
              <p className="text-[11px] text-muted-foreground">
                Skip sitemap entries pointing at other hosts. Turn off only for a
                store that genuinely serves products from a second domain.
              </p>
            </div>
            <Switch checked={form.confine_to_domain !== false}
                    onCheckedChange={(on) => set('confine_to_domain', on ? undefined : false)} />
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
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
  const [settingsTarget, setSettingsTarget] = useState<Competitor | null>(null)
  const [linkChoice, setLinkChoice] = useState<{
    itemId: string; itemTitle: string | null; candidates: VariantCandidate[]
  } | null>(null)

  const linkItem = async (itemId: string, itemTitle: string | null,
                          candidate?: VariantCandidate) => {
    if (!linkTarget) return
    try {
      await apiPost(`/api/price-intel/urls/${linkTarget.url_id}`, {
        item_id: itemId,
        competitor_sku: candidate?.sku,
        competitor_variant_id: candidate?.variant_id,
        competitor_gtin: candidate?.gtin,
        variant_options: candidate?.variant_options,
      }, 'PUT')
      toast.success(`Linked to ${itemTitle ?? itemId} — fetching its price now`)
      setLinkTarget(null)
      setLinkChoice(null)
      await mutateUrls()
      setTimeout(() => { void mutateUrls() }, 12000)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.detail as VariantSelectionRequired
        if (detail?.code === 'variant_selection_required') {
          setLinkChoice({ itemId, itemTitle, candidates: detail.candidates })
          return
        }
      }
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
                  <TableHead>Coverage</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {enabledCompetitors.map((c) => (
                  <TableRow key={c.competitor_id}>
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-1.5">
                        {c.name}
                        <MutedBadge settings={parseSettings(c.settings_json)} />
                      </span>
                    </TableCell>
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
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {(() => {
                        if (c.connector_type === BENCHMARK_CONNECTOR) return 'not crawled'
                        const cov = crawlCoverage(c.crawl_state_json)
                        if (!cov) return '—'
                        return (
                          <span className="inline-flex items-center gap-1.5">
                            {cov.products.toLocaleString()} products
                            {cov.rotating && (
                              <Badge variant="outline"
                                     className="border-sky-200 bg-sky-50 px-1 py-0 text-[10px] text-sky-700"
                                     title="Catalog exceeds the nightly page budget — each night crawls the next slice">
                                rotating
                              </Badge>
                            )}
                          </span>
                        )
                      })()}
                    </TableCell>
                    <TableCell className="max-w-40 truncate text-xs text-muted-foreground"
                               title={c.last_scrape_status ?? undefined}>
                      {c.last_scrape_status ?? '—'}
                    </TableCell>
                    <TableCell>
                      {c.connector_type !== BENCHMARK_CONNECTOR && (
                        <div className="flex items-center">
                          <Button variant="ghost" size="sm"
                                  title="Settings — notification mutes and crawl overrides"
                                  onClick={() => setSettingsTarget(c)}>
                            <Settings2 className={`h-4 w-4 ${
                              c.settings_json ? 'text-sky-600' : 'text-muted-foreground'}`} />
                          </Button>
                          <Button variant="ghost" size="sm" title="Disable"
                                  onClick={() => removeCompetitor(c.competitor_id, c.name)}>
                            <Trash2 className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </div>
                      )}
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

      <CompetitorSettingsDialog competitor={settingsTarget}
                                onClose={() => setSettingsTarget(null)}
                                onSaved={mutate} />

      <Dialog open={linkTarget !== null} onOpenChange={(open) => {
        if (!open) { setLinkTarget(null); setLinkChoice(null) }
      }}>
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
          {linkChoice && (
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">Choose the matching competitor variant</p>
              <div className="max-h-64 space-y-1 overflow-y-auto">
                {linkChoice.candidates.map((candidate, i) => (
                  <button key={`${candidate.variant_id ?? candidate.sku ?? i}`}
                          type="button"
                          onClick={() => void linkItem(linkChoice.itemId, linkChoice.itemTitle, candidate)}
                          className="flex w-full items-center justify-between rounded border px-3 py-2 text-left text-sm hover:bg-muted">
                    <span>
                      <span className="block font-medium">
                        {candidate.variant_options.join(' / ') || candidate.title || 'Variant'}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {candidate.sku ? `SKU ${candidate.sku}` : candidate.gtin ? `UPC ${candidate.gtin}` : 'No SKU'}
                      </span>
                    </span>
                    <span className="font-semibold tabular-nums">
                      {candidate.price == null ? '—' : `$${Number(candidate.price).toFixed(2)}`}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
