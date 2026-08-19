# BICI Demand Planning & Procurement Cockpit

Internal weekly demand-planning and buyer workbench. ROP/DL remains a secondary
output; the primary workflow is forecast → recommendation → editable PO draft →
reconcile → approve → Lightspeed preview.

## Lightspeed safety boundary

Live Lightspeed writes are disabled. Automated tests use `FakeLightspeedGateway`,
the live read adapter exposes no mutation methods, both PO push routes return 403,
and the client blocks POST/PUT/PATCH/DELETE before network access unless every write
gate is satisfied. No development or acceptance test may enable those gates without
the user's explicit approval immediately before the exact live mutation test.

Keep these values in development, CI, staging and the preview-only rollout:

```text
PLANNING_V2_ENABLED=true
PO_WRITE_V2_ENABLED=false
LIGHTSPEED_WRITES_ENABLED=false
```

The dormant client gate also requires a non-empty `LIGHTSPEED_WRITE_APPROVAL_TOKEN`
and `LIGHTSPEED_WRITE_SHOP_ALLOWLIST`. These are defense in depth, not permission to
run a live test.

Transactional PO drafts and planner overrides use Postgres when `DATABASE_URL` is a
Postgres URL. SQLite is a local/test fallback. BigQuery remains the analytical source
for demand history and inventory snapshots. Completed interactive planning runs are
also retained in Postgres so tab navigation does not lose them; retention is bounded
to the latest 12 unreferenced runs plus runs attached to active drafts, making the
1 GB Render database suitable for the current rollout.

The advanced weekly planner can explicitly evaluate the safe `auto-replen` population,
one brand, one vendor, one top-level category, or a supplied list of SKUs/item IDs.
It reads dated on-order supply from `v_po_current_lines`; the paginated Lightspeed
read gateway remains necessary to list empty unsent POs for buyer routing. The
workbench shares one complete paginated Lightspeed PO header snapshot across
vendor/shop selectors (five-minute default TTL) while Reconcile and Preview always
perform a fresh vendor/shop read with line relations.

## Current Architecture

1. **BigQuery is the data engine.**
   - Qualifying products come from `bici-klaviyo-datasync.BiciReorderPointDashboard.replen_qualified_items`.
   - Core product, inventory, sales, and PO facts come from `bici-klaviyo-datasync.light_speed_retailne.v_master_snapshot_latest`.
   - The app uses the latest available `snapshot_date_local` from the master snapshot view.
   - Stockout days are still calculated from `item_shop_history` because the snapshot view does not currently expose trailing out-of-stock day counts.

2. **FastAPI backend calculates recommendations.**
   - Reads qualified `auto-replen` items from BigQuery.
   - Limits rows to shop IDs `2`, `3`, and `20`: Bici Victoria, Bici Adanac, and Bici Langford.
   - Calculates guarded stockout-adjusted weighted velocity, safety stock, reorder points, desired levels, and suggested order quantity.
   - Retains the legacy ROP/DL calculation as a secondary, independently gated workflow.

3. **Next.js frontend is the review surface.**
   - Displays raw and adjusted 14d/30d/60d demand in the same columns.
   - Shows QOH, QOO, cover, recommended ROP, recommended DL, and order quantity by location.
   - Allows manual review before pushing to Lightspeed.

4. **Render hosts production.**
   - Backend is deployed as a Docker web service.
   - Frontend is deployed as a Node/Next.js web service.

## Product Qualification

Only products currently tagged `auto-replen` in Lightspeed should qualify for the dashboard.

The qualification logic lives in:

```text
bici-klaviyo-datasync.BiciReorderPointDashboard.replen_qualified_items
```

The backend joins that qualified item list to the latest master snapshot view, rather than reading directly from raw tag history.

## Locations

The app only cares about these Lightspeed shop IDs:

```text
2  = Bici Victoria
3  = Bici Adanac
20 = Bici Langford
```

Rows from other shops are ignored.

## Key Metrics

### QOH

Quantity on hand. Sourced from:

```text
v_master_snapshot_latest.qoh
```

### QOO

Quantity on order. Sourced from:

```text
v_master_snapshot_latest.po_units_remaining
```

Earlier versions calculated QOO manually from `order_line_history` and `order_history`, but history snapshots caused inflated values. The app now relies on the trusted master snapshot view.

### 14d / 30d / 60d

Each dashboard cell shows two numbers:

