'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ackSpecialOrder, unackSpecialOrder } from '@/lib/hooks'
import type { SpecialOrder } from '@/lib/types'
import { specialOrderReceivingState } from './special-order-row'
import { Check, CircleCheck, Loader2, PlayCircle, RotateCcw, UserCheck } from 'lucide-react'

/** Rows these buttons belong on: anything that can appear in Action required, plus anything
 *  already cleared by them (so it can be undone).
 *
 *  A healthy, on-track row is deliberately excluded. Offering "Done" on something that was
 *  never a problem invites clearing work that nobody had to do, which turns the count into
 *  noise — the same reason the Park control has always hidden itself on healthy rows.
 *  `actionable` misses split-shipment/backorder rows, which stay on_track while still needing
 *  a human, so the receiving exception is checked explicitly. */
export function hasClearableWork(order: SpecialOrder): boolean {
  if (order.work_status === 'in_progress' || order.work_status === 'done') return true
  if (order.actionable) return true
  const receiving = specialOrderReceivingState(order)
  return receiving === 'po_receiving' || receiving === 'po_complete_so_unreceived'
}

/** One-click clearing of a row from "Action required".
 *
 * Deliberately free of data entry. The reason-coded Park dialog still exists in the detail
 * drawer for the cases worth reporting on, but the everyday moves are just two buttons:
 *
 *   Start — I am working this now. Claims it so nobody doubles up, and quiets it for a few
 *           days so the queue reflects what is actually unowned.
 *   Done  — this task is finished. Clears the row for good; only a NEW kind of work on the
 *           same order brings it back, which is what stops a received order from silently
 *           never getting its customer call.
 *
 * Both are undoable in one click, which is what makes it safe to have no confirmation step.
 */

type Pending = 'start' | 'done' | 'undo' | null

export function SoWorkActions({
  order,
  onDone,
  className,
  size = 'default',
}: {
  order: SpecialOrder
  onDone: () => void | Promise<void>
  className?: string
  /** 'compact' is the in-row variant; 'default' is used in the detail drawer. */
  size?: 'default' | 'compact'
}) {
  const [pending, setPending] = useState<Pending>(null)
  const busy = pending !== null
  const compact = size === 'compact'
  const buttonSize = compact ? 'sm' : 'default'

  // Shopify-only rows have no Lightspeed special order to write an ack against.
  if (order.kind === 'shopify') return null
  if (!hasClearableWork(order)) return null

  const run = async (action: Pending, work: () => Promise<unknown>, success: string) => {
    setPending(action)
    try {
      await work()
      toast.success(success)
      await onDone()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'That did not save. Try again.')
    } finally {
      setPending(null)
    }
  }

  const start = () => run(
    'start',
    () => ackSpecialOrder(order.special_order_id, { work_status: 'in_progress' }),
    `SO #${order.special_order_id} is yours — it will come back if it is still open in a few days.`,
  )

  const done = () => run(
    'done',
    () => ackSpecialOrder(order.special_order_id, { work_status: 'done' }),
    `SO #${order.special_order_id} cleared from Action required.`,
  )

  const undo = (message: string) => run('undo', () => unackSpecialOrder(order.special_order_id), message)

  const spinner = <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />

  if (order.work_status === 'done') {
    return (
      <div className={cn('flex items-center gap-1.5', className)}>
        <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">
          <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
          Done
        </span>
        <Button
          type="button"
          variant="ghost"
          size={buttonSize}
          className="gap-1.5 text-xs text-muted-foreground"
          disabled={busy}
          onClick={() => undo(`SO #${order.special_order_id} reopened.`)}
          aria-label={`Reopen SO ${order.special_order_id} and return it to Action required`}
        >
          {pending === 'undo' ? spinner : <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />}
          Reopen
        </Button>
      </div>
    )
  }

  if (order.work_status === 'in_progress') {
    const owner = order.ack?.acked_by
    return (
      <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
        <span
          className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700"
          title={owner ? `Started by ${owner}` : undefined}
        >
          <UserCheck className="h-3.5 w-3.5" aria-hidden="true" />
          In progress
        </span>
        <Button
          type="button"
          size={buttonSize}
          className="gap-1.5"
          disabled={busy}
          onClick={done}
          aria-label={`Mark SO ${order.special_order_id} done and clear it from Action required`}
        >
          {pending === 'done' ? spinner : <Check className="h-3.5 w-3.5" aria-hidden="true" />}
          Done
        </Button>
        <Button
          type="button"
          variant="ghost"
          size={buttonSize}
          className="gap-1.5 text-xs text-muted-foreground"
          disabled={busy}
          onClick={() => undo(`SO #${order.special_order_id} released back to the queue.`)}
          aria-label={`Release SO ${order.special_order_id} back to the queue`}
        >
          {pending === 'undo' ? spinner : <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />}
          Release
        </Button>
      </div>
    )
  }

  // Parked rows keep their own dedicated control (SoAckMenu) rather than showing Start next to
  // a "Parked until…" chip, which would read as two competing states for the same row.
  if (order.ack_active) return null

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <Button
        type="button"
        variant="outline"
        size={buttonSize}
        className="gap-1.5"
        disabled={busy}
        onClick={start}
        aria-label={`Start work on SO ${order.special_order_id}`}
      >
        {pending === 'start' ? spinner : <PlayCircle className="h-3.5 w-3.5" aria-hidden="true" />}
        Start
      </Button>
      <Button
        type="button"
        variant="outline"
        size={buttonSize}
        className="gap-1.5"
        disabled={busy}
        onClick={done}
        aria-label={`Mark SO ${order.special_order_id} done and clear it from Action required`}
      >
        {pending === 'done' ? spinner : <Check className="h-3.5 w-3.5" aria-hidden="true" />}
        Done
      </Button>
    </div>
  )
}
