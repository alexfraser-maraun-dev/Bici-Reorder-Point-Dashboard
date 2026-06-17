'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  usePODrafts,
  usePODraft,
  useLightspeedPoAccess,
  createPODraft,
  pushPODraft,
  deletePODraft,
} from '@/lib/hooks'
import type { PurchaseOrderDraft, PODraftLine, POReconciliation } from '@/lib/types'
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { ShoppingCart, Trash2, Send, PackagePlus, RefreshCw, AlertTriangle } from 'lucide-react'

interface PurchaseOrdersProps {
  // Raw replenishment response: { data: { [location]: rec[] }, ... }
  data: any
  isLoading: boolean
}

const RECON_LABELS: Record<POReconciliation, { label: string; variant: 'default' | 'secondary' | 'outline' }> = {
  new_po: { label: 'New PO', variant: 'default' },
  append_to_open_po: { label: 'Add to open PO', variant: 'secondary' },
  already_on_po: { label: 'Already on PO', variant: 'outline' },
}

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  draft: 'secondary',
  submitted: 'default',
  pushed: 'default',
  failed: 'destructive',
}

function flattenRecsNeedingOrder(data: any): any[] {
  if (!data?.data) return []
  const out: any[] = []
  for (const location of Object.keys(data.data)) {
    for (const rec of data.data[location] || []) {
      if ((rec.qty_to_order || 0) > 0 && !rec.locked) out.push(rec)
    }
  }
  return out
}

function ReconBadge({ value }: { value: POReconciliation }) {
  const cfg = RECON_LABELS[value] || RECON_LABELS.new_po
  return <Badge variant={cfg.variant}>{cfg.label}</Badge>
}

function DraftCard({
  draft,
  onChanged,
}: {
  draft: PurchaseOrderDraft
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  // List endpoint returns headers only; fetch this draft's lines on demand.
  const { data: detail, refetch: refetchDetail } = usePODraft(draft.draft_id)
  const lines: PODraftLine[] = detail?.lines || draft.lines || []
  const totalUnits = lines.reduce((sum, l) => sum + (l.quantity || 0), 0)
  const appending = !!draft.lightspeed_order_id

  const handlePush = async () => {
    setBusy(true)
    try {
      const res = await pushPODraft(draft.draft_id)
      if (res.status === 'pushed') {
        toast.success(`PO pushed for ${draft.vendor_name || draft.vendor_id}`)
      } else {
        toast.error(`Push completed with failures for ${draft.vendor_name || draft.vendor_id}`)
      }
      refetchDetail()
      onChanged()
    } catch (e: any) {
      toast.error(e.message || 'Failed to push PO')
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    setBusy(true)
    try {
      await deletePODraft(draft.draft_id)
      toast.success('Draft deleted')
      onChanged()
    } catch (e: any) {
      toast.error(e.message || 'Failed to delete draft')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="flex items-center justify-between gap-4 border-b p-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-muted p-2">
            <ShoppingCart className="h-4 w-4" />
          </div>
          <div>
            <div className="font-medium">
              {draft.vendor_name || `Vendor ${draft.vendor_id}`}
            </div>
            <div className="text-sm text-muted-foreground">
              Shop {draft.shop_id} · {lines.length} line{lines.length === 1 ? '' : 's'} · {totalUnits} units
              {appending && ` · appends to open PO #${draft.lightspeed_order_id}`}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={STATUS_VARIANT[draft.status] || 'secondary'}>{draft.status}</Badge>
          {draft.status !== 'pushed' && (
            <>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button size="sm" disabled={busy} className="gap-1">
                    <Send className="h-3.5 w-3.5" />
                    Push
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Push this PO to Lightspeed?</AlertDialogTitle>
                    <AlertDialogDescription>
                      {appending
                        ? `This will add ${lines.length} line(s) totalling ${totalUnits} units to open PO #${draft.lightspeed_order_id} for ${draft.vendor_name || draft.vendor_id}.`
                        : `This will create a new purchase order with ${lines.length} line(s) totalling ${totalUnits} units for ${draft.vendor_name || draft.vendor_id}.`}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={handlePush}>Push to Lightspeed</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
              <Button size="icon" variant="ghost" disabled={busy} onClick={handleDelete}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>SKU</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead>Reconciliation</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lines.map((line: PODraftLine, i: number) => (
            <TableRow key={`${line.sku}-${i}`}>
              <TableCell className="font-mono text-sm">{line.sku}</TableCell>
              <TableCell className="text-right">{line.quantity}</TableCell>
              <TableCell><ReconBadge value={line.reconciliation} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function PurchaseOrders({ data, isLoading }: PurchaseOrdersProps) {
  const { data: drafts, isLoading: draftsLoading, refetch } = usePODrafts()
  const { poAccess } = useLightspeedPoAccess()
  const [generating, setGenerating] = useState(false)

  const candidateCount = useMemo(() => flattenRecsNeedingOrder(data).length, [data])

  const handleGenerate = async () => {
    const recs = flattenRecsNeedingOrder(data)
    if (recs.length === 0) {
      toast.info('No items currently need ordering.')
      return
    }
    setGenerating(true)
    try {
      const res = await createPODraft(recs)
      const count = res.drafts?.length || 0
      toast.success(`Generated ${count} draft${count === 1 ? '' : 's'} from ${recs.length} items`)
      refetch()
    } catch (e: any) {
      toast.error(e.message || 'Failed to generate drafts')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Purchase Orders</h2>
          <p className="text-sm text-muted-foreground">
            Drafts are reconciled against open Lightspeed POs — existing orders are topped up rather than duplicated.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()} className="gap-1">
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button onClick={handleGenerate} disabled={generating || isLoading} className="gap-1">
            <PackagePlus className="h-4 w-4" />
            {generating ? 'Generating…' : `Generate drafts (${candidateCount})`}
          </Button>
        </div>
      </div>

      {poAccess === false && (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <div className="font-medium text-destructive">Lightspeed purchase-order access unavailable</div>
            <div className="text-muted-foreground">
              The Lightspeed token isn’t authorized for purchase orders, so pushing will fail. Re-authorize
              with the <code className="font-mono">employee:purchase_orders</code> scope
              (run <code className="font-mono">backend/reauthorize_lightspeed.py</code>), then refresh.
            </div>
          </div>
        </div>
      )}

      {draftsLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : drafts.length === 0 ? (
        <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">
          No purchase order drafts yet. Generate drafts from items that need ordering.
        </div>
      ) : (
        <div className="space-y-4">
          {drafts.map((draft: PurchaseOrderDraft) => (
            <DraftCard key={draft.draft_id} draft={draft} onChanged={refetch} />
          ))}
        </div>
      )}
    </div>
  )
}
