import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SpecialOrder } from '@/lib/types'
import { orderMilestones, SpecialOrderRow } from './special-order-row'

function fixture(overrides: Partial<SpecialOrder> = {}): SpecialOrder {
  return {
    special_order_id: '42',
    kind: 'ls',
    description: 'Fixture helmet',
    customer_name: 'Ada Rider',
    customer_email: 'ada@example.test',
    system_sku: '2100000042',
    store: 'Victoria',
    order_id: '9001',
    po_ref_num: 'VENDOR-REF-7',
    source: 'workorder',
    workorder_id: 'WO-42',
    workorder_url: 'https://ls.example/workorders/42',
    shopify_order_name: '#1234',
    shopify_order_url: 'https://shopify.example/orders/1234',
    ls_customer_url: 'https://ls.example/customers/7',
    ls_item_url: 'https://ls.example/items/8',
    ls_order_url: 'https://ls.example/orders/9001',
    procurement_stage: 'ordered',
    procurement_stage_index: 2,
    created_date: '2026-08-01',
    po_created_date: '2026-08-04',
    ordered_date: '2026-08-05',
    po_received_date: null,
    po_ordered: true,
    po_complete: false,
    received_started: false,
    so_received: false,
    so_received_date: null,
    receiving_state: 'not_started',
    work_state: 'vendor_followup',
    next_action: 'Confirm the vendor arrival date',
    action_owner: 'procurement',
    action_due_date: '2026-08-20',
    promise_date: '2026-08-25',
    expected_date: '2026-08-24',
    fastest_landing_date: '2026-08-24',
    days_since_creation: 19,
    days_open: 24,
    intake_lag_days: 5,
    earliest_ready_date: '2026-08-25',
    earliest_ready_basis: 'po_eta_plus_buffer',
    days_lost: 2,
    priority_score: 6,
    priority_band: 'high',
    priority_reasons: ['On track to land 3 days after 2026-08-25'],
    sla_severity: 'stage_stalled',
    ack_active: false,
    ...overrides,
  } as SpecialOrder
}

afterEach(cleanup)

