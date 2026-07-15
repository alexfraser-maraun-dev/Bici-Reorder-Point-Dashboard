'use client'

import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  createPlanningRun,
  createPODraft,
  deletePODraft,
  previewPODraft,
  reconcilePODraft,
  transitionPODraft,
  updatePODraft,
  setPODraftTarget,
  useLightspeedPoAccess,
  useLatestPlanningRun,
  useOpenOrders,
  usePlanningModels,
  usePODraft,
  usePODrafts,
} from '@/lib/hooks'
import type {
  ForecastRun,
  LightspeedOpenOrder,
  LightspeedPreview,
  MonthlyPlanningRollup,
  PODraftLine,
  POReconciliation,
  PurchaseOrderDraft,
  PurchaseRecommendation,
  PlanningConfig,
  PlanningScope,
} from '@/lib/types'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Eye,
  FileJson,
  PackagePlus,
  Play,
  RefreshCw,
  Save,
  ShoppingCart,
  Trash2,
  ExternalLink,
  SlidersHorizontal,
} from 'lucide-react'

interface PurchaseOrdersProps {
  data: unknown
  isLoading: boolean
}

type Measure = 'units' | 'cogs' | 'revenue' | 'spend'

const RECON_LABELS: Record<POReconciliation, string> = {
  new_po: 'New PO',
  append_to_open_po: 'Add to unsent PO',
  already_on_po: 'Already covered',
}

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD' })
const number = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 1 })
const SHOP_NAMES: Record<string, string> = {
  '2': 'Bici Victoria',
  '3': 'Bici Adanac',
  '20': 'Bici Langford',
}
const MODEL_OPTIONS: PlanningConfig['model'][] = [
  'auto', 'current_velocity', 'seasonal_naive', 'hierarchical_seasonal', 'tsb', 'ets_damped',
]
const MODEL_LEGEND_FALLBACK: Record<string, string> = {
  auto: 'Backtests eligible models and keeps the strongest baseline or challenger.',
  current_velocity: 'Blends the latest 4, 8 and 13 weeks; responsive and non-seasonal.',
  seasonal_naive: 'Repeats the same week from the prior year.',
  hierarchical_seasonal: 'Shapes local demand with category-location seasonality and damped trend.',
  tsb: 'Models intermittent demand occurrence and positive demand size separately.',
  ets_damped: 'Exponentially smoothed level and damped trend for dense, long-history products.',
}
const lsItemUrl = (itemId: string) => `https://us.merchantos.com/?name=item.views.item&form_name=view&id=${encodeURIComponent(itemId)}&tab=details`

function ProductIdentity({ description, sku, itemId }: { description?: string | null; sku?: string | null; itemId: string }) {
  return (
    <div className="min-w-[240px]">
      <div className="max-w-[360px] font-medium leading-tight">{description || 'Unnamed item'}</div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <a className="inline-flex items-center gap-1 font-mono text-primary hover:underline" href={lsItemUrl(itemId)} target="_blank" rel="noreferrer">
          {sku || `Item ${itemId}`}<ExternalLink className="h-3 w-3" />
        </a>
        <span>LS item {itemId}</span>
      </div>
    </div>
  )
}

function measureValue(row: MonthlyPlanningRollup, measure: Measure) {
  if (measure === 'units') return row.units
  if (measure === 'cogs') return row.cogs
  if (measure === 'revenue') return row.revenue
  return 0
}

function formatMeasure(value: number, measure: Measure) {
  return measure === 'units' ? number.format(value) : money.format(value)
}

