'use client'

// Reusable catalog-item search with matrix grouping: variants of the same
// matrix collapse under one header showing the matrix description, with
// per-variant attribute badges so "Rapha Core Bib - M / Black" is
// distinguishable from its siblings. Used by the pin-search on the tracked
// table and the "link item" flow on tracked URLs.

import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
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

function VariantRow({ item, actionLabel, pending, disabled, onSelect }: {
  item: ItemSearchResult
  actionLabel: string
  pending: boolean
  disabled: boolean
  onSelect: (item: ItemSearchResult) => void
}) {
  const variantAttrs = attrs(item)
  return (
    <button type="button" disabled={disabled} onClick={() => onSelect(item)}
            className="flex w-full items-center justify-between gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-muted disabled:pointer-events-none disabled:opacity-50">
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
        {pending
          ? <Spinner className="h-3.5 w-3.5" />
          : <span className="font-medium text-foreground">{actionLabel}</span>}
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
  // The query that produced `results` — drives the "no matches for …" empty state so
  // the user can tell a search actually ran (and returned nothing) vs. never started.
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null)
  const [openMatrices, setOpenMatrices] = useState<Set<string>>(new Set())
  // Which row/matrix action is committing (drives the inline spinner). `selectLock`
  // is a synchronous guard so a double-click or Enter-repeat can't fire the same
  // pin/track twice before the list clears — the source of the phantom double-pins.
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const selectLock = useRef(false)

  const runSearch = async () => {
    if (query.trim().length < 2 || searching) return
    setSearching(true)
    try {
      const found = await searchItems(query.trim())
      setResults(found)
      setSearchedQuery(query.trim())
      // auto-expand when only a couple of matrices come back
      const matrixIds = [...new Set(found.map((r) => r.item_matrix_id).filter(Boolean))] as string[]
      setOpenMatrices(new Set(matrixIds.length <= 2 ? matrixIds : []))
    } catch (e) {
      // Leave the empty-state off on failure — the toast explains it; showing
      // "no matches" would wrongly imply the catalog was searched and came back bare.
      setResults([])
      setSearchedQuery(null)
      toast.error(e instanceof Error ? e.message : 'Item search failed')
    } finally {
      setSearching(false)
    }
  }

  const clear = () => {
    setResults([])
    setQuery('')
    setSearchedQuery(null)
  }

  // Single-flight guard shared by variant pins and whole-matrix tracks: the first
  // activation takes the lock synchronously; any second one (double-click, stray
  // Enter) is dropped until the first resolves and the list clears.
  const runGuarded = async (key: string, action: () => void | Promise<void>) => {
    if (selectLock.current) return
    selectLock.current = true
    setPendingKey(key)
    try {
      await action()
      clear()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Action failed')
    } finally {
      selectLock.current = false
      setPendingKey(null)
    }
  }

  const select = (item: ItemSearchResult) => runGuarded(item.item_id, () => onSelect(item))
  const selectMatrix = (group: MatrixGroup) =>
    runGuarded(`matrix:${group.matrixId}`, () => onSelectMatrix!(group.matrixId!, group.description))

  const busy = pendingKey !== null
  const groups = groupByMatrix(results)
  const showEmpty = searchedQuery !== null && !searching && results.length === 0

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder={placeholder} value={query}
               onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && runSearch()}
               className="w-80" />
        <Button variant="outline" size="sm" onClick={runSearch}
                disabled={searching || query.trim().length < 2}>
          {searching ? <Spinner className="h-4 w-4" /> : <Search className="h-4 w-4" />}
          {searching ? 'Searching…' : 'Search'}
        </Button>
        {(results.length > 0 || searchedQuery !== null) && (
          <Button variant="ghost" size="sm" onClick={clear} disabled={busy}>Clear</Button>
        )}
      </div>
      {searching && (
        <div className="flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
          <Spinner className="h-4 w-4" /> Searching the catalog…
        </div>
      )}
      {showEmpty && (
        <p className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
          No matches for <span className="font-medium text-foreground">{searchedQuery}</span>.
        </p>
      )}
      {results.length > 0 && (
        <div className="space-y-1 rounded-md border p-2">
          {groups.map((group) =>
            group.matrixId ? (
              <div key={`m-${group.matrixId}`} className="rounded border border-dashed">
                <div className="flex items-center justify-between gap-2 px-2 py-1.5">
                  <button
                    type="button"
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
                            disabled={busy}
                            onClick={() => selectMatrix(group)}>
                      {pendingKey === `matrix:${group.matrixId}`
                        ? <Spinner className="h-3.5 w-3.5" />
                        : null}
                      {matrixLabel} all {group.items.length}
                    </Button>
                  )}
                </div>
                {openMatrices.has(group.matrixId) && (
                  <div className="space-y-0.5 border-t px-1 py-1">
                    {group.items.map((item) => (
                      <VariantRow key={item.item_id} item={item}
                                  actionLabel={actionLabel} onSelect={select}
                                  pending={pendingKey === item.item_id} disabled={busy} />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              group.items.map((item) => (
                <VariantRow key={item.item_id} item={item}
                            actionLabel={actionLabel} onSelect={select}
                            pending={pendingKey === item.item_id} disabled={busy} />
              ))
            )
          )}
        </div>
      )}
    </div>
  )
}