describe('SpecialOrderRow decluttering', () => {
  it('puts the store in the header rather than the metadata line', () => {
    render(<SpecialOrderRow order={fixture()} onReview={vi.fn()} />)

    // The store used to be the fourth item in a `·`-separated line already carrying customer,
    // System ID and PO — three linked items it could not compete with.
    const heading = screen.getByText('SO #42').parentElement!
    expect(within(heading).getByText('Victoria')).toBeInTheDocument()
  })

  it('drops days-lost and the action due date from the tile', () => {
    render(<SpecialOrderRow order={fixture()} onReview={vi.fn()} />)

    // Both stay on the record (days_lost is still sortable, action_due_date still orders the
    // queue) — they are simply not what a buyer reads off the card.
    expect(screen.queryByText(/d lost/)).not.toBeInTheDocument()
    expect(screen.queryByText(/due /)).not.toBeInTheDocument()
    expect(screen.getByText('Confirm the vendor arrival date')).toBeInTheDocument()
  })

  it('names each date instead of collapsing them onto one stage-dependent row', () => {
    render(<SpecialOrderRow order={fixture({ procurement_stage: 'open_pool', procurement_stage_index: 0 })} onReview={vi.fn()} />)

    expect(screen.getByText('PO expected')).toBeInTheDocument()
    expect(screen.getByText('Fastest possible')).toBeInTheDocument()
    // Previously this stage rendered "Promise" and "Customer promise" as two rows of the same
    // value — a visible duplicate on every order that had not been placed yet.
    expect(screen.queryByText('Promise')).not.toBeInTheDocument()
    expect(screen.getAllByText('Customer promise')).toHaveLength(1)
  })

  it('shows days open with its seriousness score, and flags a late-tagged Shopify order', () => {
    render(<SpecialOrderRow order={fixture()} onReview={vi.fn()} />)

    expect(screen.getByText('24')).toBeInTheDocument()
    expect(screen.getByText('days open')).toBeInTheDocument()
    expect(screen.getByLabelText(/^Seriousness 6 out of 10/)).toHaveTextContent('6')
    expect(screen.getByText('Shopify order 5d earlier')).toBeInTheDocument()
  })

  it('replaces the SLA verdict when the linked Shopify order is already fulfilled', () => {
    render(<SpecialOrderRow order={fixture({
      sla_severity: 'promise_missed',
      shopify_order_closed: 'fulfilled',
      work_state: 'shopify_fulfilled',
      next_action: 'Shopify order already fulfilled — check out or cancel the SO in Lightspeed',
    })} onReview={vi.fn()} />)

    // "Promise missed" on such a row is an artefact — the customer was served or refunded and
    // nobody closed the Lightspeed record. Showing both badges would read as two live problems.
    expect(screen.getByText('Shopify fulfilled')).toBeInTheDocument()
    expect(screen.queryByText('Promise missed')).not.toBeInTheDocument()
    expect(screen.getByText(/check out or cancel the SO in Lightspeed/)).toBeInTheDocument()
  })

  it('shows a stranded customer instead of the silent closed_out verdict', () => {
    render(<SpecialOrderRow order={fixture({
      procurement_stage: 'received',
      procurement_stage_index: 3,
      so_received: true,
      so_received_date: '2026-07-22',
      sla_severity: 'closed_out',
      closeout_state: 'customer_stranded',
      work_state: 'closeout',
      priority_score: 10,
      priority_band: 'critical',
      next_action: 'Customer still waiting 34 days after arrival \u2014 fulfil or trace the item',
    })} onReview={vi.fn()} />)

    // `closed_out` makes SeverityBadge render nothing, which would leave a 10-scoring row with
    // no visible reason. The delivery clock has stopped; the customer's wait has not.
    expect(screen.getByText(/^Waiting \d+d$/)).toBeInTheDocument()
    // Past the 7-day threshold this is a real failure and wears red.
    expect(screen.getByText(/^Waiting \d+d$/).className).toMatch(/bg-red-600/)
    expect(screen.getByText(/fulfil or trace the item/)).toBeInTheDocument()
    expect(screen.getByLabelText(/^Seriousness 10 out of 10/)).toBeInTheDocument()
  })

  it('keeps a fresh unfulfilled arrival amber rather than alarming red', () => {
    const today = new Date()
    const threeDaysAgo = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 3)
    render(<SpecialOrderRow order={fixture({
      procurement_stage: 'received',
      procurement_stage_index: 3,
      so_received: true,
      so_received_date: threeDaysAgo.toISOString().slice(0, 10),
      sla_severity: 'closed_out',
      closeout_state: 'customer_stranded',
      work_state: 'closeout',
      priority_score: 4,
      priority_band: 'medium',
      next_action: 'Arrived \u2014 hand over and fulfil in Shopify',
    })} onReview={vi.fn()} />)

    // Dressing a normal three-day handover in the same red as a month-long strand is how a
    // warning colour stops meaning anything.
    const badge = screen.getByText(/^Waiting \d+d$/)
    expect(badge.className).toMatch(/bg-amber-100/)
    expect(badge.className).not.toMatch(/bg-red-600/)
  })

  it('falls back to the legacy clock for cached rows written before days_open existed', () => {
    render(<SpecialOrderRow order={fixture({ days_open: null, intake_lag_days: null })} onReview={vi.fn()} />)

    expect(screen.getByText('19')).toBeInTheDocument()
    expect(screen.queryByText(/Shopify order/)).not.toBeInTheDocument()
  })
})

describe('SpecialOrderRow source links', () => {
  it('puts product, customer, PO, workorder, and Shopify destinations on the tile', () => {
    render(<SpecialOrderRow order={fixture()} onReview={vi.fn()} />)

    const links = [
      ['Open Lightspeed product for System ID 2100000042 in a new tab', 'https://ls.example/items/8'],
      ['Open Lightspeed customer Ada Rider in a new tab', 'https://ls.example/customers/7'],
      ['Open Lightspeed purchase order 9001 in a new tab', 'https://ls.example/orders/9001'],
      ['Open Lightspeed workorder WO-42 in a new tab', 'https://ls.example/workorders/42'],
      ['Open Shopify order #1234 in a new tab', 'https://shopify.example/orders/1234'],
    ]

    for (const [name, href] of links) {
      const link = screen.getByRole('link', { name })
      expect(link).toHaveAttribute('href', href)
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    }

    expect(screen.getByRole('link', { name: /purchase order 9001/ })).toHaveTextContent('PO #9001')
  })

  it('keeps identifiers readable when a deep link is unavailable', () => {
    render(
      <SpecialOrderRow
        order={fixture({ ls_item_url: null, ls_customer_url: null, ls_order_url: null })}
        onReview={vi.fn()}
      />,
    )

    expect(screen.getByText('System ID 2100000042')).not.toHaveAttribute('href')
    expect(screen.getByText('Ada Rider')).not.toHaveAttribute('href')
    expect(screen.getByText('PO #9001')).not.toHaveAttribute('href')
  })
})

