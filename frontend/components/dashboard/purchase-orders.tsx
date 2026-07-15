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
  useLightspeedPoAccess,
  usePODraft,
  usePODrafts,
} from '@/lib/hooks'
import type {
  ForecastRun,
  LightspeedPreview,
  MonthlyPlanningRollup,
  PODraftLine,
  POReconciliation,
  PurchaseOrderDraft,
  PurchaseRecommendation,
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
              Shop {current.shop_id} · {lines.length} lines · {totalUnits} units · {money.format(totalSpend)}
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

      <Table>
        <TableHeader><TableRow>
          <TableHead>SKU / item</TableHead><TableHead className="w-28">Quantity</TableHead>
          <TableHead className="w-32">Landed cost</TableHead><TableHead>Need by</TableHead>
          <TableHead>Routing</TableHead>{editable && <TableHead className="w-12" />}
        </TableRow></TableHeader>
        <TableBody>
          {lines.map((line, index) => (
            <TableRow key={line.line_id || `${line.item_id}-${index}`}>
              <TableCell>
                <div className="font-mono text-sm">{line.sku || line.item_id}</div>
                <div className="text-xs text-muted-foreground">Item {line.item_id}</div>
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
              <TableCell><Badge variant="outline">{RECON_LABELS[line.reconciliation] || line.reconciliation}</Badge></TableCell>
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
      </Table>

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
  const { poAccess } = useLightspeedPoAccess()
  const [run, setRun] = useState<ForecastRun | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [measure, setMeasure] = useState<Measure>('units')
  const [busy, setBusy] = useState(false)

  const eligible = useMemo(
    () => run?.recommendations.filter((row) => !row.blocked && row.recommended_quantity > 0) || [],
    [run],
  )
  const exceptions = useMemo(() => run?.recommendations.filter((row) => row.blocked) || [], [run])
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
      const response = await createPlanningRun({ horizon_weeks: 52 })
      setRun(response.data)
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

      {run && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">SKU-location forecasts</div><div className="text-2xl font-semibold">{run.recommendation_count}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Actionable buys</div><div className="text-2xl font-semibold">{eligible.length}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Blocking exceptions</div><div className="text-2xl font-semibold">{run.blocking_exception_count}</div></div>
            <div className="rounded-xl border p-4"><div className="text-xs text-muted-foreground">Model / assumptions</div><div className="mt-1 text-xs font-medium">{run.model_version}<br />{run.assumption_version}</div></div>
          </div>

          <div className="rounded-xl border p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div><div className="font-medium">Monthly plan</div><div className="text-xs text-muted-foreground">52 weekly periods rolled into buyer-facing financial views.</div></div>
              <ToggleGroup type="single" value={measure} onValueChange={(value) => value && setMeasure(value as Measure)}>
                <ToggleGroupItem value="units">Units</ToggleGroupItem><ToggleGroupItem value="cogs">COGS</ToggleGroupItem>
                <ToggleGroupItem value="revenue">Revenue</ToggleGroupItem><ToggleGroupItem value="spend">Purchase spend</ToggleGroupItem>
              </ToggleGroup>
            </div>
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
              {monthly.map(([month, value]) => <div key={month} className="rounded-lg bg-muted/50 p-3"><div className="text-xs text-muted-foreground">{month}</div><div className="font-semibold">{formatMeasure(value, measure)}</div></div>)}
            </div>
          </div>

          <div className="rounded-xl border">
            <div className="flex items-center justify-between border-b p-4">
              <div><div className="font-medium">Recommended purchases</div><div className="text-xs text-muted-foreground">Only buyer-selected rows become drafts.</div></div>
              <Button onClick={buildDrafts} disabled={busy || selected.size === 0}><PackagePlus className="mr-1 h-4 w-4" />Build PO drafts ({selected.size})</Button>
            </div>
            <Table><TableHeader><TableRow>
              <TableHead className="w-10"><Checkbox checked={eligible.length > 0 && selected.size === eligible.length} onCheckedChange={(checked) => setSelected(checked ? new Set(eligible.map((row) => row.recommendation_id)) : new Set())} /></TableHead>
              <TableHead>SKU / product</TableHead><TableHead>Vendor / shop</TableHead><TableHead>Model</TableHead>
              <TableHead>Need by</TableHead><TableHead className="text-right">Qty</TableHead><TableHead className="text-right">Spend</TableHead>
            </TableRow></TableHeader><TableBody>
              {eligible.map((row: PurchaseRecommendation) => (
                <TableRow key={row.recommendation_id}>
                  <TableCell><Checkbox checked={selected.has(row.recommendation_id)} onCheckedChange={(checked) => setSelected((current) => { const next = new Set(current); checked ? next.add(row.recommendation_id) : next.delete(row.recommendation_id); return next })} /></TableCell>
                  <TableCell><div className="font-mono text-sm">{row.sku || row.item_id}</div><div className="max-w-72 truncate text-xs text-muted-foreground">{row.description || row.category}</div></TableCell>
                  <TableCell className="text-sm">{row.vendor_name}<div className="text-xs text-muted-foreground">Shop {row.location_id}</div></TableCell>
                  <TableCell><Badge variant="outline">{row.champion_model}</Badge><div className="mt-1 text-xs text-muted-foreground">{row.confidence} confidence</div></TableCell>
                  <TableCell className="text-sm">{row.need_by_week || 'Monitor'}</TableCell>
                  <TableCell className="text-right font-medium">{row.recommended_quantity}</TableCell>
                  <TableCell className="text-right">{row.purchase_commitment_spend == null ? '—' : money.format(row.purchase_commitment_spend)}</TableCell>
                </TableRow>
              ))}
            </TableBody></Table>
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