- Main number: raw units sold from the snapshot view.
- Smaller number underneath: adjusted demand using the selected stockout adjustment mode.

Raw sales fields:

```text
14d is derived from deduped sale history
v_master_snapshot_latest.sales_units_l30d
v_master_snapshot_latest.sales_units_l60d
```

The dashboard sends the selected stockout adjustment mode to the API as:

```text
adjustment_mode=shrink|min_days|cap|raw
```

The default mode is `shrink`.

### Stockout Adjustment Modes

Each mode starts from the same two daily velocities:

```text
raw daily velocity       = raw units sold / period days
adjusted daily velocity  = raw units sold / active in-stock days
```

The selected guardrail determines which daily velocity is used for the smaller adjusted 14d/30d/60d values and for recommendation math:

- `shrink`: default. Blends raw velocity toward stockout-adjusted velocity based on evidence. Confidence is `min(1, adjustment active days / 10)`.
- `min_days`: requires at least 7 adjustment active days. If there are fewer than 7, uses raw velocity.
- `cap`: allows stockout adjustment, but caps adjusted period demand at `2x` raw sales.
- `raw`: uses the unprotected stockout-adjusted velocity directly.

For example, with 1 unit sold and only 1 active in-stock day in a 30-day period:

```text
raw demand       = 1
raw adjustment   = 30
shrink mode      = about 5.1
min-days mode    = 1
cap mode         = 2
```

The demand weighting below is used as the base daily velocity for replenishment math.

When QOH is zero or negative, that day still counts as an out-of-stock day.
For adjusted-demand math, however, adjustment active days are guarded so they
never fall below the number of distinct sale days in the window or a 3-day
minimum. This keeps negative-inventory sales from creating unrealistically high
adjusted demand.

### Demand Weighting

The replenishment math uses a weighted blend of the last 14 days, days 15-30, and days 31-60:

```text
weighted velocity = (adjusted 14d daily velocity * 14d weight)
                  + (adjusted days 15-30 daily velocity * 15-30d weight)
                  + (adjusted days 31-60 daily velocity * 31-60d weight)
```

The dashboard control is labeled `Demand Weighting` and defaults to `40% / 40% / 20%`.
Preset buttons provide stable, balanced, and reactive starting points, and the custom weights must total `100%`.

### Momentum

Momentum is an informational demand-shape flag, separate from inventory status.
It compares stockout-adjusted daily velocities across 14d, 15-30d, and 31-60d:

```text
surging / rising / spiky / flat / cooling / insufficient data
```

Momentum does not directly change ROP or DL. It only explains whether recent demand is accelerating, cooling, or too thin to classify confidently. `Rising` now requires stronger multi-window evidence instead of a single mild increase.

### ROP

Recommended reorder point:

```text
(weighted velocity * growth multiplier * lead time days) + safety stock
```

### DL

Recommended desired level:

```text
weighted velocity * growth multiplier * forecast period
```

### Suggested Order Quantity

```text
max(0, recommended desired level - (max(0, QOH) + QOO))
```

Negative QOH remains visible in the dashboard, but replenishment math treats it
as zero on hand so inventory corrections do not inflate the suggested order.

## Backend Endpoints

```text
GET /api/replenishment/data
```

Returns dashboard recommendations grouped by location.

```text
GET /api/replenishment/debug
```

Returns production-safe counts showing whether qualified products are making it through the BigQuery joins.

```text
GET /api/replenishment/debug/item/{item_id}
```

Returns raw-vs-deduped diagnostics for one item, useful when investigating sales or PO discrepancies.

```text
POST /api/replenishment/push
```

Pushes selected ROP/DL updates to Lightspeed.

## Production Environment Notes

Backend dependencies include `db-dtypes`, which is required by BigQuery/Pandas when using `to_dataframe()`.

Render free instances have a 512MB memory limit. The backend Docker service is configured to run one Gunicorn worker to avoid multiplying BigQuery/Pandas memory use.

Required backend environment variables include:

```text
GOOGLE_APPLICATION_CREDENTIALS
LIGHTSPEED_ACCOUNT_ID
LIGHTSPEED_CLIENT_ID
LIGHTSPEED_CLIENT_SECRET
LIGHTSPEED_REFRESH_TOKEN
```

`GOOGLE_APPLICATION_CREDENTIALS` is a *path* to the BigQuery service-account JSON
key. On Render it must be mounted as a Secret File and this variable pointed at
the mount path.

