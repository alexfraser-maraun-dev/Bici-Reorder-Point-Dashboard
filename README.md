# SKU Reorder Point & Desired Inventory Automation

Internal control panel for reviewing and pushing location-specific reorder points and desired inventory levels to Lightspeed R-Series.

## Current Architecture

1. **BigQuery is the data engine.**
   - Qualifying products come from `bici-klaviyo-datasync.BiciReorderPointDashboard.replen_qualified_items`.
   - Core product, inventory, sales, and PO facts come from `bici-klaviyo-datasync.light_speed_retailne.v_master_snapshot_latest`.
   - The app uses the latest available `snapshot_date_local` from the master snapshot view.
   - Stockout days are still calculated from `item_shop_history` because the snapshot view does not currently expose trailing out-of-stock day counts.

2. **FastAPI backend calculates recommendations.**
   - Reads qualified `auto-replen` items from BigQuery.
   - Limits rows to shop IDs `2`, `3`, and `20`: Victoria, Bici Adanac, and Langford.
   - Calculates stockout-adjusted weighted velocity, safety stock, reorder points, desired levels, and suggested order quantity.
   - Pushes approved values back to Lightspeed using the `ItemShop` API.

3. **Next.js frontend is the review surface.**
   - Displays raw and adjusted 30d/60d demand in the same columns.
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
2  = Victoria
3  = Bici Adanac
20 = Langford
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

### 30d / 60d

Each dashboard cell shows two numbers:

- Main number: raw units sold from the snapshot view.
- Smaller number underneath: stockout-adjusted demand.

Raw sales fields:

```text
v_master_snapshot_latest.sales_units_l30d
v_master_snapshot_latest.sales_units_l60d
```

Adjusted demand is calculated as:

```text
raw units sold / in-stock days * period length
```

For example:

```text
30d adjusted demand = sales_units_l30d / max(1, 30 - days_out_of_stock_30) * 30
60d adjusted demand = sales_units_l60d / max(1, 60 - days_out_of_stock_60) * 60
```

The weighted velocity below is used as the base daily velocity for replenishment math.

### Weighted Velocity

The replenishment math uses a weighted blend of the most recent 30 days and days 31-60:

```text
weighted velocity = (adjusted 30d daily velocity * recent 30d weight)
                  + (adjusted days 31-60 daily velocity * prior 30d weight)
```

The dashboard defaults to a balanced `70% / 30%` split, with UI presets for stable, balanced, reactive, and custom weighting.

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
max(0, recommended desired level - (QOH + QOO))
```

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

Useful optional backend variables:

```text
APP_DATASET
LS_DATASET
QUALIFIED_ITEMS_VIEW
```

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
