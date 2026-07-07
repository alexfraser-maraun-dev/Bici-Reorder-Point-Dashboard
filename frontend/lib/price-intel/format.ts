// Shared display helpers so "our products" look identical everywhere in the
// price-intelligence UI.

// Lightspeed Retail item detail page (opens the product in the LS back office).
export const lightspeedItemUrl = (itemId: string) =>
  `https://us.merchantos.com/?name=item.views.item&form_name=view&id=${encodeURIComponent(itemId)}&tab=details`

// For a MAP-tagged item the floor is our own retail price (MAP == our default
// price); an explicit map_price override still wins if one is ever set.
export function mapFloor(p: {
  is_map?: boolean | null
  map_price?: number | null
  current_retail?: number | null
}): number | null {
  if (!p.is_map) return null
  return p.map_price ?? p.current_retail ?? null
}

// A MAP-tagged item is "in violation" when the lowest in-stock competitor price
// is below our floor — i.e. someone is undercutting our advertised price.
export function isMapViolation(p: {
  is_map?: boolean | null
  map_price?: number | null
  current_retail?: number | null
  market_min_in_stock?: number | null
}): boolean {
  const floor = mapFloor(p)
  return floor != null && p.market_min_in_stock != null
    && p.market_min_in_stock < floor - 0.01
}

// Consistent identifier sub-line: brand · UPC <value> · System ID <system sku>.
// "System ID" is the Lightspeed system SKU — the identifier the user actually
// searches on in LS (not the internal item_id, which only appears in the URL).
// Missing UPC is called out (coverage matters); missing pieces are omitted.
export function itemIdentity(parts: {
  brand?: string | null
  upc?: string | null
  systemSku?: string | null
}): string {
  return [
    parts.brand,
    parts.upc ? `UPC ${parts.upc}` : 'no UPC',
    parts.systemSku ? `System ID ${parts.systemSku}` : null,
  ].filter(Boolean).join(' · ')
}