Useful optional backend variables:

```text
APP_DATASET
LS_DATASET
SHOPIFY_DATASET
QUALIFIED_ITEMS_VIEW
LS_PO_SNAPSHOT_TTL_SECONDS
```

### Feature access control

Every page and Ordering tab is a *feature* declared once in
`backend/app/services/access/registry.py`. The Admin page (`/admin`) switches
them on or off at runtime and sets per-user permissions by login email.

A feature that is off is genuinely dormant, not merely hidden:

* the nav entry and tab don't render, so their components never mount and never
  fetch;
* the route renders a short "turned off" panel instead of the page;
* the backend refuses that feature's own endpoints with a 403 before any
  BigQuery or Lightspeed work starts (`access/service.feature_for_path`).

Storage is the same Postgres/SQLite store the PO workflow uses
(`app_feature_flags`, `app_user_access`). A feature with no stored row falls
back to `default_enabled` in the registry, so a fresh or reset database behaves
exactly as the code ships.

**First run.** While no admin exists anywhere — no `APP_ADMIN_EMAILS` and no
stored row with role `admin` — every signed-in user is treated as an admin, and
the Admin page says so in a banner. This is what stops a fresh deployment from
being locked out of its own settings; it is safe because the app already sits
behind Google OAuth restricted to `@bici.cc`, so reaching the page at all means
the visitor is staff. Naming the first admin under **People** ends it.

```text
APP_ADMIN_EMAILS   comma-separated emails that are always admins, whatever the
                   database says. Optional, but setting it pins admin access
                   across a database reset and is the way back in if the last
                   admin is demoted.
```

Other things worth knowing:

* Per-user rules only subdivide features that are globally on. Switching a
  feature off turns it off for everyone, admins included.
* The Admin page itself can never be switched off (`registry.ALWAYS_ON`), and
  its nav link stays visible when the access API is unreachable — hiding it
  would strand the one page that can diagnose the outage. Its endpoints are
  admin-gated server-side, so showing the link grants nothing.
* `DATABASE_URL` must point at Postgres in production. Without it the store
  falls back to a local SQLite file, which on Render is ephemeral — settings
  would revert to the registry defaults on each deploy.

### Google Merchant Center benchmark (price intelligence)

Pulls the market benchmark price and Google's suggested price for products in our
Merchant Center feed, and records them as two reference-only sources alongside
scraped competitors. Off unless `PI_GOOGLE_BENCHMARK_ENABLED` is set.

```text
PI_GOOGLE_BENCHMARK_ENABLED      default false
PI_GOOGLE_MERCHANT_ID            numeric Merchant Center account id
PI_GOOGLE_MERCHANT_CREDENTIALS   path to the Merchant Center SA JSON key, OR the JSON inline
PI_GOOGLE_BENCHMARK_COUNTRY      default CA
PI_GOOGLE_INSIGHTS_ENABLED       default true (the suggested-price report)
```

This uses a **separate** service account from `GOOGLE_APPLICATION_CREDENTIALS` —
the one already registered in Merchant Center, needing at least the Standard role.
It is loaded explicitly rather than through ADC, so the two never conflict. Because
`PI_GOOGLE_MERCHANT_CREDENTIALS` also accepts inline JSON, Render can hold it in an
ordinary environment variable instead of a second Secret File.

`google_benchmark_enabled` is also exposed in the price-intel Admin console, so the
pull can be stopped without a redeploy.

Merchant Center's terms restrict this data to internal use: it can't be resold,
publicly displayed, advertised, or aggregated across businesses.

The frontend requires:

```text
NEXT_PUBLIC_API_URL
```

This must point to the deployed backend URL and must be present when the frontend is built.

## Deployment Checklist

1. Push backend and frontend changes to GitHub.
2. Confirm Render redeploys the affected service.
3. Test the backend directly:

```text
https://bici-reorder-point-dashboard-b.onrender.com/api/replenishment/data?forecast_period=60&safety_days=7&growth_multiplier=1&force_refresh=true
```

4. If data is missing, check:

```text
https://bici-reorder-point-dashboard-b.onrender.com/api/replenishment/debug
```

5. If one item looks wrong, check:

```text
https://bici-reorder-point-dashboard-b.onrender.com/api/replenishment/debug/item/{item_id}
```
