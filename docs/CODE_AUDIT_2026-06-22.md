# Codebase Audit — 2026-06-22

Full read-through of `backend/` and `frontend/` for bugs, logic errors, and performance issues.
Most low/medium items have now been actioned (see **Completed**); a few are intentionally left
open because they need a product decision or live BigQuery access (see **Still open**).

Verification after every change: backend `./venv/bin/python -m unittest` (**105 tests pass**) +
`py_compile` of all app modules; frontend dev server reloads with **no console errors**.

Constraint honoured throughout: changes improve the tool (clarity, footprint, correctness)
without altering runtime behaviour, except where explicitly noted (none of the completed items
change app behaviour).

---

## ✅ Completed

| ID | What | Files |
|----|------|-------|
| **F1** | **Bug:** API rejected 3 of 4 stockout modes. `adjustment_mode` was validated against `("shrink","grow")` but engine/UI/README use `shrink/min_days/cap/raw` — selecting **Min days/Cap/Raw** returned HTTP 400 and broke the table. Aligned the API to the four real modes. *(committed mid-session)* | `backend/app/main.py` |
| **F2** | Untracked the generated `tsconfig.tsbuildinfo` build artifact + gitignored `*.tsbuildinfo`. *(committed mid-session)* | `frontend/.gitignore` |
| **L1** | Removed `b_0MQNYUCEK7S/` — a 101-file v0.dev export duplicate of the frontend (incl. pages already deleted from the real app). | (dir) |
| **L2** | Removed `frontend/src/app/` — leftover create-next-app starter ("edit page.tsx"). Confirmed dead: `@/*` → root `./*`, nothing imports `src/`, root `app/` wins over `src/app/`. | (dir) |
| **L3** | Removed empty `frontend/next.config.ts`. Confirmed `next.config.mjs` is the active config (Next's `CONFIG_FILES` order is `js → mjs → ts`, first match wins), so this is a no-op cleanup. | `frontend/next.config.ts` |
| **L4** | Removed dead `recommendation_engine.py` (the unused "V1 formula") and its only caller, the legacy `test_pipeline.py`. | backend |
| **L5** | Removed the unused SQLAlchemy layer `backend/app/db/` + the legacy scripts that imported it (`test_db.py`, `test_connections.py`), and dropped now-orphaned `sqlalchemy` + `psycopg2-binary` from `requirements.txt` (smaller image / less memory on the 512 MB Render plan). The running app persists everything to BigQuery. | backend |
| **L6** | Removed the unused `momentum_data` param threaded through `process_recommendations` and both call sites. | backend |
| **L8** | Removed the per-cycle `console.log('[HealthCheck] Pinging…')` noise. | `frontend/lib/hooks.ts` |
| **M3** | `useConnectionStatus` now tracks the in-flight cycle and clears the abort timer once all three probes settle, and aborts + clears on unmount (previously the per-cycle `clearTimeout` cleanup was discarded). | `frontend/lib/hooks.ts` |

---

## ⏸️ Still open (need a decision or live access)

### H1 — Health-check polling re-authenticates every ~30s  *(highest-value remaining)*
`LightspeedClient`/`ShopifyClient` are instantiated per-request and cache tokens only on the
instance, so every health poll triggers an OAuth refresh/exchange (~2 token round-trips / 30s /
open tab) — latency on the dots + an OAuth rate-limit risk, now tripled by the Shopify dot.
**Fix:** a module-level cached client/token with expiry, like `bigquery_sync.get_bq_client()`.
Left for a deliberate change since it touches the auth path on every integration.

### H2 — Verify production CORS
`main.py` defaults `CORS_ALLOWED_ORIGINS` to localhost; `render.yaml` doesn't set it. Confirm the
prod frontend origin is set in the Render dashboard, then pin it in `render.yaml` so a fresh
deploy can't silently break. (Needs deploy-env access.)

### M1 — Momentum thin-data guard is partly dead
`calculate_momentum_status` checks `active_days_14 < 3` / `active_days_15_30d < 3`, but
`effective_active_days()` floors active days at 3, so those never fire — momentum can read
`surging`/`rising` on very thin recent evidence. Fixing it changes momentum *labels* (informational
only; doesn't touch ROP/DL), so it needs a product call on the right evidence threshold (e.g. guard
on raw distinct-sale-days). Deferred to avoid silently shifting the momentum column.

### M2 — 14d vs 30/60d demand come from different sources
`total_units_sold_14` is deduped `sale_line_history`; `total_units_sold_30/60` are the snapshot
view's `sales_units_l30d/l60d`. The 15–30d window is `30d − 14d`, so a definitional gap distorts it
(guarded with `max(0,…)`, i.e. fails quietly). Needs a reconciliation query against live BigQuery
(no offline creds) to confirm the 14d source ≈ the snapshot's trailing-14.

### L7 — Loose backend scripts / no test layout
Remaining root scripts (`debug_*.py`, `list_*.py`, `exchange_ls_code.py`, `reauthorize_lightspeed.py`,
`flush_logs.py`, `test_health.py`, `test_data_api.py`, `test_ls_put*.py`, `test_sheets_sync.py`,
`test_writeback_trial.py`, `test_creds.py`, `scratch/`) mix real `unittest` suites with one-off ops
scripts. Suggest a `tests/` package + a `scripts/` (or archive) dir so the suite is CI-discoverable.
Deferred (pure reorg; the `sys.path` shims in the tests need care) — low value, easy to get wrong.

### L9 — Cosmetic SQL in `fetch_active_vendor_lead_times`
`active_sample_count` duplicates `active_po_count`, and `last_po_ordered_at` actually computes
`MAX(first_received_at)`. The UI consumes `active_po_count`/`last_po_ordered_at`, so the names can't
change without a frontend change; left as-is rather than churn a working query for cosmetics.

---

## Notes / verified-healthy
- `forecasting.py` is pure, documented, fully unit-tested — no issues.
- PO reconcile/push idempotency (`po_service.py`) and special-order triage / Shopify matching
  (`special_order_service.py`, `shopify_match.py`) read correctly and fail safe.
- User-controlled SQL values use parameterized queries; f-string interpolations are
  operator-controlled constants or FastAPI-typed ints — not injectable.
