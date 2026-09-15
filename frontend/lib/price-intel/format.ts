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

// The colour/size a competitor listing names, for the Matching queue. The
// connector's structured options win (`variant_options_json`, e.g.
// ["Hydrogen White Matt","Medium"]) — on SmartEtailing stores (The Bike Zone,
// Oak Bay) the title is the bare model name ("Ventral MIPS") and the options are
// the ONLY place the variant lives. Shopify/Magento titles carry it as a
// " - Colour / Size" tail, which is the fallback. Null when neither says.
export function listingVariantLabel(link: {
  variant_options_json?: string | null
  competitor_title?: string | null
}): string | null {
  if (link.variant_options_json) {
    try {
      const parsed: unknown = JSON.parse(link.variant_options_json)
      if (Array.isArray(parsed)) {
        const parts = parsed
          .map((p) => (p == null ? '' : String(p).trim()))
          .filter((p) => p !== '')
        if (parts.length > 0) return parts.join(' / ')
      }
    } catch {
      // malformed JSON: fall through to the title
    }
  }
  const title = link.competitor_title ?? ''
  const cut = title.lastIndexOf(' - ')
  if (cut === -1) return null
  const tail = title.slice(cut + 3).trim()
  if (!tail) return null
  return tail.split(/\s+\/\s+/).map((p) => p.trim()).filter(Boolean).join(' / ')
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
