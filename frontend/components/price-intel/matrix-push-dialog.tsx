'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { executeMatrixPush, previewMatrixPush } from '@/lib/price-intel/hooks'
import type { MatrixPushPreview, TrackedProduct } from '@/lib/price-intel/types'
import { FloorOverrideDialog } from './floor-override-dialog'
import { AlertTriangle, ShieldAlert } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const FLOOR_LABEL: Record<string, string> = {
  map_price: 'MAP price',
  min_price_override: 'manual price floor',
  cost_margin_floor: 'cost + margin floor',
}

// Whole-matrix price push. The price is fixed (it comes from the competitor row
// the user clicked), the variant list comes live from Lightspeed via the preview
// endpoint, and the push echoes that exact list back so the server can abort if
// the matrix changed in between. The user must explicitly confirm they reviewed
// every variant before the button enables, and a below-floor (e.g. sub-MAP)
// price additionally routes through the authorize dialog before executing.
export function MatrixPushDialog({ product, price, open, onOpenChange, onPushed }: {
  product: TrackedProduct | null
  price: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onPushed: () => void
}) {
  const [preview, setPreview] = useState<MatrixPushPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open || !product || price == null) return
    setPreview(null)
    setError(null)
    setReviewed(false)
    setConfirmOpen(false)
    setLoading(true)
    previewMatrixPush(product.item_id, price)
      .then(setPreview)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load the matrix preview'))
      .finally(() => setLoading(false))
  }, [open, product, price])

  if (!product || price == null) return null

  const rejected = preview?.guard === 'rejected'
  const belowFloor = preview?.guard === 'below_floor'
  const canPush = !!preview && !rejected && reviewed

  // The strictest floor across variants, for the authorize dialog's wording.
  const worstFloor = preview?.variants.reduce<{ price: number; source: string | null } | null>(
    (acc, v) => {
      if (v.guard !== 'below_floor' || v.floor_price == null) return acc
      return acc == null || v.floor_price > acc.price
        ? { price: v.floor_price, source: v.floor_source }
        : acc
    }, null) ?? null

  const push = async (authorized = false) => {
    if (!canPush || !preview) return
    if (belowFloor && !authorized) {
      setConfirmOpen(true)
      return
    }
    setBusy(true)
    try {
      await executeMatrixPush(
        product.item_id, price, belowFloor && authorized,
        preview.variants.map((v) => v.item_id)
      )
      toast.success(
        `Pushed ${fmt(price)} to all ${preview.variant_count} variants of ` +
        `${preview.matrix_description ?? product.title}`
      )
      onOpenChange(false)
      onPushed()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Matrix price push failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Match price across the whole matrix</DialogTitle>
          <DialogDescription className="truncate">
            {preview?.matrix_description ?? product.matrix_description ?? product.title}
            {' — '}sets the Default price of every variant below to{' '}
            <span className="font-semibold text-foreground">{fmt(price)}</span>
          </DialogDescription>
        </DialogHeader>

        {loading && <Skeleton className="h-48 rounded-md" />}

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {preview && (
          <>
            {preview.warnings.map((w) => (
              <div key={w} className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{w}</span>
              </div>
            ))}

            <div className="max-h-64 overflow-y-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Variant</TableHead>
                    <TableHead className="text-right">Current</TableHead>
                    <TableHead className="text-right">New</TableHead>
                    <TableHead>Check</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.variants.map((v) => (
                    <TableRow key={v.item_id}>
                      <TableCell className="max-w-64">
                        <span className="block truncate text-sm">
                          {v.attributes.length ? v.attributes.join(' / ') : (v.description ?? v.item_id)}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {v.system_sku ?? v.item_id}{!v.tracked && ' · not tracked'}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{fmt(v.current_price)}</TableCell>
                      <TableCell className={cn(
                        'text-right font-medium tabular-nums',
                        v.current_price != null && price < v.current_price && 'text-rose-700',
                        v.current_price != null && price > v.current_price && 'text-emerald-700',
                      )}>
                        {fmt(price)}
                      </TableCell>
                      <TableCell>
                        {v.guard === 'ok' ? (
                          <span className="text-xs text-muted-foreground">ok</span>
                        ) : (
                          <Badge variant="outline" title={v.guard_reasons.join('; ')} className={cn(
                            'px-1.5 py-0 text-[11px]',
                            v.guard === 'rejected'
                              ? 'border-rose-200 bg-rose-50 text-rose-700'
                              : 'border-amber-300 bg-amber-50 text-amber-800'
                          )}>
                            {v.guard === 'rejected' ? 'blocked' : `below ${FLOOR_LABEL[v.floor_source ?? ''] ?? 'floor'}`}
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {rejected && (
              <div className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {preview.guard_reasons.join('; ')} — the matrix push is blocked.
                  Fix the outlier variant with a single-item push first.
                </span>
              </div>
            )}

            {belowFloor && (
              <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {preview.guard_reasons.join('; ')}
                  {worstFloor?.source === 'map_price' &&
                    ' — advertising below MAP can breach the brand agreement'}.
                  Pushing will ask you to authorize it.
                </span>
              </div>
            )}

            {!rejected && (
              <label className="flex items-center gap-2 text-sm font-medium">
                <Checkbox checked={reviewed}
                          onCheckedChange={(v) => setReviewed(v === true)} />
                I&apos;ve reviewed all {preview.variant_count} variants above
              </label>
            )}
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => push()} disabled={!canPush || busy}
                  className={cn(belowFloor && 'bg-amber-600 text-white hover:bg-amber-700')}>
            {busy
              ? 'Pushing…'
              : preview
                ? `Push ${fmt(price)} to ${preview.variant_count} variants`
                : `Push ${fmt(price)}`}
          </Button>
        </DialogFooter>

        {preview && (
          <FloorOverrideDialog
            open={confirmOpen}
            onOpenChange={setConfirmOpen}
            price={price}
            floorPrice={worstFloor?.price ?? null}
            floorSource={worstFloor?.source ?? null}
            subject={preview.matrix_description ?? product.title ?? product.item_id}
            variantCount={preview.variant_count}
            onAuthorize={() => {
              setConfirmOpen(false)
              void push(true)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
