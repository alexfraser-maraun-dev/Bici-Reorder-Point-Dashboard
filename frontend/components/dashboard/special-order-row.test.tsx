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
    work_state: 'vendor_followup',
    next_action: 'Confirm the vendor arrival date',
    action_owner: 'procurement',
    action_due_date: '2026-08-20',
    promise_date: '2026-08-25',
    expected_date: '2026-08-24',
    fastest_landing_date: '2026-08-24',
    days_since_creation: 19,
    days_lost: 2,
    sla_severity: 'stage_stalled',
    ack_active: false,
    ...overrides,
  } as SpecialOrder
}

afterEach(cleanup)

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
    expect(within(rail).getByText('Arrived').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(within(rail).getByText('Arrived').closest('li')).toHaveTextContent('Pending')
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
          shopify_order_name: '#1234',
        })}
        onReview={vi.fn()}
      />,
    )

    const rail = screen.getByRole('list', { name: 'Order milestones for #1234' })
    expect(within(rail).getByText('Shopify order')).toBeInTheDocument()
    expect(within(rail).getByText('Lightspeed SO').closest('li')).toHaveAttribute('aria-current', 'step')
  })
})
