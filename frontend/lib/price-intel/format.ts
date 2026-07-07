// Shared display helpers so "our products" look identical everywhere in the
// price-intelligence UI.

// Lightspeed Retail item detail page (opens the product in the LS back office).
export const lightspeedItemUrl = (itemId: string) =>
  `https://us.merchantos.com/?name=item.views.item&form_name=view&id=${encodeURIComponent(itemId)}&tab=details`

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
