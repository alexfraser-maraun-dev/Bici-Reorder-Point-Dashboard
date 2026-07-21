'use client'

// Reusable catalog-item search with matrix grouping: variants of the same
// matrix collapse under one header showing the matrix description, with
// per-variant attribute badges so "Rapha Core Bib - M / Black" is
// distinguishable from its siblings. Used by the pin-search on the tracked
// table and the "link item" flow on tracked URLs.

import { useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { searchItems } from '@/lib/price-intel/hooks'
import { itemIdentity } from '@/lib/price-intel/format'
import type { ItemSearchResult } from '@/lib/price-intel/types'
import { ChevronDown, ChevronRight, Layers, Search } from 'lucide-react'

const fmt = (v: number | null | undefined) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)

function attrs(r: ItemSearchResult): string[] {
  return [r.attribute_1, r.attribute_2, r.attribute_3].filter(
    (a): a is string => !!a && a.trim() !== ''
  )
}

interface MatrixGroup {
  matrixId: string | null
  description: string | null
  items: ItemSearchResult[]
}

function groupByMatrix(results: ItemSearchResult[]): MatrixGroup[] {
  const groups: MatrixGroup[] = []
  const byMatrix = new Map<string, MatrixGroup>()
  for (const r of results) {
    if (r.item_matrix_id) {
      let group = byMatrix.get(r.item_matrix_id)
      if (!group) {
        group = { matrixId: r.item_matrix_id, description: r.matrix_description, items: [] }
        byMatrix.set(r.item_matrix_id, group)
        groups.push(group)
      }
      group.items.push(r)
    } else {
      groups.push({ matrixId: null, description: null, items: [r] })
    }
  }
  return groups
}

function VariantRow({ item, actionLabel, onSelect }: {
  item: ItemSearchResult
  actionLabel: string
  onSelect: (item: ItemSearchResult) => void
}) {
  const variantAttrs = attrs(item)
  return (
    <button onClick={() => onSelect(item)}
            className="flex w-full items-center justify-between gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-muted">
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium">{item.title}</span>
        {variantAttrs.map((a) => (
          <Badge key={a} variant="outline" className="shrink-0 px-1.5 py-0 text-[11px]">{a}</Badge>
        ))}
        <span className="shrink-0 text-xs text-muted-foreground">
          {itemIdentity({ brand: item.brand, upc: item.upc_normalized, systemSku: item.system_sku })}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
        {fmt(item.current_retail)}
        <span className="font-medium text-foreground">{actionLabel}</span>
      </span>
    </button>
  )
}

export function ItemSearchPicker({
  actionLabel = 'Pin',
  matrixActionLabel,
  placeholder = 'Search name / SKU / id…',
  onSelect,
  onSelectMatrix,
}: {
  actionLabel?: string
  // Label for the whole-matrix action (defaults to actionLabel). The tracked table
  // passes "Track" because subscribing a matrix is a persistent, self-syncing action,
  // not a one-shot pin.
  matrixActionLabel?: string
  placeholder?: string
  onSelect: (item: ItemSearchResult) => void | Promise<void>
  // when provided, matrix headers offer "<matrixActionLabel> all N variants"
  onSelectMatrix?: (matrixId: string, description: string | null) => void | Promise<void>
}) {
  const matrixLabel = matrixActionLabel ?? actionLabel
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ItemSearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [openMatrices, setOpenMatrices] = useState<Set<string>>(new Set())

  const runSearch = async () => {
    if (query.trim().length < 2 || searching) return
    setSearching(true)
    try {
      const found = await searchItems(query.trim())
      setResults(found)
      // auto-expand when only a couple of matrices come back
      const matrixIds = [...new Set(found.map((r) => r.item_matrix_id).filter(Boolean))] as string[]
      setOpenMatrices(new Set(matrixIds.length <= 2 ? matrixIds : []))
    } catch (e) {
      setResults([])
      toast.error(e instanceof Error ? e.message : 'Item search failed')
    } finally {
      setSearching(false)
    }
  }

  const clear = () => {
    setResults([])
    setQuery('')
  }

  const select = async (item: ItemSearchResult) => {
    await onSelect(item)
    clear()
  }

  const groups = groupByMatrix(results)

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder={placeholder} value={query}
               onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && runSearch()}
               className="w-80" />
        <Button variant="outline" size="sm" onClick={runSearch} disabled={searching}>
          <Search className="h-4 w-4" /> Search
        </Button>
        {results.length > 0 && (
          <Button variant="ghost" size="sm" onClick={clear}>Clear</Button>
        )}
      </div>
      {results.length > 0 && (
        <div className="space-y-1 rounded-md border p-2">
          {groups.map((group) =>
            group.matrixId ? (
              <div key={`m-${group.matrixId}`} className="rounded border border-dashed">
                <div className="flex items-center justify-between gap-2 px-2 py-1.5">
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
                    onClick={() =>
                      setOpenMatrices((prev) => {
                        const next = new Set(prev)
                        if (next.has(group.matrixId!)) next.delete(group.matrixId!)
                        else next.add(group.matrixId!)
                        return next
                      })
                    }>
                    {openMatrices.has(group.matrixId) ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <Layers className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-medium">
                      {group.description || group.items[0]?.title}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {group.items.length} variant{group.items.length === 1 ? '' : 's'}
                    </span>
                  </button>
                  {onSelectMatrix && group.items.length > 1 && (
                    <Button variant="outline" size="sm" className="h-7 shrink-0 text-xs"
                            onClick={async () => {
                              await onSelectMatrix(group.matrixId!, group.description)
                              clear()
                            }}>
                      {matrixLabel} all {group.items.length}
                    </Button>
                  )}
                </div>
                {openMatrices.has(group.matrixId) && (
                  <div className="space-y-0.5 border-t px-1 py-1">
                    {group.items.map((item) => (
                      <VariantRow key={item.item_id} item={item}
                                  actionLabel={actionLabel} onSelect={select} />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              group.items.map((item) => (
                <VariantRow key={item.item_id} item={item}
                            actionLabel={actionLabel} onSelect={select} />
              ))
            )
          )}
        </div>
      )}
    </div>
  )
}
