# Codebase Audit — 2026-06-22

Full read-through of `backend/` and `frontend/` looking for bugs, logic errors, and
performance issues. Findings are bucketed by importance. Two items were fixed in this pass
(see **Fixed in this pass**); everything else is a proposed project, not yet touched.

Scope note: the frontend runs a non-standard, very new Next.js (16.2 / React 19, see
`frontend/AGENTS.md`), so frontend changes were left for a deliberate session. The core
forecasting/replenishment math is well covered by unit tests (74 forecasting + 7 inventory
+ 17 identity + 7 brand-sourcing tests all pass).

---

## ✅ Fixed in this pass (safe, verified)

### F1 — API rejected 3 of the 4 stockout-adjustment modes (HIGH)
`backend/app/main.py` validated `adjustment_mode` against `("shrink", "grow")`, but:
- the engine (`replenishment_engine.py`) supports `{"shrink", "min_days", "cap", "raw"}`,
- the UI dropdown (`sheets-replenishment.tsx`) offers exactly those four,
- the README documents those four,
- `"grow"` exists nowhere in the engine.

**Effect:** selecting **Min days**, **Cap**, or **Raw** in the dashboard returned HTTP 400,
so the table failed to load (SWR throws on non-OK). Only the default **Shrink** worked.
**Fix:** aligned the API validation to `("shrink", "min_days", "cap", "raw")`. API, engine,
and frontend now agree. `py_compile` clean.

### F2 — `tsconfig.tsbuildinfo` was tracked in git (LOW)
A generated TypeScript build artifact was committed (and was the noisy "modified" file in the
working tree). Added `*.tsbuildinfo` to `frontend/.gitignore` and `git rm --cached` it (kept on
disk). Keeps it out of future diffs.

> Note: `backend/app/main.py` now contains **both** the in-progress Shopify-health change and
> the F1 fix. They're in unrelated parts of the file.

---

## 🔴 High priority (correctness / production)

### H1 — Health-check polling re-authenticates every 30s
`useConnectionStatus` polls `/api/health/{lightspeed,bigquery,shopify}` every 30s. Each endpoint
does `LightspeedClient()` / `ShopifyClient()` fresh per request, and the bearer/OAuth token is
cached only on the **instance**. With no `LIGHTSPEED_BEARER_TOKEN` env set (the README only
requires the refresh token), every Lightspeed health poll calls `_refresh_access_token()`, and
every Shopify poll calls `_fetch_token()` (client-credentials exchange). That's ~2 token
round-trips every 30s, indefinitely, per open browser tab — needless latency on the status dots
and a real OAuth rate-limit risk. The new Shopify dot doubles this.
**Suggested:** a module-level cached client (or process-level token cache with expiry) reused
across requests, like the BigQuery client singleton in `bigquery_sync.get_bq_client()`.

### H2 — Verify production CORS is configured
`main.py` defaults `CORS_ALLOWED_ORIGINS` to `localhost:3002` only, and `render.yaml` does not
set it for the backend service. Browser calls go directly to the backend
(`NEXT_PUBLIC_API_URL`), so if `CORS_ALLOWED_ORIGINS` isn't set in the Render dashboard to the
production frontend origin, every request would be blocked. Prod reportedly works, so it's
likely set in the dashboard — but it should be pinned in `render.yaml` (or documented) so a
fresh deploy can't silently break.

---

## 🟠 Medium priority (logic / robustness)

### M1 — Momentum's thin-data guard is partly dead code
`calculate_momentum_status` treats `active_days_14 < 3` and `active_days_15_30d < 3` as
"insufficient data", but `effective_active_days()` floors active days at **3**, so those two
conditions can never be true. Only `raw_units_sold_60d <= 0` or `active_days_31_60d < 7` can
trip the guard. Net effect: an item with a single recent in-stock day can still be classified
`surging`/`rising` (the separate `spiky` branch catches some, not all, of these). Decide whether
the guard should run on *raw* active days (pre-floor) and adjust.

### M2 — 14d vs 30/60d demand come from different sources
`fetch_tagged_items_metrics` builds `total_units_sold_14` from deduped `sale_line_history`, but
`total_units_sold_30/60` from the snapshot view's `sales_units_l30d/l60d`. The engine derives the
15–30d window as `total_units_sold_30 - total_units_sold_14`. If the two sources dedupe/define
sales differently, that subtraction can be distorted (it's guarded with `max(0, …)`, so it fails
*quietly* rather than loudly). Worth a reconciliation check that the 14d source ≈ the snapshot's
trailing-14 so the mid-window split is trustworthy.