function DraftCard({ draft, onChanged }: { draft: PurchaseOrderDraft; onChanged: () => void }) {
  const { data: detail, refetch } = usePODraft(draft.draft_id)
  const current = (detail || draft) as PurchaseOrderDraft
  const [lines, setLines] = useState<PODraftLine[]>([])
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<LightspeedPreview | null>(null)
  const [reconciled, setReconciled] = useState<LightspeedPreview | null>(null)
  const {
    data: openOrders,
    meta: openOrderMeta,
    isLoading: ordersLoading,
    isRefreshing: ordersRefreshing,
    error: openOrdersError,
    refetch: refreshOpenOrders,
  } = useOpenOrders(current.vendor_id, current.shop_id)
  const eligibleOrders = (openOrders as LightspeedOpenOrder[]).filter((order) => order.po_state === 'unsent')
  const poSnapshotLabel = openOrderMeta?.snapshot_at
    ? new Date(openOrderMeta.snapshot_at).toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' })
    : null

  useEffect(() => {
    if (current.lines) setLines(current.lines)
  }, [current.lines])

  const totalUnits = lines.reduce((sum, line) => sum + Number(line.quantity || 0), 0)
  const totalSpend = lines.reduce(
    (sum, line) => sum + Number(line.quantity || 0) * Number(line.landed_cost ?? line.unit_cost ?? 0),
    0,
  )
  const editable = current.status === 'draft'

  const perform = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true)
    try {
      await work()
      toast.success(success)
      await refetch()
      onChanged()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Purchase-order action failed')
    } finally {
      setBusy(false)
    }
  }

  const save = () => perform(
    () => updatePODraft(current.draft_id, current.version, lines),
    'Draft changes saved',
  )

  const chooseTarget = (value: string) => perform(
    () => setPODraftTarget(current.draft_id, current.version, value === 'new' ? null : value),
    value === 'new' ? 'Routing set to a new unsent PO' : `Routing set to Lightspeed PO #${value}`,
  )

  const refreshOrderSnapshot = async () => {
    try {
      await refreshOpenOrders(true)
      toast.success('Loaded a fresh complete Lightspeed PO snapshot')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Lightspeed PO refresh failed closed')
    }
  }

  const approve = () => perform(
    () => transitionPODraft(current.draft_id, current.version, 'approved'),
    'Draft approved for preview',
  )

  const reconcile = async () => {
    setBusy(true)
    try {
      const response = await reconcilePODraft(current.draft_id)
      setReconciled(response.data)
      toast.success('Reconciled against a complete Lightspeed PO snapshot')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Reconciliation failed closed')
    } finally {
      setBusy(false)
    }
  }

  const generatePreview = async () => {
    setBusy(true)
    try {
      const response = await previewPODraft(current.draft_id)
      setPreview(response.data)
      await refetch()
      onChanged()
      toast.success('Lightspeed preview generated — no changes were sent')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Preview failed')
    } finally {
      setBusy(false)
    }
  }

  const downloadPreview = () => {
    if (!preview) return
    const blob = new Blob([JSON.stringify(preview, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `lightspeed-preview-${current.draft_id}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-muted p-2"><ShoppingCart className="h-4 w-4" /></div>
          <div>
            <div className="font-medium">{current.vendor_name || `Vendor ${current.vendor_id}`}</div>
            <div className="text-sm text-muted-foreground">
              {SHOP_NAMES[current.shop_id] || `Shop ${current.shop_id}`} · {lines.length} lines · {totalUnits} units · {money.format(totalSpend)}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={current.status === 'cancelled' ? 'destructive' : 'secondary'}>{current.status}</Badge>
          {editable && <Button size="sm" variant="outline" onClick={save} disabled={busy}><Save className="mr-1 h-3.5 w-3.5" />Save</Button>}
          {editable && <Button size="sm" variant="outline" onClick={reconcile} disabled={busy}><RefreshCw className="mr-1 h-3.5 w-3.5" />Reconcile</Button>}
          {editable && <Button size="sm" onClick={approve} disabled={busy || lines.length === 0}><CheckCircle2 className="mr-1 h-3.5 w-3.5" />Approve</Button>}
          {(current.status === 'approved' || current.status === 'previewed') && (
            <Button size="sm" onClick={generatePreview} disabled={busy}><Eye className="mr-1 h-3.5 w-3.5" />Generate Lightspeed Preview</Button>
          )}
          {current.status !== 'cancelled' && (
            <Button size="icon" variant="ghost" disabled={busy} onClick={() => perform(() => deletePODraft(current.draft_id), 'Draft cancelled')}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {reconciled && (
        <div className="border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          Reconciliation: {reconciled.operations.length} proposed action(s); {reconciled.read_only_inbound_orders.length} ordered/received PO(s) retained as read-only supply.
        </div>
      )}

      {editable && (
        <div className="flex flex-wrap items-center gap-3 border-b bg-muted/20 px-4 py-3">
          <div className="min-w-[180px]">
            <div className="text-sm font-medium">Lightspeed routing</div>
            <div className="text-xs text-muted-foreground">
              Choose with buyer context; only unsent, unreceived POs are eligible.
              {poSnapshotLabel && ` Snapshot ${poSnapshotLabel} · ${openOrderMeta.total_order_count} total open POs.`}
            </div>
          </div>
          <Select value={current.lightspeed_order_id || 'new'} onValueChange={chooseTarget} disabled={busy || ordersLoading || ordersRefreshing || !!openOrdersError}>
            <SelectTrigger className="w-full sm:w-[360px]"><SelectValue placeholder="Select an open PO" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="new">Create a new unsent PO (preview only)</SelectItem>
              {eligibleOrders.map((order) => (
                <SelectItem key={order.orderID} value={String(order.orderID)}>
                  PO #{order.orderID}{order.refNum ? ` · ${order.refNum}` : ''}{order.createTime ? ` · created ${String(order.createTime).slice(0, 10)}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            onClick={refreshOrderSnapshot}
            disabled={ordersLoading || ordersRefreshing}
          >
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${ordersRefreshing ? 'animate-spin' : ''}`} />
            Refresh PO list
          </Button>
          {eligibleOrders.length > 0 && !current.lightspeed_order_id && (
            <Badge variant="outline">{eligibleOrders.length} eligible open PO{eligibleOrders.length === 1 ? '' : 's'}</Badge>
          )}
          {openOrdersError && (
            <Badge variant="destructive">Complete PO snapshot unavailable</Badge>
          )}
        </div>
      )}

      <div className="overflow-x-auto"><Table className="min-w-[980px]">
        <TableHeader><TableRow>
          <TableHead>Item / SKU</TableHead><TableHead className="w-28">Quantity</TableHead>
          <TableHead className="w-32">Landed cost</TableHead><TableHead>Need by</TableHead>
          <TableHead>Routing</TableHead>{editable && <TableHead className="w-12" />}
        </TableRow></TableHeader>
        <TableBody>
          {lines.map((line, index) => (
            <TableRow key={line.line_id || `${line.item_id}-${index}`}>
              <TableCell>
                <ProductIdentity description={line.description} sku={line.sku} itemId={line.item_id} />
              </TableCell>
              <TableCell>
                <Input type="number" min={0} disabled={!editable} value={line.quantity}
                  onChange={(event) => setLines(lines.map((value, i) => i === index ? { ...value, quantity: Number(event.target.value) } : value))} />
              </TableCell>
              <TableCell>
                <Input type="number" min={0} step="0.01" disabled={!editable} value={line.landed_cost ?? line.unit_cost ?? ''}
                  onChange={(event) => setLines(lines.map((value, i) => i === index ? { ...value, landed_cost: Number(event.target.value), unit_cost: Number(event.target.value) } : value))} />
              </TableCell>
              <TableCell className="text-sm">{line.need_by_week || '—'}</TableCell>
              <TableCell><Badge variant="outline">
                {current.lightspeed_order_id ? `PO #${current.lightspeed_order_id}` : eligibleOrders.length ? 'Choose route' : (RECON_LABELS[line.reconciliation] || line.reconciliation)}
              </Badge></TableCell>
              {editable && <TableCell><Button size="icon" variant="ghost" onClick={() => setLines(lines.filter((_, i) => i !== index))}><Trash2 className="h-3.5 w-3.5" /></Button></TableCell>}
            </TableRow>
          ))}
          {editable && (
            <TableRow><TableCell colSpan={6}>
              <Button size="sm" variant="ghost" onClick={() => setLines([...lines, {
                sku: null, item_id: '', location_id: current.shop_id, quantity: 1,
                unit_cost: null, landed_cost: null, source: 'manual', reconciliation: 'new_po',
              }])}><PackagePlus className="mr-1 h-3.5 w-3.5" />Add manual item line</Button>
            </TableCell></TableRow>
          )}
        </TableBody>
      </Table></div>

      <Dialog open={!!preview} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="max-h-[85vh] max-w-4xl overflow-auto">
          <DialogHeader>
            <DialogTitle>Lightspeed change preview</DialogTitle>
            <DialogDescription>Exact proposed payload. No write method was called and no Lightspeed data changed.</DialogDescription>
          </DialogHeader>
          <Alert><FileJson className="h-4 w-4" /><AlertTitle>Preview only</AlertTitle><AlertDescription>
            {preview?.operations.length || 0} action(s), writes_performed = false
          </AlertDescription></Alert>
          <pre className="overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(preview?.operations, null, 2)}</pre>
          <Button variant="outline" onClick={downloadPreview}><Download className="mr-1 h-4 w-4" />Download audit JSON</Button>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function PurchaseOrders({ isLoading }: PurchaseOrdersProps) {
  const { data: drafts, isLoading: draftsLoading, refetch } = usePODrafts()
  const { data: latestRun, refetch: refetchLatestRun } = useLatestPlanningRun()
  const { data: modelLegend } = usePlanningModels()
  const { poAccess } = useLightspeedPoAccess()
  const [run, setRun] = useState<ForecastRun | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [measure, setMeasure] = useState<Measure>('units')
  const [busy, setBusy] = useState(false)
  const [scopeType, setScopeType] = useState<PlanningScope>('auto_replen')
  const [scopeValue, setScopeValue] = useState('')
  const [planShop, setPlanShop] = useState('all')
  const [config, setConfig] = useState<PlanningConfig>({
    model: 'auto', service_quantile: 0.9, history_years: 3,
    review_period_weeks: 4, demand_multiplier: 1,
    seasonal_smoothing_weeks: 5, seasonal_shrinkage: 1, lead_time_days: null,
  })
  const [shopFilter, setShopFilter] = useState('all')
  const [brandFilter, setBrandFilter] = useState('all')
  const [vendorFilter, setVendorFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [needByFilter, setNeedByFilter] = useState('all')
  const effectiveModelLegend = Object.keys(modelLegend).length ? modelLegend : MODEL_LEGEND_FALLBACK

  useEffect(() => {
    if (latestRun) {
      const restored = latestRun as ForecastRun
      setRun(restored)
      if (restored.config) setConfig((current) => ({ ...current, ...restored.config }))
      if (restored.scope_type) setScopeType(restored.scope_type)
      if (restored.scope_value) setScopeValue(restored.scope_value)
    }
  }, [latestRun])

  const actionable = useMemo(
    () => run?.recommendations.filter((row) => !row.blocked && row.recommended_quantity > 0) || [],
    [run],
  )
  const eligible = useMemo(() => actionable.filter((row) => {
    if (shopFilter !== 'all' && row.location_id !== shopFilter) return false
    if (brandFilter !== 'all' && (row.brand || 'Unmapped') !== brandFilter) return false
    if (vendorFilter !== 'all' && (row.vendor_name || 'Unmapped') !== vendorFilter) return false
    if (categoryFilter !== 'all' && (row.category_top_level || row.category || 'Uncategorized') !== categoryFilter) return false
    if (needByFilter !== 'all') {
      if (!row.need_by_week) return needByFilter === 'monitor'
      const days = Math.ceil((new Date(row.need_by_week).getTime() - Date.now()) / 86400000)
      if (needByFilter === '4w' && days > 28) return false
      if (needByFilter === '8w' && (days <= 28 || days > 56)) return false
      if (needByFilter === '13w' && (days <= 56 || days > 91)) return false
      if (needByFilter === 'later' && days <= 91) return false
    }
    return true
  }), [actionable, shopFilter, brandFilter, vendorFilter, categoryFilter, needByFilter])
  const exceptions = useMemo(() => run?.recommendations.filter((row) => row.blocked) || [], [run])
  const filterOptions = useMemo(() => ({
    brands: [...new Set(actionable.map((row) => row.brand || 'Unmapped'))].sort(),
    vendors: [...new Set(actionable.map((row) => row.vendor_name || 'Unmapped'))].sort(),
    categories: [...new Set(actionable.map((row) => row.category_top_level || row.category || 'Uncategorized'))].sort(),
  }), [actionable])
  const monthly = useMemo(() => {
    const result = new Map<string, number>()
    for (const row of run?.monthly_rollups || []) {
      result.set(row.month, (result.get(row.month) || 0) + measureValue(row, measure))
    }
    if (measure === 'spend') {
      const spend = (run?.recommendations || []).reduce((sum, row) => sum + Number(row.purchase_commitment_spend || 0), 0)
      result.set(run?.as_of_date.slice(0, 7) || '', spend)
    }
    return [...result.entries()].sort().slice(0, 12)
  }, [run, measure])

  const runPlan = async () => {
    setBusy(true)
    try {
      if (scopeType !== 'auto_replen' && !scopeValue.trim()) {
        throw new Error('Enter the brand, vendor, category, or item IDs to evaluate.')
      }
      const response = await createPlanningRun({
        horizon_weeks: 52,
        location_ids: planShop === 'all' ? undefined : [planShop],
        scope_type: scopeType,
        scope_value: scopeType === 'item_ids' ? undefined : scopeValue.trim() || undefined,
        item_ids: scopeType === 'item_ids' ? scopeValue.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean) : undefined,
        config,
      })
      setRun(response.data)
      await refetchLatestRun()
      setSelected(new Set())
      toast.success(`Planning run created with ${response.data.recommendation_count} SKU-location forecasts`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Planning run failed')
    } finally {
      setBusy(false)
    }
  }

  const buildDrafts = async () => {
    if (!run || selected.size === 0) return
    setBusy(true)
    try {
      const response = await createPODraft(run.run_id, [...selected])
      toast.success(`Created ${response.data.length} local draft(s) from ${selected.size} selected recommendations`)
      setSelected(new Set())
      refetch()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Draft creation failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Demand Planning &amp; Procurement Workbench</h2>
          <p className="text-sm text-muted-foreground">Review demand → select recommendations → build PO → reconcile → approve → preview.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}><RefreshCw className="mr-1 h-3.5 w-3.5" />Refresh drafts</Button>
          <Button onClick={runPlan} disabled={busy || isLoading}><Play className="mr-1 h-4 w-4" />{busy ? 'Working…' : 'Run 52-week plan'}</Button>
        </div>
      </div>

      <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:bg-amber-950/20 dark:text-amber-100">
        <AlertTriangle className="h-4 w-4" /><AlertTitle>Preview-only Lightspeed rollout</AlertTitle>
        <AlertDescription>PO creation and line updates are disabled. The workbench generates auditable payloads but cannot send them to Lightspeed.</AlertDescription>
      </Alert>
      {poAccess === false && <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>Read access unavailable</AlertTitle><AlertDescription>A complete PO snapshot is required; reconciliation and preview will fail closed.</AlertDescription></Alert>}

      <div className="rounded-xl border p-4">
        <div className="mb-4 flex items-center gap-2"><SlidersHorizontal className="h-4 w-4" /><div><div className="font-medium">Planning scope &amp; mathematics</div><div className="text-xs text-muted-foreground">Use the safe tagged catalog, or explicitly call forward a brand, vendor, category, or SKU list.</div></div></div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div><div className="mb-1 text-xs font-medium">Evaluation scope</div><Select value={scopeType} onValueChange={(value) => setScopeType(value as PlanningScope)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="auto_replen">Auto-replen tagged products</SelectItem><SelectItem value="brand">Specific brand</SelectItem><SelectItem value="vendor">Specific vendor</SelectItem><SelectItem value="category">Top-level category</SelectItem><SelectItem value="item_ids">SKU / item ID list</SelectItem></SelectContent></Select></div>
          <div><div className="mb-1 text-xs font-medium">Scope value</div><Input value={scopeValue} onChange={(event) => setScopeValue(event.target.value)} disabled={scopeType === 'auto_replen'} placeholder={scopeType === 'item_ids' ? 'Comma-separated SKUs or IDs' : scopeType === 'auto_replen' ? 'Not required' : `Enter ${scopeType}`} /></div>
          <div><div className="mb-1 text-xs font-medium">Plan shop</div><Select value={planShop} onValueChange={setPlanShop}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All Bici shops</SelectItem>{Object.entries(SHOP_NAMES).map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent></Select></div>
          <div><div className="mb-1 text-xs font-medium">Forecast model</div><Select value={config.model} onValueChange={(value) => setConfig({ ...config, model: value as PlanningConfig['model'] })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{MODEL_OPTIONS.map((key) => <SelectItem key={key} value={key}>{key === 'auto' ? 'Automatic champion' : key.replaceAll('_', ' ')}</SelectItem>)}</SelectContent></Select></div>
          <div><div className="mb-1 text-xs font-medium">Service target</div><Select value={String(config.service_quantile)} onValueChange={(value) => setConfig({ ...config, service_quantile: Number(value) as PlanningConfig['service_quantile'] })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="0.8">P80</SelectItem><SelectItem value="0.9">P90 balanced default</SelectItem><SelectItem value="0.95">P95</SelectItem></SelectContent></Select></div>
          <div><div className="mb-1 text-xs font-medium">Demand multiplier</div><Input type="number" min={0} max={3} step={0.05} value={config.demand_multiplier} onChange={(event) => setConfig({ ...config, demand_multiplier: Number(event.target.value) })} /></div>
          <div><div className="mb-1 text-xs font-medium">Review period (weeks)</div><Input type="number" min={1} max={26} value={config.review_period_weeks} onChange={(event) => setConfig({ ...config, review_period_weeks: Number(event.target.value) })} /></div>
          <div><div className="mb-1 text-xs font-medium">Lead-time override (days)</div><Input type="number" min={1} max={365} value={config.lead_time_days ?? ''} onChange={(event) => setConfig({ ...config, lead_time_days: event.target.value ? Number(event.target.value) : null })} placeholder="Use vendor history" /></div>
          <div><div className="mb-1 text-xs font-medium">History (years)</div><Input type="number" min={1} max={5} value={config.history_years} onChange={(event) => setConfig({ ...config, history_years: Number(event.target.value) })} /></div>
          <div><div className="mb-1 text-xs font-medium">Season smoothing (weeks)</div><Input type="number" min={1} max={13} value={config.seasonal_smoothing_weeks} onChange={(event) => setConfig({ ...config, seasonal_smoothing_weeks: Number(event.target.value) })} /></div>
          <div><div className="mb-1 text-xs font-medium">Category shrinkage</div><Input type="number" min={0} max={10} step={0.25} value={config.seasonal_shrinkage} onChange={(event) => setConfig({ ...config, seasonal_shrinkage: Number(event.target.value) })} /></div>
        </div>
        <details className="mt-4 rounded-lg bg-muted/40 p-3"><summary className="cursor-pointer text-sm font-medium">Forecast model legend</summary><div className="mt-3 grid gap-2 md:grid-cols-2">{Object.entries(effectiveModelLegend).map(([key, explanation]) => <div key={key} className="rounded-md border bg-background p-3"><div className="text-sm font-medium">{key === 'auto' ? 'Automatic champion' : key.replaceAll('_', ' ')}</div><div className="mt-1 text-xs text-muted-foreground">{String(explanation)}</div></div>)}</div></details>
      </div>

      {run && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">SKU-location forecasts</div><div className="text-2xl font-semibold">{run.recommendation_count}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Actionable buys</div><div className="text-2xl font-semibold">{actionable.length}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Blocking exceptions</div><div className="text-2xl font-semibold">{run.blocking_exception_count}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Model / assumptions</div><div className="mt-1 text-xs font-medium">{run.model_version}<br />{run.assumption_version}</div></div>
          </div>

          <div className="rounded-xl border p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div><div className="font-medium">Monthly plan</div><div className="text-xs text-muted-foreground">52 weekly periods rolled into buyer-facing financial views.</div></div>
              <ToggleGroup className="flex w-full flex-wrap justify-start gap-1 sm:w-auto" type="single" value={measure} onValueChange={(value) => value && setMeasure(value as Measure)}>
                <ToggleGroupItem className="whitespace-nowrap" value="units">Units</ToggleGroupItem><ToggleGroupItem className="whitespace-nowrap" value="cogs">COGS</ToggleGroupItem>
                <ToggleGroupItem className="whitespace-nowrap" value="revenue">Revenue</ToggleGroupItem><ToggleGroupItem className="whitespace-nowrap" value="spend">Purchase spend</ToggleGroupItem>
              </ToggleGroup>
            </div>
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
              {monthly.map(([month, value]) => <div key={month} className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{month}</div><div className="font-semibold">{formatMeasure(value, measure)}</div></div>)}
            </div>
          </div>

          <div className="rounded-xl border">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
              <div><div className="font-medium">Recommended purchases</div><div className="text-xs text-muted-foreground">Only buyer-selected rows become drafts.</div></div>
              <Button onClick={buildDrafts} disabled={busy || selected.size === 0}><PackagePlus className="mr-1 h-4 w-4" />Build PO drafts ({selected.size})</Button>
            </div>
            <div className="grid gap-2 border-b bg-muted/20 p-3 sm:grid-cols-2 lg:grid-cols-5">
              <Select value={shopFilter} onValueChange={setShopFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All shops</SelectItem>{Object.entries(SHOP_NAMES).map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent></Select>
              <Select value={brandFilter} onValueChange={setBrandFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All brands</SelectItem>{filterOptions.brands.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
              <Select value={vendorFilter} onValueChange={setVendorFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All vendors</SelectItem>{filterOptions.vendors.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
              <Select value={categoryFilter} onValueChange={setCategoryFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All top categories</SelectItem>{filterOptions.categories.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
              <Select value={needByFilter} onValueChange={setNeedByFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Any need-by</SelectItem><SelectItem value="4w">Within 4 weeks</SelectItem><SelectItem value="8w">Weeks 5–8</SelectItem><SelectItem value="13w">Weeks 9–13</SelectItem><SelectItem value="later">Later than 13 weeks</SelectItem><SelectItem value="monitor">No need date</SelectItem></SelectContent></Select>
            </div>
            <div className="overflow-x-auto"><Table className="min-w-[1080px]"><TableHeader><TableRow>
              <TableHead className="w-10"><Checkbox checked={eligible.length > 0 && selected.size === eligible.length} onCheckedChange={(checked) => setSelected(checked ? new Set(eligible.map((row) => row.recommendation_id)) : new Set())} /></TableHead>
              <TableHead>Product / SKU</TableHead><TableHead>Brand / category</TableHead><TableHead>Vendor / shop</TableHead><TableHead>Model</TableHead>
              <TableHead>Need by</TableHead><TableHead className="text-right">Qty</TableHead><TableHead className="text-right">Spend</TableHead>
            </TableRow></TableHeader><TableBody>
              {eligible.map((row: PurchaseRecommendation) => (
                <TableRow key={row.recommendation_id}>
                  <TableCell><Checkbox checked={selected.has(row.recommendation_id)} onCheckedChange={(checked) => setSelected((current) => { const next = new Set(current); checked ? next.add(row.recommendation_id) : next.delete(row.recommendation_id); return next })} /></TableCell>
                  <TableCell><ProductIdentity description={row.description} sku={row.sku} itemId={row.item_id} /></TableCell>
                  <TableCell className="text-sm">{row.brand || 'Unmapped brand'}<div className="text-xs text-muted-foreground">{row.category_top_level || row.category || 'Uncategorized'}</div></TableCell>
                  <TableCell className="text-sm">{row.vendor_name}<div className="text-xs text-muted-foreground">{SHOP_NAMES[row.location_id] || `Shop ${row.location_id}`}</div></TableCell>
                  <TableCell><Badge variant="outline">{row.champion_model}</Badge><div className="mt-1 text-xs text-muted-foreground">{row.confidence} confidence</div></TableCell>
                  <TableCell className="text-sm">{row.need_by_week || 'Monitor'}</TableCell>
                  <TableCell className="text-right font-medium">{row.recommended_quantity}</TableCell>
                  <TableCell className="text-right">{row.purchase_commitment_spend == null ? '—' : money.format(row.purchase_commitment_spend)}</TableCell>
                </TableRow>
              ))}
            </TableBody></Table></div>
          </div>

          {exceptions.length > 0 && <Alert variant="destructive"><AlertTriangle className="h-4 w-4" /><AlertTitle>{exceptions.length} blocking exception(s)</AlertTitle><AlertDescription>
            Missing landed cost or vendor mapping prevents draft creation. Examples: {exceptions.slice(0, 5).map((row) => row.sku || row.item_id).join(', ')}.
          </AlertDescription></Alert>}
        </>
      )}

      <div className="space-y-3">
        <div><h3 className="font-medium">PO drafts</h3><p className="text-xs text-muted-foreground">Transactional, versioned and editable until approval.</p></div>
        {draftsLoading ? <><Skeleton className="h-40" /><Skeleton className="h-40" /></> : drafts.length === 0 ? (
          <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">Run a plan and select recommendations to create the first draft.</div>
        ) : drafts.map((draft: PurchaseOrderDraft) => <DraftCard key={draft.draft_id} draft={draft} onChanged={refetch} />)}
      </div>
    </div>
  )
}
