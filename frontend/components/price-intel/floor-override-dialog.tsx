'use client'

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { ShieldAlert } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

const FLOOR_LABEL: Record<string, string> = {
  map_price: 'MAP price',
  min_price_override: 'manual price floor',
  cost_margin_floor: 'cost + margin floor',
}

// The manual-authorization step for a below-floor (most importantly sub-MAP)
// price push. The parent dialog opens this instead of executing when the server
// preview says below_floor; only the explicit "Authorize" click runs the push
// (with override_floor=true, which the audit log records as "overridden").
export function FloorOverrideDialog({
  open, onOpenChange, price, floorPrice, floorSource, subject, variantCount, onAuthorize,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  price: number
  floorPrice: number | null
  floorSource: string | null
  // what's being repriced, e.g. the item title or matrix description
  subject: string
  // set for matrix pushes so the scope is unmissable
  variantCount?: number
  onAuthorize: () => void
}) {
  const isMap = floorSource === 'map_price'
  const label = FLOOR_LABEL[floorSource ?? ''] ?? 'price floor'
  const shortfall = floorPrice != null ? floorPrice - price : null

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-amber-600" />
            {isMap ? 'Authorize sub-MAP pricing?' : 'Authorize below-floor pricing?'}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm">
              <p>
                <span className="font-semibold text-foreground">{fmt(price)}</span> is{' '}
                {shortfall != null && shortfall > 0 && (
                  <><span className="font-semibold text-foreground">{fmt(shortfall)}</span> </>
                )}
                below the {label} of{' '}
                <span className="font-semibold text-foreground">{fmt(floorPrice)}</span> for{' '}
                <span className="font-medium text-foreground">{subject}</span>
                {variantCount != null && (
                  <> — it will apply to <span className="font-semibold text-foreground">
                    all {variantCount} variants</span> of the matrix</>
                )}.
              </p>
              {isMap && (
                <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-amber-900">
                  Advertising below MAP can breach the brand agreement with the
                  supplier. Only proceed if this has been cleared.
                </p>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className="bg-amber-600 text-white hover:bg-amber-700"
            onClick={onAuthorize}
          >
            Authorize &amp; push {fmt(price)}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
