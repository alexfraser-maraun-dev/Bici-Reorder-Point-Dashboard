'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { ackSpecialOrder, unackSpecialOrder } from '@/lib/hooks'
import type { SpecialOrder, SoReasonCode } from '@/lib/types'
import { BellOff, BellRing, AlertOctagon } from 'lucide-react'

// Wording that says what the buyer is actually waiting on, so the reason is worth reporting on
// later. Mirrors REASON_CODES in so_sla_service.py.
const REASONS: { value: SoReasonCode; label: string }[] = [
  { value: 'vendor_backorder', label: 'Vendor backorder' },
  { value: 'awaiting_vendor_reply', label: 'Awaiting vendor reply' },
  { value: 'customer_contacted', label: 'Customer contacted, agreed to wait' },
  { value: 'item_discontinued', label: 'Item discontinued' },
  { value: 'waiting_on_cs', label: 'Waiting on customer service' },
  { value: 'substitute_offered', label: 'Substitute offered' },
  { value: 'other', label: 'Other' },
]

const isoInDays = (days: number) => {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

/** Park / un-park a special order. Only rendered for rows that actually need action or are
 *  already parked — an Ack button on a healthy row invites dismissing things that were never
 *  a problem. */
export function SoAckMenu({ order, onDone }: { order: SpecialOrder; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState<SoReasonCode | ''>('')
  const [checkback, setCheckback] = useState(isoInDays(7))
  const [note, setNote] = useState('')

  if (!order.actionable && !order.ack_active) return null

  const submit = async () => {
    if (!reason) return
    setBusy(true)
    try {
      await ackSpecialOrder(order.special_order_id, {
        reason_code: reason,
        note: note.trim() || undefined,
        checkback_date: checkback,
      })
      toast.success(`SO #${order.special_order_id} parked until ${checkback}`)
      setOpen(false)
      setReason('')
      setNote('')
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not park this special order')
    } finally {
      setBusy(false)
    }
  }

  const unpark = async () => {
    setBusy(true)
    try {
      await unackSpecialOrder(order.special_order_id)
      toast.success(`SO #${order.special_order_id} returned to the queue`)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not un-park this special order')
    } finally {
      setBusy(false)
    }
  }

  if (order.ack_active && order.ack) {
    const label = REASONS.find((r) => r.value === order.ack!.reason_code)?.label ?? order.ack.reason_code
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs text-muted-foreground"
                  disabled={busy} onClick={unpark}>
            <BellOff className="h-3.5 w-3.5" />
            Parked
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <p className="font-medium">{label}</p>
          <p className="text-xs">Check back {order.ack.checkback_date}
            {order.ack.acked_by ? ` · ${order.ack.acked_by}` : ''}</p>
          {order.ack.note && <p className="mt-1 text-xs italic">“{order.ack.note}”</p>}
          {/* The re-arm rule is the part people get wrong, so say it plainly. */}
          <p className="mt-1 text-xs opacity-80">
            Re-opens automatically if the stage, customer promise or PO ETA changes. Click to
            return it now.
          </p>
        </TooltipContent>
      </Tooltip>
    )
  }

  return (
    <>
      <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => setOpen(true)}>
        <BellRing className="h-3.5 w-3.5" />
        Park
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Park SO #{order.special_order_id}</DialogTitle>
            <DialogDescription>
              {order.description ?? 'Special order'} — {order.store ?? 'unknown store'}
            </DialogDescription>
          </DialogHeader>

          <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            {order.sla_reason}
          </p>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Why is this waiting?</Label>
              <Select value={reason} onValueChange={(v) => setReason(v as SoReasonCode)}>
                <SelectTrigger><SelectValue placeholder="Choose a reason" /></SelectTrigger>
                <SelectContent>
                  {REASONS.map((r) => (
                    <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="checkback">Check back on</Label>
              <div className="flex items-center gap-2">
                <Input id="checkback" type="date" value={checkback}
                       onChange={(e) => setCheckback(e.target.value)} className="w-[170px]" />
                {[3, 7, 14].map((d) => (
                  <Button key={d} type="button" variant="outline" size="sm"
                          className="h-8 px-2 text-xs" onClick={() => setCheckback(isoInDays(d))}>
                    {d}d
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Required. When this date passes without progress the order escalates rather than
                staying quietly parked.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="note">Note (optional)</Label>
              <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)}
                        placeholder="e.g. HLC confirming stock Friday" rows={2} />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
            <Button onClick={submit} disabled={busy || !reason || !checkback}>
              {busy ? 'Parking…' : 'Park'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Shown when a check-back date has passed without the situation changing — the "parked rather
 *  than worked" pattern that produced the long tail. */
export function EscalationBadge({ level }: { level: number }) {
  if (!level) return null
  return (
    <Badge variant="outline"
           className={level >= 2
             ? 'gap-1 border-red-700 bg-red-600 text-[10px] font-medium text-white'
             : 'gap-1 border-orange-200 bg-orange-100 text-[10px] font-medium text-orange-700'}>
      <AlertOctagon className="h-3 w-3" />
      {level >= 2 ? 'Escalated ×2' : 'Escalated'}
    </Badge>
  )
}
