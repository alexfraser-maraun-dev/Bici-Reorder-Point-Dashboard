'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, BookOpen } from 'lucide-react'
import { SeverityBadge, SourceBadge, StageBadge } from './special-order-badges'
import type { SlaSeverity, SpecialOrderSource, TriageStage } from '@/lib/types'

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
      <p className="min-w-0 flex-1 text-sm leading-5 text-muted-foreground">{children}</p>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
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

const STAGES: { key: TriageStage; text: string }[] = [
  { key: 'shopify', text: 'Shopify intake: the tagged order has not yet been matched to a Lightspeed special order.' },
  { key: 'open_pool', text: 'Awaiting PO: the special order exists in Lightspeed but is not attached to a purchase order.' },
  { key: 'unordered_po', text: 'Draft PO: attached to a purchase order that has not been sent to the vendor.' },
  { key: 'ordered', text: 'In transit: the purchase order has been placed and the item is awaiting arrival.' },
  { key: 'received', text: 'Arrived: the item has been received. Delivery SLA stops here; remaining work belongs in close-out.' },
]

const SOURCES: { key: SpecialOrderSource; text: string }[] = [
  { key: 'shopify', text: 'Raised from a Shopify order tagged SO. A customer promise can be recorded here; an explicit save writes that date to the Shopify order.' },
  { key: 'workorder', text: 'Raised from a service workorder. The workorder’s ETA-out describes the bike, not the part; service records a separate parts promise in this tool.' },
  { key: 'neither', text: 'Raised straight into Lightspeed at the counter or by phone. This is a valid intake route, not automatically a data fault.' },
]

export interface SoLegendCounts {
  stages?: Partial<Record<TriageStage, number>>
  sources?: Partial<Record<SpecialOrderSource, number>>
  severities?: Partial<Record<SlaSeverity, number>>
}

function VisualWithCount({ visual, count }: { visual: React.ReactNode; count?: number }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {visual}
      {count !== undefined && (
        <span className="text-xs tabular-nums text-muted-foreground" aria-label={`${count} orders`}>
          {count}
        </span>
      )}
    </span>
  )
}

export function SoLegend({ counts }: { counts?: SoLegendCounts }) {
  const [open, setOpen] = useState(false)
  const contentId = 'special-orders-legend-content'

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={contentId}
        className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium hover:bg-muted/50"
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <BookOpen className="h-4 w-4 text-muted-foreground" />
        Legend &amp; logic
        <span className="text-xs font-normal text-muted-foreground">
          — what the badges, dates and numbers mean
        </span>
      </button>

      {open && (
        <div id={contentId} className="space-y-4 border-t px-3 py-3">
          <Section title="Pipeline stages">
            {STAGES.map((s) => (
              <Row
                key={s.key}
                visual={<VisualWithCount visual={<StageBadge stage={s.key} />} count={counts?.stages?.[s.key]} />}
              >
                {s.text}
              </Row>
            ))}
            <p className="mt-1 text-sm leading-5 text-muted-foreground">
              Each tile splits only into <span className="font-medium">Needs action</span> and{' '}
              <span className="font-medium">On track</span>. Position is a fact about the order;
              what to do about it is what the tabs are for.
            </p>
          </Section>

          <Section title="Where it came from">
            {SOURCES.map((s) => (
              <Row
                key={String(s.key)}
                visual={<VisualWithCount visual={<SourceBadge source={s.key} />} count={counts?.sources?.[s.key]} />}
              >
                {s.text}
              </Row>
            ))}
          </Section>

          <Section title="What needs attention">
            {SEVERITIES.map((s) => (
              <Row
                key={s.key}
                visual={<VisualWithCount visual={<SeverityBadge severity={s.key} />} count={counts?.severities?.[s.key]} />}
              >
                {s.text}
              </Row>
            ))}
            <p className="mt-1 text-sm leading-5 text-muted-foreground">
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
            <Row visual={<span className="text-xs font-medium">PO expected</span>}>
              When the vendor says the <em>box</em> lands at the store — the purchase order&rsquo;s
              own arrival date, straight from Lightspeed.
            </Row>
            <Row visual={<span className="text-xs font-medium">Fastest possible</span>}>
              When the <em>customer</em> could actually collect it: the arrival date plus a{' '}
              receiving buffer. Before a PO exists it is the soonest across every route — in stock,
              transferable, already inbound, or ordered now at lead time + buffer.
            </Row>
            <Row visual={<span className="text-xs font-medium">Days open</span>}>
              How long the customer has been waiting, counted from the earlier of the Shopify order
              date and the Lightspeed special order date. Those differ when an order is{' '}
              <code className="text-[11px]">SO</code>-tagged days after it went live; the tile says
              so when the gap is real. Service orders count from the Lightspeed special order —
              a bike can sit on the rack for a week before anyone finds it needs a part.
            </Row>
            <Row visual={<span className="text-xs font-medium">Seriousness</span>}>
              One 1–10 number to sort the whole board on, driven by the clocks rather than by the
              severity label — <em>Promise missed</em> covers a one-day slip and a forty-day one.{' '}
              <span className="font-medium">7–10</span> means a real customer promise is already
              broken, scaled by how late.{' '}
              <span className="font-medium">1–6</span> is how much room is left before it lands
              late, measured against the quoted date or, where nobody quoted one, the date it would
              have landed had it been ordered when it appeared. Received orders run{' '}
              <span className="font-medium">1–4</span> on close-out age. No amount of missing ETAs
              or missed check-backs can push a row into the 7–10 band — only a broken promise does.
              The score is intrinsic: parking an order dims the badge but never lowers the number.
            </Row>
          </Section>

          <Section title="Parking an order">
            <p className="text-sm leading-5 text-muted-foreground">
              Parking requires a reason and a check-back date — an open-ended dismissal cannot be
              reported on, and is how orders get parked rather than worked. A parked order
              re-opens by itself if its <span className="font-medium">stage</span>,{' '}
              <span className="font-medium">customer date</span> or{' '}
              <span className="font-medium">PO expected date</span> changes, so a snooze can only
              ever hide the problem it was taken for. Miss the check-back date and it escalates.
            </p>
          </Section>

          <Section title="Write boundaries">
            <p className="text-sm leading-5 text-muted-foreground">
              The tool recommends a sourcing route but cannot attach a special order to a purchase
              order: Lightspeed’s API does not support that relationship, so allocation is completed
              in Lightspeed and confirmed after a refresh. A customer promise is always chosen by a
              person; when that person explicitly saves the Shopify ETA here, the tool writes the
              date to the Shopify order. Service parts promises are stored and audited in this tool
              without changing the workorder ETA-out.
            </p>
          </Section>
        </div>
      )}
    </div>
  )
}
