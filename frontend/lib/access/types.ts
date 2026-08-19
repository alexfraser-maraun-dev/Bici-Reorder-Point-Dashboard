// Mirrors backend/app/services/access/registry.py. Feature keys are the contract
// between the two — the backend owns the list, the frontend maps keys to icons.

export type FeatureKey =
  | 'ordering'
  | 'special_orders'
  | 'price_intel'
  | 'how_to_use'
  | 'admin'
  | 'ordering.inventory'
  | 'ordering.demand'
  | 'ordering.purchase_orders'
  | 'ordering.po_tracker'
  | 'ordering.vendors'
  | 'ordering.brands'

export interface AccessState {
  email: string | null
  role: 'admin' | 'member'
  is_admin: boolean
  features: Record<string, boolean>
  default_ordering_tab: FeatureKey
}

export interface FeatureDefinition {
  key: FeatureKey
  label: string
  description: string
  group: string
  kind: 'page' | 'tab'
  parent: FeatureKey | null
  default_enabled: boolean
  always_on: boolean
  admin_only: boolean
  enabled: boolean
  customized: boolean
}

export interface UserAccessRecord {
  email: string
  role: 'admin' | 'member'
  overrides: Record<string, boolean>
  locked: boolean
  updated_at?: string
  updated_by?: string
}
