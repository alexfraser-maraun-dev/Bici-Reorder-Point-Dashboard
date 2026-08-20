'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, BookOpen } from 'lucide-react'
import { SeverityBadge, SourceBadge, StageBadge } from './special-order-badges'
import type { SlaSeverity, SpecialOrderSource } from '@/lib/types'
import type { ProcurementStage } from '@/lib/types'

/** Explains the derived elements on this page.
 *
 *  Everything visual is rendered with the SAME components the rows use, so the legend cannot
 *  drift from what it describes — the recurring failure on this page has been a second copy of
 *  a definition quietly going stale.
 *
 *  Deliberately covers only what is NOT self-evident: the numbers this tool computes and the
 *  rules it applies. Field labels like "Customer" or "SKU" explain themselves.
 */

function Row({ visual, children }: { visual: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-1">
      <div className="w-[150px] shrink-0 pt-0.5">{visual}</div>
      <p className="min-w-0 flex-1 text-xs text-muted-foreground">{children}</p>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
        {title}
      </p>
      {children}
    </div>
  )
}

const SEVERITIES: { key: SlaSeverity; text: string }[] = [
  { key: 'promise_missed', text: 'The date quoted to the customer has passed and the item is still not here.' },
  { key: 'impossible', text: 'It cannot arrive by the quoted date even if ordered today. Re-quote the customer rather than chase it.' },
  { key: 'order_today', text: 'Today is the last day it can be ordered and still make the quoted date.' },
  { key: 'stage_stalled', text: 'No promise pressure, but this step has overrun its time limit for this store. Limits differ by store because the stores genuinely differ.' },
  { key: 'at_risk', text: 'Still achievable, but the margin is down to a few days.' },
  { key: 'no_promise', text: 'Nobody recorded a date for this customer, so there is nothing to schedule against. Owned by whoever raised it — the service bench for workorders, CS for Shopify orders.' },
]

const STAGES: { key: ProcurementStage; text: string }[] = [
  { key: 'open_pool', text: 'Exists in Lightspeed but is not on any purchase order yet.' },
  { key: 'unordered_po', text: 'Attached to a purchase order that has never been sent to the vendor.' },
  { key: 'ordered', text: 'The purchase order has been placed and the item is on its way.' },
  { key: 'received', text: 'The item has arrived. The SLA clock stops here — anything still open is paperwork, not a late delivery.' },
]

const SOURCES: { key: SpecialOrderSource; text: string }[] = [
  { key: 'shopify', text: 'Raised from a Shopify order tagged SO. These are the only ones that carry a customer-quoted date.' },
  { key: 'workorder', text: 'Raised from a service workorder. The workorder’s eta-out is the bike’s booking date, not a promise about the part, so it is not treated as one.' },
  { key: 'neither', text: 'Raised straight into Lightspeed at the counter or by phone. A real order type, not a data fault — of 57 such orders, 56 verifiably have no Shopify order behind them.' },
]

export function SoLegend() {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-muted/50"
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <BookOpen className="h-4 w-4 text-muted-foreground" />
        Legend &amp; logic
        <span className="text-xs font-normal text-muted-foreground">
          — what the badges, dates and numbers mean
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t px-3 py-3">
          <Section title="Where it is (tiles)">
            {STAGES.map((s) => (
              <Row key={s.key} visual={<StageBadge stage={s.key} />}>{s.text}</Row>
            ))}
            <p className="mt-1 text-xs text-muted-foreground">
              Each tile splits only into <span className="font-medium">Needs action</span> and{' '}
              <span className="font-medium">On track</span>. Position is a fact about the order;
              what to do about it is what the tabs are for.
            </p>
          </Section>

          <Section title="Where it came from">
            {SOURCES.map((s) => (
              <Row key={String(s.key)} visual={<SourceBadge source={s.key} />}>{s.text}</Row>
            ))}
          </Section>

          <Section title="What needs attention">
            {SEVERITIES.map((s) => (
              <Row key={s.key} visual={<SeverityBadge severity={s.key} />}>{s.text}</Row>
            ))}
            <p className="mt-1 text-xs text-muted-foreground">
              Healthy and already-received orders carry no badge. A muted badge means the order is
              parked — the problem is known and someone has a date to come back to it.
            </p>
          </Section>

          <Section title="The dates and numbers">
            <Row visual={<span className="text-xs font-medium">Soonest it can land</span>}>
              The earliest this product can realistically be here, across every route: already in
              stock at this store, transferable from the sister store, already inbound on a
              purchase order with unclaimed units, or ordered now. Ordering now means{' '}
              <span className="font-medium">today + the vendor’s median lead time + a receiving
              buffer</span> — there is no “next order window” term, because ordering here is
              demand-driven rather than scheduled.
            </Row>
            <Row visual={<span className="text-xs font-medium text-red-600">Days lost</span>}>
              The gap between when the item <em>could</em> have landed had it been ordered the day
              the special order appeared, and the soonest it can land now. This is delay we
              caused. It needs no customer quote, which is why it works for the majority of
              special orders that have none. It stops accruing once the item is received.
            </Row>
            <Row visual={<span className="text-xs font-medium">Order by</span>}>
              The last date the item can be ordered and still meet a quoted date. Only shown when
              a customer date exists.
            </Row>
            <Row visual={<span className="text-xs font-medium">Days open</span>}>
              Raw age since the special order was created. Useful context, but not a priority
              signal on its own — a 40-day-old order for a 2-day-lead-time part and one for a
              20-day part are not the same problem. Prefer <em>Days lost</em>.
            </Row>
          </Section>

          <Section title="Parking an order">
            <p className="text-xs text-muted-foreground">
              Parking requires a reason and a check-back date — an open-ended dismissal cannot be
              reported on, and is how orders get parked rather than worked. A parked order
              re-opens by itself if its <span className="font-medium">stage</span>,{' '}
              <span className="font-medium">customer date</span> or{' '}
              <span className="font-medium">PO expected date</span> changes, so a snooze can only
              ever hide the problem it was taken for. Miss the check-back date and it escalates.
            </p>
          </Section>

          <Section title="Two things the tool cannot do">
            <p className="text-xs text-muted-foreground">
              It cannot attach a special order to a purchase order: Lightspeed’s API rejects every
              write to that record, so allocation happens in Lightspeed and the next sync confirms
              it landed. And it never writes a customer date to Shopify — a promise has to be made
              by a person.
            </p>
          </Section>
        </div>
      )}
    </div>
  )
}
