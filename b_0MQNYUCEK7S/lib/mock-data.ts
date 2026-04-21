import type {
  SkuLocationRow,
  RecommendationRun,
  WritebackAuditEntry,
  ManagedSku,
  WritebackStatus,
} from './types'

const brands = ['Nike', 'Adidas', 'Puma', 'New Balance', 'Reebok', 'Under Armour', 'Asics', 'Converse']
const vendors = ['SportsDirect Inc', 'Athletic Supply Co', 'Footwear Distributors', 'Global Sports', 'Premium Athletics']
const categories = ['Running Shoes', 'Basketball Shoes', 'Training', 'Casual Footwear', 'Athletic Apparel', 'Accessories']
const locations = ['Downtown Store', 'Mall Location', 'Outlet Center', 'Online Warehouse', 'Regional Distribution']

const products = [
  'Air Max 90', 'Ultra Boost 22', 'Classic Leather', 'Fresh Foam 1080', 'Gel-Kayano 29',
  'Chuck Taylor All Star', 'Suede Classic', 'Club C 85', 'HOVR Phantom 3', 'GEL-Nimbus 25',
  'Air Force 1', 'Stan Smith', 'Classic Cortez', '574 Core', 'Old Skool Pro',
  'React Infinity', 'Ultraboost Light', 'Nano X3', 'FuelCell Rebel', 'Novablast 3',
]

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

function randomChoice<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]
}

function generateSku(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
  let sku = ''
  for (let i = 0; i < 8; i++) {
    sku += chars.charAt(Math.floor(Math.random() * chars.length))
  }
  return sku
}

export function generateMockSkuData(count: number = 150): SkuLocationRow[] {
  const rows: SkuLocationRow[] = []

  for (let i = 0; i < count; i++) {
    const onHand = randomInt(0, 100)
    const onOrder = randomInt(0, 50)
    const currentReorderPoint = randomInt(5, 30)
    const currentDesiredLevel = randomInt(20, 80)
    const avgDailySales = Math.round((randomInt(1, 20) + Math.random()) * 10) / 10
    const leadTimeDays = randomInt(3, 21)
    const safetyStock = Math.ceil(avgDailySales * randomInt(3, 7))
    
    const recommendedReorderPoint = Math.ceil(avgDailySales * leadTimeDays + safetyStock)
    const recommendedDesiredLevel = Math.ceil(recommendedReorderPoint + avgDailySales * 14)
    
    const inventoryPosition = onHand + onOrder
    const needsOrder = inventoryPosition <= currentReorderPoint
    const changed = currentReorderPoint !== recommendedReorderPoint || currentDesiredLevel !== recommendedDesiredLevel
    const locked = Math.random() < 0.1
    const override = Math.random() < 0.08

    const statusOptions: WritebackStatus[] = ['pending', 'success', 'failed', 'not_pushed']
    const statusWeights = [0.1, 0.6, 0.05, 0.25]
    const rand = Math.random()
    let cumulative = 0
    let writebackStatus: WritebackStatus = 'not_pushed'
    for (let j = 0; j < statusOptions.length; j++) {
      cumulative += statusWeights[j]
      if (rand < cumulative) {
        writebackStatus = statusOptions[j]
        break
      }
    }

    rows.push({
      id: `row-${i + 1}`,
      sku: generateSku(),
      product: randomChoice(products),
      brand: randomChoice(brands),
      vendor: randomChoice(vendors),
      category: randomChoice(categories),
      location: randomChoice(locations),
      trailingUnitsSold: randomInt(10, 500),
      daysOutOfStock: randomInt(0, 14),
      avgDailySales,
      leadTimeDays,
      onHand,
      onOrder,
      inventoryPosition,
      currentReorderPoint,
      recommendedReorderPoint,
      currentDesiredLevel,
      recommendedDesiredLevel,
      suggestedBuyQty: Math.max(0, recommendedDesiredLevel - inventoryPosition),
      needsOrder,
      changed,
      locked,
      override,
      writebackStatus,
      safetyStock,
      lastPushedAt: writebackStatus === 'success' ? new Date(Date.now() - randomInt(0, 7) * 24 * 60 * 60 * 1000).toISOString() : undefined,
    })
  }

  return rows
}

export function generateMockRecommendationRuns(): RecommendationRun[] {
  return [
    {
      id: 'run-1',
      runDate: '2024-01-15T08:00:00Z',
      status: 'completed',
      trailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      totalRows: 1247,
      changedRows: 342,
      needsOrderCount: 89,
      duration: '2m 34s',
    },
    {
      id: 'run-2',
      runDate: '2024-01-14T08:00:00Z',
      status: 'completed',
      trailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      totalRows: 1245,
      changedRows: 287,
      needsOrderCount: 76,
      duration: '2m 28s',
    },
    {
      id: 'run-3',
      runDate: '2024-01-13T08:00:00Z',
      status: 'completed',
      trailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      totalRows: 1243,
      changedRows: 312,
      needsOrderCount: 82,
      duration: '2m 31s',
    },
    {
      id: 'run-4',
      runDate: '2024-01-12T08:00:00Z',
      status: 'failed',
      trailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      totalRows: 0,
      changedRows: 0,
      needsOrderCount: 0,
      duration: '0m 45s',
    },
    {
      id: 'run-5',
      runDate: '2024-01-11T08:00:00Z',
      status: 'completed',
      trailingDays: 90,
      forecastDays: 30,
      safetyDays: 7,
      totalRows: 1240,
      changedRows: 298,
      needsOrderCount: 71,
      duration: '2m 22s',
    },
  ]
}

export function generateMockWritebackAudit(): WritebackAuditEntry[] {
  const entries: WritebackAuditEntry[] = []
  const users = ['john.doe@company.com', 'jane.smith@company.com', 'mike.wilson@company.com']
  const fields: ('reorder_point' | 'desired_level')[] = ['reorder_point', 'desired_level']

  for (let i = 0; i < 50; i++) {
    const status = Math.random() < 0.92 ? 'success' : 'failed'
    entries.push({
      id: `audit-${i + 1}`,
      timestamp: new Date(Date.now() - randomInt(0, 14) * 24 * 60 * 60 * 1000 - randomInt(0, 86400000)).toISOString(),
      user: randomChoice(users),
      sku: generateSku(),
      location: randomChoice(locations),
      field: randomChoice(fields),
      oldValue: randomInt(5, 30),
      newValue: randomInt(10, 50),
      status,
      errorMessage: status === 'failed' ? 'Lightspeed API timeout - please retry' : undefined,
    })
  }

  return entries.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
}

export function generateMockManagedSkus(): ManagedSku[] {
  const skus: ManagedSku[] = []
  const users = ['john.doe@company.com', 'jane.smith@company.com', 'system']

  for (let i = 0; i < 80; i++) {
    skus.push({
      id: `managed-${i + 1}`,
      sku: generateSku(),
      product: randomChoice(products),
      brand: randomChoice(brands),
      vendor: randomChoice(vendors),
      category: randomChoice(categories),
      active: Math.random() < 0.9,
      addedAt: new Date(Date.now() - randomInt(30, 365) * 24 * 60 * 60 * 1000).toISOString(),
      addedBy: randomChoice(users),
    })
  }

  return skus
}

// Export static data for filters
export const filterOptions = {
  locations,
  vendors,
  brands,
  categories,
}
