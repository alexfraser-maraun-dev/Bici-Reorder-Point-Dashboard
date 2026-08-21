import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  lookupShopifyOrders,
  updateServicePromise,
  updateShopifyEta,
} from '@/lib/hooks'
import { EditableEta, EditableServicePromise } from './special-order-fields'
import { MatchPickerDialog } from './special-order-match'

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('@/lib/hooks', () => ({
  updateShopifyEta: vi.fn(),
  updateServicePromise: vi.fn(),
  lookupShopifyOrders: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(updateShopifyEta).mockResolvedValue({ status: 'success' })
  vi.mocked(updateServicePromise).mockResolvedValue({
    status: 'success',
    special_order_id: '42',
    service_promise_date: '2026-09-20',
    service_promise_source: 'service_manual',
    changed: true,
  })
  vi.mocked(lookupShopifyOrders).mockResolvedValue([])
})

afterEach(cleanup)

describe('promise date editors', () => {
  it('keeps a Shopify date as a draft until Save is pressed', async () => {
    render(<EditableEta orderId="gid://shopify/Order/1" value="2026-09-10" />)

    const input = screen.getByLabelText('Customer promise date in Shopify')
    fireEvent.change(input, { target: { value: '2026-09-18' } })
    fireEvent.blur(input)

    expect(updateShopifyEta).not.toHaveBeenCalled()
    expect(screen.getByText('Unsaved change')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(updateShopifyEta).toHaveBeenCalledWith({
        shopify_order_id: 'gid://shopify/Order/1',
        eta: '2026-09-18',
      })
    })
  })

  it('reviews a Shopify promise clear before writing', async () => {
    render(<EditableEta orderId="gid://shopify/Order/1" value="2026-09-10" />)

    fireEvent.click(screen.getByRole('button', { name: /Clear Customer promise date in Shopify/ }))
    expect(screen.getByRole('alertdialog', { name: 'Clear the customer promise?' })).toBeInTheDocument()
    expect(updateShopifyEta).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Clear promise' }))

    await waitFor(() => {
      expect(updateShopifyEta).toHaveBeenCalledWith({
        shopify_order_id: 'gid://shopify/Order/1',
        eta: null,
      })
    })
  })

  it('saves a distinct service parts promise instead of a workorder ETA-out', async () => {
    render(<EditableServicePromise specialOrderId="42" value={null} />)

    fireEvent.change(screen.getByLabelText('Service parts promise date'), {
      target: { value: '2026-09-20' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(updateServicePromise).toHaveBeenCalledWith('42', '2026-09-20')
    })
  })
})

describe('manual matching review', () => {
  it('requires confirmation before linking a local candidate', async () => {
    const onPick = vi.fn().mockResolvedValue(undefined)

    render(
      <MatchPickerDialog
        open
        onOpenChange={vi.fn()}
        title="Link special order"
        description="Choose the matching order."
        items={[{
          key: 'shopify-1001',
          title: 'Shopify #1001',
          subtitle: 'rider@example.com',
          meta: 'SKU-123',
          candidate: true,
        }]}
        onPick={onPick}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Shopify #1001/ }))
    expect(onPick).not.toHaveBeenCalled()
    expect(screen.getByText(/Confirm that this is the same customer and item/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm link' }))
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('shopify-1001'))
  })
})
