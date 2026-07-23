# Codebase Audit — 2026-07-22 (price-intel emphasis)

Full sweep prompted by a recent uptick in bugs, with emphasis on Price Intelligence.
Every issue below was verified in source before fixing — and **all of them are fixed in this
pass**. Cross-checked against `CODE_AUDIT_2026-06-22.md` so known/deferred items aren't
re-reported.

Verification: backend `./venv/bin/python -m unittest` — 222 passing (the `test_sheets_sync`
loader error is pre-existing: it imports a removed `google_sheets` module). Frontend
`tsc --noEmit` clean, dev server compiles with no errors. Live checks against BigQuery via a
local backend: `/api/price-intel/summary|tracked|links` run clean on the new SQL, and the
`/api/forecast/coverage` seasonal lookup now hits for **88%** of item-rows (was 67%; the
remainder are non-product categories — Labour, Shipping, Gift Card — correctly neutral).

## Critical

### C1 — Shared fixed temp-table names → concurrent MERGE data corruption ✅ fixed
`repository._merge_upsert` / `insert_product_links` / `update_link_verdicts` and
`bigquery_sync.upsert_managed_skus` all staged MERGE rows into a deterministic
`<table>_temp` with `WRITE_TRUNCATE`. Two concurrent writers to the same target (a user
saving a tracked URL while the nightly run flushes link verdicts — both staging into
`pi_product_links_temp`) truncated each other's staged rows: silent lost writes / wrong-row
merges. **Most likely root cause of the recent duplicate-row / lost-write bugs.**
Fix: per-call unique staging names (`_tmp_<uuid>`) via a shared `_staging_table` context
manager — deleted in `finally`, 1h expiration as a leak backstop. Tests in
`test_price_intelligence_links.py` (`StagingTableTests`).

## High

### H1 — Failed nightly run retried every 60 s for the rest of the day ✅ fixed
The scheduler guard (`has_successful_run_on`) only counted `success/partial`, and `due`
stays true until midnight — so a systemically failing run re-fired a full scrape (SerpApi +
Anthropic spend + Slack alert) every tick. Fix: guard renamed to
`has_scheduler_blocking_run_on` and now counts **any scheduled attempt** (incl. `failed`),
plus an in-process last-attempt-date marker that holds even when BigQuery itself is down.
Tests in `test_price_intelligence_scheduler.py`.

### H2 — Duplicate tracked-product rows fanned out through three un-deduped joins ✅ fixed
`get_tracked_urls`, `get_product_links`, `count_pending_links` LEFT JOINed
`pi_tracked_products` raw. With duplicate `item_id` rows (the concurrent manual-pin MERGE
case the main tracked query already defends against), URLs got scraped twice (duplicate
observations/events), the Match queue showed phantom links, and the pending badge inflated.
Fix: all three join a deduped subquery (`SQL_TRACKED_DEDUPED`, same QUALIFY pattern as the
market query). Test: `TrackedJoinDedupeTests`. (C1 removes the duplicates' cause; this makes
reads robust regardless.)

### H3 — Forward-coverage heatmap silently lost seasonality ✅ fixed
`main.py get_forward_coverage` looked up seasonal profiles by `rec["category"]`
(= snapshot `category_name`, a leaf name) against profiles keyed by category-tree values —
1 in 3 item-rows missed and projected flat, defeating the seasonal-stockout purpose.
Fix: leaf-name → highest-volume `category_path` map built from the same history records
(volume breaks ties between same-named leaves under different parents), mirroring the
planning-service fallback. Measured live: 67% → 88% hit rate.

## Medium

### M1 — Market/undercut/MAP math ignored currency ✅ fixed (defensive)
Observations store `currency` but comparisons treated every price as CAD; a USD storefront
would read ~35% cheap and fire false undercut/MAP alerts. All competitors are currently CAD
(confirmed), so this is a guard: `sql_cad_only()` now filters non-CAD observations out of
the market CTEs (tracked/matrices/coverage/per-item), the digest stats, and the Python
undercut/MAP event logic (`_build_events`). Unreported currency → CAD.

### M2 — Full-scan nights never refreshed SERP/manual-link prices ✅ fixed
The confirmed-link re-check phase was skipped entirely in full-scan mode, but links on
connector-less competitors (SERP-discovered/manual) are never reached by the catalog crawl —
their prices went stale on every full-scan night. Fix: the phase always runs; in full-scan
mode it re-checks only links whose competitor's catalog was *not* actually crawled
(`crawled_competitor_ids`).

### M3 — KPI quick-filter was a sticky latch ✅ fixed
Radix unmounts inactive tabs, so returning to the Tracked tab re-forced the last KPI-tile
filter (discarding manual changes), and re-clicking the same tile did nothing. Fix:
consume-and-clear — the table applies the quick filter then immediately clears the parent
state, making it a one-shot event.

### M4 — `scrape/status` polled every 4 s forever ✅ fixed
`ScrapeStatusButton` hard-coded `useScrapeStatus(true)`. Now polls only while a run is live
or just started; the mount fetch still detects an already-running scrape and switches
polling on.

## Low (all fixed ✅)

- **L1** Bulk confirm/reject buttons in Match review: in-flight guard (disable + drop
  already-deciding ids) so a double-click can't fire duplicate batch POSTs.
- **L2** Digest "Regenerate": now polls until a *newer* digest lands (generation is a
  server background task) instead of instantly refetching the old one; button stays
  disabled meanwhile.
- **L3** Tracked-table pin/exclude row actions: per-item in-flight guard (they compute the
  new value from the rendered row, so rapid double-clicks sent stale values).
- **L4** `upsert_setting`/`delete_setting` now pop the settings cache under `_cache_lock`.
- **L5** `mark_competitor_scraped` invalidates the `competitors` cache so scrape-health
  statuses update mid-run.

## Still open (pre-existing, tracked in CODE_AUDIT_2026-06-22.md)

- **H1 (old)** Lightspeed/Shopify token cached per client instance → refresh churn
  (worsened by the 6-worker special-orders fan-out). Needs a deliberate auth-path change.
- **M2 (old)** 14d vs 30/60d demand windows from different sources — needs a live-BQ
  reconciliation query.
- **M1 (old)** Dead momentum thin-data guard — needs a product call on the threshold.

## Verified clean (no action needed)

Daily-series `MAX(snapshot_date_local)` pinning everywhere; reliable-history clamps;
forecasting math division guards; SQL parameterization (incl. price-intel); planning-store
optimistic concurrency; PO-watch ack/snooze; Shopify tiered matching; Lightspeed
pagination/429 handling; the Next.js auth proxy (all price-intel calls routed through it;
retry logic safe for non-idempotent methods); SWR key hygiene; `_to_price` parsing;
matrix-market aggregate queries (fan-out-safe via MIN/COUNT DISTINCT/QUALIFY).

## Note

`.claude/launch.json`'s backend command now uses an absolute path (the preview runner
choked on the relative `cd backend`).
