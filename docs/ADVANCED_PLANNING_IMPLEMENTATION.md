# Advanced Planning Implementation

## Delivered vertical slice

- Complete Monday-based SKU-location demand facts from `sales_master_view`, excluding
  warranty workorder demand and incomplete current weeks.
- Exposure-corrected circular 52-week category profiles with ISO week 53 handling.
- Per-item champion selection across weighted velocity, seasonal naive, hierarchical
  seasonality, TSB and damped ETS, using rolling 1/4/8/13/26/52-week origins.
- WAPE, MASE, signed bias, P90 quantile loss and an inventory-cost proxy.
- P50/P80/P90/P95 weekly forecasts, scheduled receipt timing, projected inventory,
  need-by dates, case-pack/MOQ rounding, COGS/revenue/spend rollups and priority scoring.
- Landed cost and retail price from the latest master snapshot; missing cost/vendor is
  a blocking exception rather than a zero-cost substitution.
- Versioned forecast-to-PO lineage: run, model, assumptions, source snapshot and
  recommendation IDs.
- Transactional Postgres PO drafts, lines, overrides, workflow history and idempotent
  preview actions, with SQLite used only for local/test operation.
- Buyer workbench for plan generation, financial measure toggles, recommendation
  selection, editable draft quantities/costs, line removal/manual lines, reconciliation,
  approval and downloadable exact Lightspeed preview JSON.
- Buyer-controlled planning scope (tagged catalog, brand, vendor, top category or
  explicit SKU/item IDs), shop filtering, model override, service quantile, history,
  demand multiplier, review period, lead-time override and seasonality controls.
- Persisted planning runs in Postgres (latest 12 unreferenced runs plus every run tied
  to a live draft), so leaving the PO tab or restarting a web worker does not lose work.
- Product-first display, Lightspeed item deep links, Bici shop names, recommendation
  filters, an in-tool model legend and Units/COGS Demand & Seasonality views.
- Paginated, fail-closed Lightspeed PO reads and normalized states. Only unsent,
  unreceived, incomplete and non-archived POs can be previewed as update targets;
  ordered or partially received POs are read-only inbound supply.
- Explicit buyer selection among eligible unsent Lightspeed POs. Empty POs are read
  from Lightspeed headers; they cannot exist in a line-grain warehouse view.
- One fail-closed, paginated Lightspeed PO header snapshot shared by every workbench
  vendor/shop selector. Header-only loading keeps the account-wide snapshot small;
  planning lines come from `v_po_current_lines` and Reconcile/Preview load the
  selected vendor/shop lines directly from Lightspeed. The default five-minute TTL is configurable with
  `LS_PO_SNAPSHOT_TTL_SECONDS`; manual refresh replaces the complete snapshot.
  Reconcile and Preview intentionally bypass this cache and perform a fresh read.
- Scheduled on-order supply from `v_po_current_lines`, using remaining units not
  allocated to confirmed open special orders and expected-arrival dates by PO.
- Vendor identity repair using active brand sourcing rules and a normalized vendor
  name-to-ID directory derived from current PO history. A live read-only audit of all
  531 tagged SKU-location rows found zero missing costs and zero unresolved vendors
  after this resolution step (2026-07-15).

## Safety invariants

- `LiveLightspeedReadGateway` has no mutation surface.
- Automated PO tests use `FakeLightspeedGateway` and sanitized in-memory fixtures.
- Process-level guards reject Lightspeed mutation verbs before authentication/network.
- Legacy live mutation scripts are quarantined from automated test discovery.
- New order previews omit `orderedDate`; BICI never places a vendor order.
- Both HTTP push routes fail closed with 403 in the preview-only rollout.

## Remaining rollout work

These depend on additional data quality, operating infrastructure or a separately
approved phase and are intentionally not presented as production-complete:

- Daily availability/lost-sales facts and empirical vendor lead-time distributions.
- Persistent BigQuery forecast/backtest output tables and portfolio acceptance reports.
- SKU/category/global saved service-level override precedence beyond the per-run buyer setting.
- Product-search-backed manual PO lines and explicit alternative-vendor selection.
- Promotion, price, weather, event, lifecycle and replacement-product driver pipelines.
- LightGBM challenger and MinT hierarchy reconciliation.
- External OTB connector. The typed `OTBProvider` and category-location-month financial
  dimensions are already present for that future integration.
- Any live PO synchronization. Its first mutation test still requires immediate,
  explicit user approval with account, shop, vendor, payload, timing and recovery plan.
