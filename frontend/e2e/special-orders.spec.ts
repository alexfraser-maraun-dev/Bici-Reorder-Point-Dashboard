import { expect, test } from '@playwright/test'
import { encode } from 'next-auth/jwt'

const AUTH_SECRET = 'special-orders-playwright-secret'
const NOW = '2026-08-20T18:00:00Z'

type Stage = 'open_pool' | 'unordered_po' | 'ordered' | 'received'

const stageIndex: Record<Stage, number> = {
  open_pool: 0,
  unordered_po: 1,
  ordered: 2,
  received: 3,
}

function workForStage(stage: Stage) {
  if (stage === 'ordered') {
    return {
      work_state: 'vendor_followup',
      queue_states: ['in_transit', 'vendor_followup'],
      next_action: 'Chase the vendor for an updated arrival date',
      action_owner: 'procurement',
    }
  }
  if (stage === 'received') {
    return {
      work_state: 'closeout',
      queue_states: ['closeout'],
      next_action: 'Contact the customer and close the order',
      action_owner: 'retail',
    }
  }
  return {
    work_state: 'needs_ordering',
    queue_states: ['needs_ordering'],
    next_action: stage === 'open_pool'
      ? 'Confirm the supply route in Lightspeed'
      : 'Place the draft purchase order with the vendor',
    action_owner: 'procurement',
  }
}

function makeOrder(sequence: number, stage: Stage) {
  const work = workForStage(stage)
  const id = String(1000 + sequence)
  const poId = String(5000 + sequence)
  const hasShopifyOrder = sequence % 5 === 0
  return {
    special_order_id: id,
    status: stage === 'received' ? 'Received' : 'Open',
    unit_quantity: '1',
    shop_id: '1',
    store: sequence % 2 === 0 ? 'Victoria' : 'Adanac',
    timestamp: '2026-08-01T12:00:00Z',
    created_date: '2026-08-01',
    days_since_creation: 19 + sequence,
    contacted: false,
    completed: false,
    customer_id: `customer-${id}`,
    customer_name: `Customer ${id}`,
    customer_phone: '250-555-0100',
    customer_email: `customer-${id}@example.test`,
    item_id: `item-${id}`,
    system_sku: `SKU-${id}`,
    upc: `00000000${id}`,
    brand: 'Test Brand',
    available_vendors: [],
    description: `Fixture product ${id}`,
    order_id: stage === 'open_pool' ? null : poId,
    vendor_id: stage === 'open_pool' ? null : 'vendor-1',
    vendor_name: stage === 'open_pool' ? null : 'Fixture Vendor',
    order_type: stage === 'open_pool' ? null : 'Replenishment',
    expected_date: stage === 'ordered' ? '2026-08-18' : null,
    ordered_date: stage === 'ordered' || stage === 'received' ? '2026-08-05' : null,
    po_ordered: stage === 'ordered' || stage === 'received',
    po_complete: stage === 'received',
    received_started: stage === 'received',
    procurement_stage: stage,
    procurement_stage_index: stageIndex[stage],
    source: hasShopifyOrder ? 'shopify' : 'neither',
    days_in_stage: 4 + sequence,
    po_created_date: stage === 'open_pool' ? null : '2026-08-04',
    po_received_date: stage === 'received' ? '2026-08-19' : null,
    po_ref_num: stage === 'open_pool' ? null : `REF-${id}`,
    days_po_open: stage === 'open_pool' ? null : 15,
    sale_line_id: `sale-line-${id}`,
    order_line_id: stage === 'open_pool' ? null : `order-line-${id}`,
    vendor_lead_time_days: 7,
    link_provenance: null,
    link_broken: null,
    matched_via_closed_order: false,
    sla_severity: stage === 'received' ? 'closed_out' : 'stage_stalled',
    sla_severity_rank: stage === 'received' ? 7 : 3,
    sla_owner: stage === 'received' ? 'receiving' : 'procurement',
    sla_reason: work.next_action,
    promise_date: '2026-08-25',
    promise_source: null,
    lead_time_days: 7,
    lead_time_source: 'fastest_qualifying_vendor',
    receiving_buffer_days: 2,
    order_by_date: '2026-08-16',
    slack_days: -4,
    stage_sla_days: 5,
    days_over_stage_sla: sequence,
    missing_promise: false,
    promise_owner: null,
    fastest_landing_date: stage === 'received' ? '2026-08-19' : '2026-08-28',
    fastest_path_tier: stage === 'received' ? 'received' : 'new_po',
    could_have_landed: '2026-08-10',
    days_lost: sequence,
    ack: null,
    ack_active: false,
    escalation_level: 0,
    actionable: true,
    checkback_due: false,
    ...work,
    action_due_date: '2026-08-20',
    closeout_state: stage === 'received' ? 'customer_contact_required' : null,
    service_promise_date: null,
    service_promise_source: null,
    service_promise_recorded_at: null,
    service_promise_recorded_by: null,
    flag: 'none',
    days_overdue: null,
    is_overdue: false,
    shopify_match: hasShopifyOrder ? 'matched' : 'none',
    shopify_match_basis: hasShopifyOrder ? 'email_sku' : null,
    shopify_order_id: hasShopifyOrder ? `gid://shopify/Order/${id}` : null,
    shopify_order_name: hasShopifyOrder ? `#SHOP-${id}` : null,
    shopify_order_url: hasShopifyOrder ? `https://admin.shopify.com/store/test/orders/${id}` : null,
    shopify_expected_date: null,
    shopify_fulfillment_status: null,
    shopify_financial_status: null,
    shopify_candidates: [],
    workorder_id: null,
    workorder_status: null,
    workorder_note: null,
    workorder_internal_note: null,
    workorder_hook_in: null,
    workorder_eta_out: null,
    workorder_time_in: null,
    workorder_url: null,
    ls_item_url: `https://ls.example.test/items/${id}`,
    ls_customer_url: `https://ls.example.test/customers/${id}`,
    ls_order_url: stage === 'open_pool' ? null : `https://ls.example.test/orders/${poId}`,
    kind: 'ls',
    ambiguous_candidate: false,
  }
}