### M3 — `useConnectionStatus` per-cycle timeout isn't cleared
In `lib/hooks.ts`, `checkHealth()` returns `() => clearTimeout(timeoutId)`, but the value is
discarded (it's called as `checkHealth()`, not wired into the effect's cleanup). The 8s abort
timer just auto-fires each cycle. Minor timer churn; also the `AbortController` is recreated each
cycle so an in-flight request from a previous cycle isn't aborted on unmount. Low-impact, easy fix.

---

## 🟡 Low priority (cleanup / tech debt — no behavior change)

### L1 — Large committed cruft: `b_0MQNYUCEK7S/` (101 tracked files)
A v0.dev export — a stale duplicate of the frontend (its own `package.json`, `pnpm-lock.yaml`,
`app/`, `components/`, including pages that were deliberately deleted from the real frontend per
commit `daa55aa`). Dead weight; recommend removing the whole directory.

### L2 — `frontend/src/app/` is leftover create-next-app boilerplate
Contains the default "edit page.tsx / Next.js logo" starter. Next.js uses the root `app/` when
both exist, so `src/app/` is ignored — but it's confusing. Remove it.

### L3 — Two `next.config` files
`next.config.mjs` (real config) and `next.config.ts` (empty boilerplate) coexist. Consolidate to
one. Separately, the `.mjs` sets `typescript.ignoreBuildErrors: true`, so type errors never fail
the build — worth revisiting once the codebase is stable (it currently hides regressions).

### L4 — Dead backend module: `recommendation_engine.py`
`calculate_recommendation` (the "V1 formula") is never imported anywhere. The live path is
`replenishment_engine.process_recommendations`. Remove or clearly mark legacy.

### L5 — Unused SQLAlchemy layer: `backend/app/db/`
`models.py` + `database.py` (and the `sqlalchemy` / `psycopg2-binary` deps) are only referenced
by old root scripts (`test_db.py`, `flush_logs.py`). The app persists everything to BigQuery.
Either delete the ORM layer + deps or document it as intentionally-retained legacy.

### L6 — `process_recommendations(momentum_data=…)` is an unused parameter
Threaded through the signature and every call site but never read (momentum is computed
internally). Remove the param and the `momentum_data={}` args.

### L7 — Loose scripts at `backend/` root
~15 `test_*.py`/`debug_*.py`/`list_*.py`/`exchange_ls_code.py` + `backend/scratch/` mix real
unittest suites (`test_forecasting`, `test_inventory_status`, `test_identity_fields`,
`test_brand_sourcing`, `test_special_orders`) with one-off operational scripts. No `tests/` dir or
pytest config. Suggest a `tests/` package for the real suites and a `scripts/` (or archive) for
the rest, so the suite is discoverable/CI-runnable. (`pytest` isn't installed in either venv; the
suites run via `python -m unittest`.)

### L8 — Stray `console.log` in the health poller
`lib/hooks.ts:394` logs `[HealthCheck] Pinging backend at: …` on every 30s cycle. Left for the
owner since `hooks.ts` is mid-edit; remove when convenient.

### L9 — Minor query naming in `fetch_active_vendor_lead_times`
`active_sample_count` duplicates `active_po_count` (both `COUNT(DISTINCT order_id)`), and
`last_po_ordered_at` is actually `MAX(first_received_at)`. Cosmetic, but misleading if surfaced.

---

## ⚡ Performance opportunities

- **P1 (= H1):** cache the Lightspeed/Shopify clients/tokens at module level — the single biggest
  easy win, removes ~2 OAuth round-trips every 30s per tab.
- **P2:** `fetch_tagged_items_metrics` runs a 60-day date-spine cross-join on every cold cache
  (5-min TTL, warmed on startup). Fine today; if the qualified-item set grows, push the stockout
  day-count into a scheduled/materialized BQ view instead of recomputing per request.
- **P3:** `get_replenishment_data` recomputes all recommendations on each debounced slider change.
  Cheap CPU + server-cached BQ, so OK — flagged only as a place to add response memoization if the
  row count grows a lot.

---

## Notes / verified-healthy

- Forecasting module (`forecasting.py`) is pure, well-documented, and fully unit-tested — no
  issues found.
- PO reconciliation/push (`po_service.py`) idempotency logic (re-check open POs at push, top-up
  vs. append vs. create) reads correctly.
- Special-order triage + Shopify matching (`special_order_service.py`, `shopify_match.py`) is
  clean; matching is pure and resilient (returns empty on any Shopify/BQ failure).
- SQL with user-controlled values uses parameterized queries; the f-string interpolations are
  operator-controlled constants (`TARGET_SHOP_IDS`, dataset names) or FastAPI-typed `int` limits,
  so not injectable.
