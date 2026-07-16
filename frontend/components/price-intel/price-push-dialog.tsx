'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { executePricePush, previewPricePush } from '@/lib/price-intel/hooks'
import type { PricePushPreview, TrackedProduct } from '@/lib/price-intel/types'
import { FloorOverrideDialog } from './floor-override-dialog'
import { AlertTriangle, ShieldAlert } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const FLOOR_LABEL: Record<string, string> = {
  map_price: 'MAP price',
  min_price_override: 'manual price floor',
  cost_margin_floor: 'cost + margin floor',
}

export function PricePushDialog({ product, open, onOpenChange, onPushed, initialPrice }: {
  product: TrackedProduct | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onPushed: () => void
  // Pre-fills the price input (e.g. "match this competitor"); falls back to
  // market min, then current retail.
  initialPrice?: number | null
}) {
  const [priceInput, setPriceInput] = useState('')
  const [preview, setPreview] = useState<PricePushPreview | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open && product) {
      const suggested = initialPrice ?? product.market_min_in_stock ?? product.current_retail
      setPriceInput(suggested != null ? Number(suggested).toFixed(2) : '')
      setPreview(null)
      setConfirmOpen(false)
    }
  }, [open, product, initialPrice])

  // Server-side preview: the guard verdict shown here is recomputed on push, so
  // this dialog is informative, not authoritative.
  useEffect(() => {
    const price = parseFloat(priceInput)
    if (!open || !product || !Number.isFinite(price) || price <= 0) {
      setPreview(null)
      return
    }
    const handle = setTimeout(async () => {
      try {
        setPreview(await previewPricePush(product.item_id, price))
      } catch {
        setPreview(null)
      }
    }, 350)
    return () => clearTimeout(handle)
  }, [open, product, priceInput])

  if (!product) return null

  const price = parseFloat(priceInput)
  const belowFloor = preview?.guard === 'below_floor'
  const rejected = preview?.guard === 'rejected'
  const canPush = !!preview && Number.isFinite(price) && !rejected

  // A below-floor push never executes directly: the button routes through the
  // authorization dialog, and only its "Authorize" click calls back here with
  // authorized=true (sent as override_floor, which the audit log records).
  const push = async (authorized = false) => {
    if (!canPush || !preview) return
    if (belowFloor && !authorized) {
      setConfirmOpen(true)
      return
    }
    setBusy(true)
    try {
      await executePricePush(product.item_id, price, belowFloor && authorized)
      toast.success(`Pushed ${fmt(price)} to Lightspeed for ${product.title}`)
      onOpenChange(false)
      onPushed()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Price push failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Push price to Lightspeed</DialogTitle>
          <DialogDescription className="truncate">{product.title}</DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <span className="text-muted-foreground">Current price</span>
          <span className="text-right tabular-nums">{fmt(product.current_retail)}</span>
          <span className="text-muted-foreground">Market min (in stock)</span>
          <span className="text-right tabular-nums">{fmt(product.market_min_in_stock)}</span>
          <span className="text-muted-foreground">Market median</span>
          <span className="text-right tabular-nums">{fmt(product.market_median)}</span>
          <span className="text-muted-foreground">Cost</span>
          <span className="text-right tabular-nums">{fmt(product.current_cost)}</span>
          {preview?.floor_price != null && (
            <>
              <span className="text-muted-foreground">
                Floor ({FLOOR_LABEL[preview.floor_source ?? ''] ?? 'floor'})
              </span>
              <span className="text-right font-medium tabular-nums">{fmt(preview.floor_price)}</span>
            </>
          )}
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">New price (CAD)</label>
          <Input value={priceInput} onChange={(e) => setPriceInput(e.target.value)}
                 inputMode="decimal" placeholder="0.00" />
        </div>

        {rejected && (
          <div className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{preview?.guard_reasons.join('; ')} — this push is blocked.</span>
          </div>
        )}

        {belowFloor && (
          <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {fmt(price)} is below the {FLOOR_LABEL[preview!.floor_source ?? ''] ?? 'floor'} of{' '}
              <strong>{fmt(preview!.floor_price)}</strong>
              {preview!.floor_source === 'map_price' && ' — advertising below MAP can breach the brand agreement'}.
              Pushing will ask you to authorize it.
            </span>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => push()} disabled={!canPush || busy}
                  className={cn(belowFloor && 'bg-amber-600 text-white hover:bg-amber-700')}>
            {busy ? 'Pushing…' : `Push ${Number.isFinite(price) ? fmt(price) : ''} to Lightspeed`}
          </Button>
        </DialogFooter>

        {preview && Number.isFinite(price) && (
          <FloorOverrideDialog
            open={confirmOpen}
            onOpenChange={setConfirmOpen}
            price={price}
            floorPrice={preview.floor_price}
            floorSource={preview.floor_source}
            subject={product.title ?? product.item_id}
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