function makeOrders() {
  const orders: ReturnType<typeof makeOrder>[] = []
  let sequence = 1
  const add = (stage: Stage, count: number) => {
    for (let index = 0; index < count; index += 1) {
      orders.push(makeOrder(sequence, stage))
      sequence += 1
    }
  }
  add('open_pool', 8)
  add('unordered_po', 7)
  add('ordered', 10)
  add('received', 5)
  return orders
}

const shopifyOnly = [1, 2].map((sequence) => ({
  order_id: `gid://shopify/Order/${sequence}`,
  order_name: `#SO-SHOP-${sequence}`,
  customer_email: `shopify-${sequence}@example.test`,
  shopify_expected_date: '2026-08-25',
  created_at: '2026-08-18T12:00:00Z',
  fulfillment_status: 'unfulfilled',
  financial_status: 'paid',
  skus: [`SHOPIFY-SKU-${sequence}`],
  shopify_order_url: `https://admin.shopify.com/store/test/orders/${sequence}`,
  ambiguous_candidate: false,
}))

const worklist = {
  orders: makeOrders(),
  shopify_only: shopifyOnly,
  fetched_at: NOW,
  reason_codes: ['awaiting_vendor_reply', 'customer_contacted', 'other'],
  summary: {
    by_severity: { stage_stalled: 25, closed_out: 5 },
    by_owner: { procurement: 25, receiving: 5, cs: 0 },
    missing_promise_by_owner: { service: 0, cs: 0 },
    by_work_state: { intake: 2, needs_ordering: 15, vendor_followup: 10, closeout: 5 },
    by_queue_state: { intake: 2, needs_ordering: 15, in_transit: 10, vendor_followup: 10, closeout: 5 },
    by_action_owner: { procurement: 25, retail: 7 },
    actionable: 32,
    acked: 0,
    checkback_due: 0,
    escalated: 0,
    missing_promise: 0,
  },
  meta: {
    live_only_days: 365,
    total_before_window: 30,
    sources: {
      lightspeed: { status: 'ok', fetched_at: NOW },
      shopify: { status: 'ok', fetched_at: NOW },
      bigquery: { status: 'ok', fetched_at: NOW },
      workorders: { status: 'ok', fetched_at: NOW },
    },
  },
}

