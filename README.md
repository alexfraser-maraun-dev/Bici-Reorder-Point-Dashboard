# SKU Reorder Point & Desired Inventory Automation

This app acts as the internal control panel to calculate, review, and push location-specific reorder points and desired inventory levels to Lightspeed R-Series.

## Architecture

Based on technical validation, the system uses the following architecture:

1. **BigQuery (Data Engine)**: Source of truth for sales history (trailing units sold), inventory snapshots (on hand), open POs (on order), days out of stock, and lead times.
2. **PostgreSQL (App Database)**: Stores managed SKUs, location-specific policies (trailing days, forecast days, overrides), recommendation runs, and writeback audit logs.
3. **Python / FastAPI (Backend)**:
   - Fetches summarized data from BigQuery.
   - Computes daily sales, safety stock, and reorder metrics.
   - Pushes approved updates to Lightspeed via the `ItemShop` API.
4. **React / Next.js (Frontend)**: Internal control panel for the team to review recommendations, filter by location/vendor, and manually trigger writebacks.
5. **Render**: Hosting platform for both backend and frontend.

## MVP Scope (V1)
- Import scope of ~1000 SKUs.
- Run monthly calculation reading from BigQuery.
- Display current vs recommended values.
- Calculate metrics adjusting for "days out of stock".
- Allow manual review and push to Lightspeed.
- No automatic writebacks yet.