describe('SpecialOrderRow progress rail', () => {
  it('shows completed milestones and marks the next milestone as current', () => {
    render(<SpecialOrderRow order={fixture()} onReview={vi.fn()} />)

    const rail = screen.getByRole('list', { name: 'Order milestones for SO #42' })
    expect(within(rail).getByText('SO created').closest('li')).not.toHaveAttribute('aria-current')
    expect(within(rail).getByText('Ordered').closest('li')).not.toHaveAttribute('aria-current')
    expect(within(rail).getByText('SO check-in').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(within(rail).getByText('SO check-in').closest('li')).toHaveTextContent('Pending')
  })

  it('uses authoritative stage completion without inventing a missing date', () => {
    const milestones = orderMilestones(fixture({ ordered_date: null, po_ordered: false }))
    expect(milestones.find((milestone) => milestone.key === 'ordered')).toEqual({
      key: 'ordered',
      label: 'Ordered',
      date: null,
      complete: true,
    })
  })

  it('shows the Lightspeed SO as the next step for Shopify-only intake', () => {
    render(
      <SpecialOrderRow
        order={fixture({
          kind: 'shopify',
          special_order_id: 'gid://shopify/Order/1234',
          source: 'shopify',
          order_id: null,
          po_created_date: null,
          ordered_date: null,
          po_ordered: false,
          receiving_state: 'not_started',
          shopify_order_name: '#1234',
        })}
        onReview={vi.fn()}
      />,
    )

    const rail = screen.getByRole('list', { name: 'Order milestones for #1234' })
    expect(within(rail).getByText('Shopify order')).toBeInTheDocument()
    expect(within(rail).getByText('Lightspeed SO').closest('li')).toHaveAttribute('aria-current', 'step')
  })

  it('does not mark the SO received when PO receiving has started', () => {
    render(
      <SpecialOrderRow
        order={fixture({
          received_started: true,
          po_received_date: '2026-08-18',
          receiving_state: 'po_receiving',
        })}
        onReview={vi.fn()}
      />,
    )

    const checkIn = screen.getByText('SO check-in').closest('li')
    expect(checkIn).toHaveAttribute('aria-current', 'step')
    expect(checkIn).toHaveTextContent('PO receiving Aug 18 · SO pending')
    expect(checkIn).toHaveTextContent('Likely split shipment / backorder')
  })

  it('keeps SO check-in pending when the PO is complete', () => {
    const received = orderMilestones(fixture({
      po_complete: true,
      received_started: true,
      po_received_date: '2026-08-18',
      receiving_state: 'po_complete_so_unreceived',
    })).find((milestone) => milestone.key === 'received')

    expect(received).toMatchObject({
      label: 'SO check-in',
      date: null,
      complete: false,
      detail: 'PO complete Aug 18 · SO pending',
      hint: 'Likely split shipment / backorder',
      attention: true,
    })
  })

  it('completes check-in only from the individual SO receipt signal and date', () => {
    const received = orderMilestones(fixture({
      procurement_stage: 'received',
      procurement_stage_index: 3,
      po_complete: true,
      received_started: true,
      po_received_date: '2026-08-18',
      so_received: true,
      so_received_date: '2026-08-20',
      receiving_state: 'so_received',
    })).find((milestone) => milestone.key === 'received')

    expect(received).toMatchObject({
      date: '2026-08-20',
      complete: true,
      attention: false,
    })
  })

  it('derives the PO exception safely for cached rows without the new fields', () => {
    const received = orderMilestones(fixture({
      receiving_state: undefined,
      so_received: undefined,
      po_complete: true,
      received_started: true,
    })).find((milestone) => milestone.key === 'received')

    expect(received).toMatchObject({
      complete: false,
      detail: 'PO complete · SO pending',
      attention: true,
    })
  })

  it('uses the received stage only as a fallback when explicit receipt fields are absent', () => {
    const legacyReceived = orderMilestones(fixture({
      receiving_state: undefined,
      so_received: undefined,
      procurement_stage: 'received',
      procurement_stage_index: 3,
      po_complete: false,
      received_started: false,
      po_received_date: null,
    })).find((milestone) => milestone.key === 'received')
    const explicitlyUnreceived = orderMilestones(fixture({
      receiving_state: undefined,
      so_received: false,
      procurement_stage: 'received',
      procurement_stage_index: 3,
      po_complete: false,
      received_started: false,
      po_received_date: null,
    })).find((milestone) => milestone.key === 'received')

    expect(legacyReceived?.complete).toBe(true)
    expect(explicitlyUnreceived?.complete).toBe(false)
  })
})