test('desktop Special Orders worklist is actionable, reconciled, paginated, and uses a detail drawer', async ({
  context,
  page,
}) => {
  const token = await encode({
    secret: AUTH_SECRET,
    token: {
      sub: 'playwright-user',
      name: 'Playwright User',
      email: 'playwright@example.test',
    },
    maxAge: 60 * 60,
  })
  await context.addCookies([{
    name: 'next-auth.session-token',
    value: token,
    url: 'http://127.0.0.1:3100',
    httpOnly: true,
    sameSite: 'Lax',
    expires: Math.floor(Date.now() / 1000) + 60 * 60,
  }])

  let recommendationCalls = 0
  let activityCalls = 0

  await page.route('**/api/auth/session', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      user: { name: 'Playwright User', email: 'playwright@example.test', image: null },
      expires: '2026-08-21T18:00:00.000Z',
    }),
  }))
  await page.route('**/backend/api/admin/access', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      data: {
        email: 'playwright@example.test',
        role: 'admin',
        is_admin: true,
        bootstrap_mode: false,
        features: { ordering: true, special_orders: true, price_intel: true, admin: true },
        default_ordering_tab: 'ordering.po_tracker',
      },
    }),
  }))
  await page.route('**/backend/api/health/*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok' }),
  }))
  await page.route('**/backend/api/special-orders/escalations**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(worklist),
  }))
  await page.route('**/backend/api/special-orders/*/activity', (route) => {
    activityCalls += 1
    const specialOrderId = new URL(route.request().url()).pathname.split('/').at(-2) ?? 'unknown'
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        special_order_id: specialOrderId,
        activity: [{
          timestamp: '2026-08-20T17:30:00Z',
          type: 'fixture',
          label: 'Activity fixture loaded',
          actor: 'playwright@example.test',
          details: { source: 'route-intercepted activity' },
        }],
      }),
    })
  })
  await page.route('**/backend/api/special-orders/*/po-recommendation', (route) => {
    recommendationCalls += 1
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        special_order_id: 'fixture',
        tier: 'new_po',
        recommendation: { tier: 'new_po', vendor_id: 'vendor-1', vendor_name: 'Fixture Vendor' },
        alternatives: [],
        reason: 'Fixture recommendation',
        promise_date: '2026-08-25',
        draft_pos_available: true,
        fastest_landing_date: '2026-08-28',
        could_have_landed: '2026-08-10',
        days_lost: 18,
      }),
    })
  })

  await page.goto('/special-orders')

  await expect(page.getByRole('heading', { name: 'Special Orders' })).toBeVisible()
  const actionTab = page.getByRole('tab', { name: /^Action required/ })
  await expect(actionTab).toHaveAttribute('aria-selected', 'true')
  await expect(actionTab).toContainText('32')

  const pipeline = page.getByRole('region', { name: 'Live pipeline' })
  await expect(pipeline).toContainText('32 total')
  const expectedPipeline = new Map([
    ['Shopify intake', 2],
    ['Awaiting PO', 8],
    ['Draft PO', 7],
    ['In transit', 10],
    ['Arrived', 5],
  ])
  for (const [label, count] of expectedPipeline) {
    await expect(pipeline.getByRole('listitem').filter({ hasText: label })).toContainText(String(count))
  }
  const pipelineCounts = (await pipeline.getByRole('listitem').allTextContents())
    .map((text) => Number(text.match(/^\s*(\d+)/)?.[1] ?? Number.NaN))
  expect(pipelineCounts).toHaveLength(5)
  expect(pipelineCounts.reduce((total, count) => total + count, 0)).toBe(32)

  await expect(page.getByText('Showing 1–25 of 32 orders')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Review / })).toHaveCount(25)
  await expect(page.getByRole('list', { name: /^Order milestones for / })).toHaveCount(25)
  await expect(page.getByRole('link', { name: /^Open Lightspeed product for System ID/ }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /^Open Lightspeed customer/ }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /^Open Lightspeed purchase order/ }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /^Open Shopify order/ }).first()).toBeVisible()
  expect(activityCalls).toBe(0)

  await page.getByRole('button', { name: /^Review SO/ }).first().click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('heading', { name: /^SO #/ })).toBeVisible()
  await expect(drawer.getByText('Activity fixture loaded')).toBeVisible()
  expect(activityCalls).toBe(1)
  await expect(drawer.getByRole('heading', { name: 'Open in source systems' })).toHaveCount(0)

  await drawer.getByRole('button', { name: 'Where to order' }).click()
  await expect(drawer.getByText('Fixture recommendation')).toBeVisible()
  expect(recommendationCalls).toBe(1)
  await expect(drawer.getByRole('button', { name: /override po|select po|add to po/i })).toHaveCount(0)
  await expect(drawer.getByText(/PO override/i)).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(drawer).not.toBeVisible()
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(page.getByText('Showing 26–32 of 32 orders')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Review / })).toHaveCount(7)
})
