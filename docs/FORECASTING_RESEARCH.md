# Demand Forecasting & Inventory Planning Research — for BICI

Research compiled June 2026 to inform turning the BICI inventory tool into a master
procurement / demand-planning system for a 3-shop bicycle/outdoor retailer.

**The core business problem this research targets:** BICI is highly seasonal, and
*different categories peak in opposite seasons* (indoor trainers Oct–Feb; nutrition
May–Sep). A forecast must view each SKU through **both its own history and its
category's seasonal shape**, especially for SKUs with thin or no history.

A note on sourcing: most commercial vendors publish marketing pages, not method
papers. Where a claim is marketing fluff with no real method behind it, this doc
says so. The genuinely useful method detail comes from Lokad's technical docs, the
academic literature (Hyndman on hierarchical reconciliation; the new-product
profiling literature), legacy enterprise docs that name the mechanism explicitly
("seasonality groups"), and the existing prototype already in this repo.

---

## 1. Methods used by the leaders

| Tool | Target market / pricing tier | Core method (what's real vs. claimed) | Seasonality | New products | Intermittent / slow movers | Lead-time variability |
|---|---|---|---|---|---|---|
| **Inventory Planner by Sage** | SMB DTC / Shopify, mid-tier SaaS | "Advanced algorithms, *not* AI" by their own admission — essentially configurable smoothing over historical sales with manual adjustment fields. Opaque, low sophistication. | Seasonal vs. year-round mode; recommends *shorter lookback* + manual "adjustment field" for seasonal items — i.e. the planner does the seasonal thinking, not the model. | Manual / like-item assumptions; no documented statistical new-product model. | Not specifically documented. | Factors supply lead time into reorder point; no probabilistic lead-time model documented. |
| **Cogsy** | SMB DTC / Shopify | Demand planning + "ready-to-submit POs", working-capital view. Method opaque (marketing only). Strength is the *workflow/UI*, not the math. | Accounts for seasonality + marketing events; mechanism undisclosed. | "Prepare for new product launches" — manual planning. | Not documented. | Not documented. |
| **NetSuite Demand Planning** | Mid-market ERP module | **Four explicit, classical models**: Moving Average, Linear Regression, **Seasonal Average** (uses last year's seasonal data), and Sales Forecast (CRM/sales input). Transparent but basic. | "Seasonal analysis intervals" configurable per item; Seasonal Average projects last year forward. | No statistical new-product model; relies on Sales Forecast input or manual. | Reorder-point method for stable items; weak for lumpy demand. | Time-Phased vs. Reorder-Point replenishment; lead time is an input, not modeled stochastically. |
| **RELEX** | Enterprise retail/grocery | ML "demand drivers" model at product-location-day; **level-shift detection**; in-memory engine for millions of SKUs. Real ML, but the public docs are benefit-focused, not method-specific. | Auto-detects seasonality/trend **at all relevant product-hierarchy levels** (this is the relevant pattern for BICI). | **Data pooling / multilevel modeling** across channels, product types, locations — explicitly aggregates up the hierarchy to forecast sparse items. | Handled within ML; details opaque. | Probabilistic "uncertainty scenarios". |
| **Blue Ridge** | Mid-market distribution/retail | Marketed as "demand-sensing" + auto model selection. Public method detail thin. | Auto seasonal detection (claimed). | Like-item. | Targets distribution where intermittent demand is common; specific estimator undisclosed. | Lead-time variability factored into safety stock. |
| **GAINSystems (GAINS)** | Enterprise supply chain | Long-standing OR/statistics house; mixes classical statistical forecasting with optimization. Method detail not public. | Classical seasonal models. | Like-item / attribute. | Strong heritage in spare-parts / intermittent (their core market historically). | Explicit lead-time variability modeling for safety stock. |
| **GMDH Streamline** | SMB → mid-market | **Most transparent of the SMB tools.** Decomposition model `(Slope*Time + Level) * Seasonality * Adjustment`; **auto-selects which components apply per item** (Level only / Level×Seasonality / Trend+Level). Separate **intermittent model** computing probability of occurrence, median demand, and log-normal deviation. Auto-trims the fitted window to the "relevant part" of history. | Seasonal coefficients from all non-zero history; recommends **≥24 months** to see seasonality. **No documented group/category seasonal profile** — a gap for opposite-peaking SKUs with short history. | Not a documented strength. | Dedicated intermittent model (prob-of-occurrence + log-normal); sets expected demand to zero but sizes safety stock from the distribution. | Used in safety-stock sizing. |
| **Lokad** | Mid → enterprise, "Supply Chain Scientist" service model | **The deepest published method.** **Probabilistic forecasting** (probability mass over 0,1,2,… units) + **differentiable programming** to fit custom models. Forecasts demand *and* lead times *and* returns as distributions. | **Low-dimensional parametric cyclic models** with the cyclicity **hard-coded, taken as given** (yearly, weekly, monthly paycheck, quasi-yearly events like Easter/Black Friday). Explicitly argues this beats letting ML *discover* seasonality when history is thin — directly relevant to BICI. | Differentiable programming injects **expert priors** to exploit the "law of small numbers"; can fit with single-digit data points. | Probabilistic by construction (the `Ranvar` distribution structure); captures lumpiness a point forecast can't. | Models lead time as a **distribution**, not a constant. |
| **Netstock / Inventoro** | SMB (ERP/QuickBooks/Xero add-on) | AB(C)-XYZ classification driving policy; "several algorithms" auto-chosen; ML weighting of recent trend. Method opaque but pitched as approachable for no-data-science teams. | Auto seasonality + event-based adjustments; flexible hierarchies (forecast at any level). | Like-item. | Picks up intermittent demand and one-off spikes; lost-sales adjustment. | Checks supplier lead-time variability. |

### Honest read of the landscape
- **SMB tools (Inventory Planner, Cogsy, Netstock, Inventoro)** mostly run classical
  smoothing/decomposition behind an "AI" label and win on *workflow and UI*, not math.
  Inventory Planner openly says it is "not AI."
- **Streamline** is the SMB tool whose method is most worth copying because it is
  documented and simple: a decomposition with **per-item automatic component
  selection** — exactly the "is this SKU seasonal or not?" decision BICI needs.
- **Lokad** is the most intellectually honest and the most relevant on the *seasonality*
  question: hard-code the seasonal shape as a prior rather than asking a model to
  rediscover it from one or two noisy years. This is the single most transferable idea
  for BICI.
- **RELEX/GAINS** confirm the enterprise pattern: forecast and pool seasonality **up
  the product hierarchy** so sparse SKUs inherit a category shape.

Sources: [Inventory Planner AI page](https://www.inventory-planner.com/ai-demand-forecasting/),
[Inventory Planner best practices (seasonal)](https://help-essentials.inventory-planner.com/en/articles/9419930-best-practices-for-forecasting),
[Cogsy dashboard](https://cogsy.com/features/inventory-management/actionable-dashboard/),
[NetSuite Demand Planning](https://www.netsuite.com/portal/products/erp/demand-planning.shtml),
[NetSuite replenishment methods](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/article_162497068568.html),
[RELEX ML in retail forecasting](https://www.relexsolutions.com/resources/machine-learning-in-retail-demand-forecasting/),
[RELEX demand planning](https://www.relexsolutions.com/solutions/demand-planning-software/),
[Lokad technology](https://www.lokad.com/technology),
[Lokad demand forecasting FAQ](https://www.lokad.com/demand-forecasting/),
[Lokad probabilistic forecasting](https://www.lokad.com/probabilistic-forecasting/),
[GMDH Streamline statistical forecasting docs](https://gmdhsoftware.com/documentation-sl/statistical-forecasting),
[Netstock forecasting methods](https://www.netstock.com/blog/inventory-forecasting-methods-to-improve-planning-processes/).

---

## 2. Category vs. product-level seasonality (the most important section)

BICI's exact problem — SKUs in one catalog that peak in opposite seasons, many with
< 2 years of clean history — is a *solved pattern* in mature planning systems. It has a
name and a well-trodden mechanism.

### The named mechanism: "seasonality groups" / "seasonal profiles"
Legacy enterprise planners (Oracle Demantra, JDA/Blue Yonder, SAP) implement
**seasonality groups**: a user-defined grouping of items (by product, product group,
market, geography) that produces **one shared seasonal profile** which any member item
can borrow.

The decisive quote for BICI, from a Demantra-style implementation:

> *"If an item is seasonal but doesn't have sufficient history to determine its own
> profile, then a seasonality group profile can be assigned to the item… A key benefit
> of the seasonality group is that it allows seasonal items to be forecast using
> seasonal models with **fewer than 18 months** of demand history. The seasonality
> group profile is **more stable than individual profiles** because the aggregation
> process smoothes out random errors."*
> — [Maintaining Seasonality Groups (PeopleSoft/Demantra docs)](https://psbookswli.mycmsc.com/ODLA/fin84_848/eng/psbooks/sdpl/htm/sdplp06a.htm)

This is **exactly** what BICI needs: an "indoor trainers" group with an Oct–Feb shape
and a "nutrition" group with a May–Sep shape; each new or sparse SKU inherits its
group's shape instead of trying to learn one from noise.

### How the leaders implement the same idea
- **Hierarchical / aggregated seasonality.** RELEX detects seasonality "at all relevant
  product-hierarchy levels" and **pools data** across product types/locations/time to
  forecast long-tail and new items — i.e. estimate the seasonal shape at the *category*
  level where data is dense, apply it at the *SKU* level where data is sparse.
  ([RELEX](https://www.relexsolutions.com/resources/machine-learning-in-retail-demand-forecasting/))
- **Cluster seasonality profiles (Walmart approach).** A SKU is placed into one or more
  clusters and a **cluster-level seasonal profile** is computed and applied to the SKU.
  Reported in the new-product/seasonal forecasting literature and Walmart patents.
  ([SKU-level seasonal forecasting overview](https://stylematrix.io/how-sku-level-forecasting-improves-seasonal-retail-trends-in-fashion/))
- **Linear mixed-effects models.** A patented item-level approach uses **mixed-effects
  models**: a *fixed effect* shared across the group (the category seasonal curve) plus a
  *random effect* per SKU (its own deviation). This is the statistically principled
  version of "borrow the category shape, adjust per SKU."
  ([Mixed-effects item-level forecasting patent](https://patents.justia.com/patent/10373105))
- **Lokad: hard-code the cycle as a prior.** Rather than discover seasonality per SKU,
  Lokad fits a low-dimensional parametric seasonal profile and *takes the cyclicity as
  given*, which is far more robust with few data points. The category profile is the
  natural source of that "given" shape. ([Lokad demand forecasting](https://www.lokad.com/demand-forecasting/))
- **Attribute / like-item profiling for brand-new SKUs.** When a SKU has *zero* history,
  group it by attributes (category, brand, price band, season-tag) and use the most
  similar existing items' shape. Academic instances: **DemandForest** (K-means cluster
  → Random Forest / Quantile Regression Forest on product attributes for pre-launch
  forecasts) and supersession modeling (inherit the replaced item's history).
  ([Forecasting demand profiles of new products](https://www.researchgate.net/publication/346313899_Forecasting_demand_profiles_of_new_products),
  [ToolsGroup on AI for new product introduction](https://www.toolsgroup.com/blog/how-ai-powered-demand-forecasting-transforms-new-product-introductions/))

### Practical recipe for BICI (concrete, implementable now)
1. **Build category-level seasonal indices first**, not SKU-level. Aggregate sales by
   `category × month_of_year` across all available years and compute multiplicative
   indices normalized to mean ≈ 1.0. With opposite-peaking categories, the trainers
   group yields indices like `Nov≈1.8, Jul≈0.3` while nutrition yields `Jul≈1.7,
   Jan≈0.4`. **The aggregation is what makes these stable** even when individual SKUs are
   sparse.
   - The repo's existing `forecasting.seasonality_indices(period_totals)` already
     computes exactly this normalized-to-1.0 index map. Today it's fed a *single global*
     total; the change is to compute **one map per category** instead.
2. **Pick the seasonal source per SKU by a data-sufficiency rule** (the Streamline
   "auto-select components" idea):
   - SKU has **≥ 2 full years** of clean history AND a statistically clear own-seasonal
     signal → use its **own** seasonal indices.
   - Otherwise (most SKUs) → use the **category** seasonal indices.
   - Brand new / no category match → fall back to **attribute group** (brand + price
     band) or **flat (neutral 1.0)**.
3. **Blend, don't hard-switch (shrinkage).** Combine SKU-own and category indices with a
   weight that grows with the SKU's months of history:
   `w = clamp(months_history / 24, 0, 1)`;
   `index = w * sku_index[m] + (1 - w) * category_index[m]`.
   This is the practical, code-friendly form of the mixed-effects "fixed + random
   effect" idea. The existing `seasonality_indices(..., smoothing=...)` shrinkage knob is
   a cruder version of the same instinct.
4. **Apply the chosen index to the forward velocity**, which the repo already supports via
   `apply_forecast_enrichment(base_daily_sales, seasonality_index=...)` and the engine's
   `seasonality_indices_by_period` / `forecast_month` hooks. The missing piece is purely
   *which* index map to pass per row — make it per-category.
5. **Forecast the lead-time window, not just the current month.** A PO placed in August
   for trainers covers Sep–Oct demand. Multiply daily velocity by the **average of the
   indices across the months the order will cover**, not just the order month — otherwise
   you under-buy ahead of a ramp. (See §5 worksheet design.)

---

## 3. Concrete algorithms we could realistically implement (Python / BigQuery)

For a bike shop with thousands of SKUs, most of them slow or seasonal, **simple +
robust + per-item model selection beats one fancy global model.** Below, each method
with when to use it, data needs, and how it handles the category-seasonality problem.

| Method | When it's the right choice | Data needs | Pros | Cons | Category-seasonality fit |
|---|---|---|---|---|---|
| **Seasonal-naive baseline** (this month = same month last year × growth) | Always — as the benchmark every other model must beat | ≥ 12–13 months | Trivial, surprisingly hard to beat for seasonal retail | No trend, no pooling, dies with < 1 yr history | Implicitly per-SKU; use *category* seasonal-naive when SKU history is thin |
| **Multiplicative seasonal indices × base velocity** (what the repo does) | The pragmatic core for BICI right now | Monthly totals; robust with category pooling | Transparent, fast in BigQuery (GROUP BY month), already built, easy to apply category profiles | Indices are static unless recomputed; doesn't model trend itself | **Best fit** — compute indices at category level, apply per SKU (see §2) |
| **Holt-Winters / ETS** (triple exponential smoothing: level+trend+season) | Steadier SKUs with ≥ 2 yrs history and a clear own season | ~24 months for the seasonal term | Captures level+trend+season jointly; in `statsmodels` | Needs ≥ 2 seasonal cycles; one model per SKU; struggles with intermittency | Per-SKU only; for sparse SKUs, *seed the seasonal component from the category profile* |
| **SARIMA** | Few high-value, well-behaved SKUs where squeezing accuracy matters | ≥ 2–3 yrs, low intermittency | Flexible, handles autocorrelation | Heavy to fit/tune per SKU at thousands-of-SKU scale; fragile on lumpy/short data | Poor for sparse SKUs; not recommended broadly for BICI |
| **Croston / SBA / TSB** (already prototyped here) | Slow movers / intermittent demand (lots of zero-sales weeks) — a large share of a bike shop's long tail | Any length; designed for sparsity | Right tool for lumpy demand; cheap; **TSB decays to 0 when an item dies** (good for discontinued/obsolete) | Assumes ~no seasonality itself; SBA de-biases Croston; pick TSB for obsolescence risk, SBA for active items | Combine: use Croston/TSB for the *baseline rate*, then multiply by the **category** seasonal index for shape |
| **Prophet** | SKUs/categories with multiple seasonalities + holiday effects, when you want decomposition with little tuning | ≥ 1–2 yrs ideally | Easy, handles holidays/changepoints, decomposable, robust to gaps | Can over/under-fit thin series; per-SKU at scale is slow; weaker than tuned ETS on clean seasonal data | Fit Prophet at **category level**, export its seasonal component as the profile |
| **Gradient-boosted trees** (LightGBM/XGBoost) — single **global** model with features | Phase 3, once you have clean multi-year data and want one model across all SKUs | Lots of rows; engineered features | One model learns cross-SKU patterns; naturally does pooling; handles many drivers (price, promo, weather) | Needs feature engineering + ML ops; less interpretable; overkill early | **Excellent** — encode `category`, `month`, `category×month` as features and the model learns category seasonality + per-SKU deviation automatically (the ML version of mixed effects) |
| **Hierarchical reconciliation (bottom-up / MinT)** | When you want SKU forecasts to **sum coherently** to category and store totals (for OTB budgets) | Forecasts at each level + history for covariance (MinT) | Coherent numbers across the hierarchy; improves accuracy at sparse leaves by borrowing strength | MinT needs a residual covariance estimate; added complexity | Directly addresses category↔SKU: forecast where data is dense, reconcile down. **Bottom-up** is the simple start; **MinT** later. ([Hyndman MinT paper](https://robjhyndman.com/papers/mint.pdf), [Hierarchical forecasting review](https://robjhyndman.com/files/prato/Hierarchical%20Forecasting%20Review.pdf)) |

### Recommended phased build
- **Phase 1 (now) — category seasonal profiles + intermittent baseline.** This is the
  highest-ROI, lowest-risk step and directly fixes the opposite-peaking problem:
  1. BigQuery: aggregate `category × month_of_year × year` units.
  2. Compute **category-level** multiplicative indices (reuse `seasonality_indices`).
  3. Per-SKU history-weighted blend of SKU-own vs. category index (§2 step 3).
  4. For slow movers, compute baseline rate via **TSB** (already in repo), then apply
     the category index.
  5. Apply across the **lead-time window**, not just the current month.
  6. Keep it all opt-in/additive, exactly as the current prototype is structured.
- **Phase 2 — model selection + Holt-Winters/ETS for the steady, high-value SKUs**, with
  the seasonal component seeded from the category profile for thin SKUs. Add a
  seasonal-naive and current-engine benchmark and only switch a SKU to ETS if it wins on
  backtest.
- **Phase 3 — global gradient-boosted model** with `category`, `month`, `category×month`,
  price, promo, and (optionally) weather features; this learns category seasonality and
  per-SKU deviation in one model and supersedes the hand-built indices if it backtests
  better. Add **hierarchical reconciliation (bottom-up, then MinT)** so SKU forecasts
  roll up coherently to category/store totals for OTB budgeting.

Sources: [GMDH decomposition & auto component selection](https://gmdhsoftware.com/documentation-sl/statistical-forecasting),
[Croston vs SBA vs TSB comparison](https://tejark.medium.com/the-croston-method-a-smarter-way-to-forecast-intermittent-demand-dd33b20b207a),
[Microsoft Learn: Croston's method](https://learn.microsoft.com/en-us/dynamics365/supply-chain/demand-planning/croston-method),
[Lokad parametric seasonality](https://www.lokad.com/demand-forecasting/),
[MinT reconciliation (Hyndman)](https://robjhyndman.com/papers/mint.pdf).

---

## 4. Accuracy metrics & safety stock (brief)

### Metrics — use these, in this order
- **WMAPE / WAPE** (volume-weighted absolute % error) — the supply-chain standard.
  Critically, it **doesn't blow up on zero-sales periods**, unlike plain MAPE, so it's
  the right primary metric for a bike shop full of intermittent SKUs.
- **MASE** (mean absolute scaled error) — scale-free; error relative to a seasonal-naive
  benchmark. **< 1 means you beat naive.** Best for comparing across SKUs of different
  volume and across categories. Use as the model-selection criterion.
- **Bias / tracking signal** — cumulative signed error ÷ MAD. Detects *systematic*
  over- or under-forecasting (a planner's most expensive failure). Watch this per
  category, since a wrong seasonal profile shows up as seasonal bias.
- Avoid plain **MAPE** as a primary metric (undefined on zeros, asymmetric).

### Backtesting
- Use **rolling-origin / time-series cross-validation** (walk-forward), multiple
  backtest windows, never random k-fold (leaks future into past). Average metrics across
  windows. Always report against the **seasonal-naive baseline** — if the fancy model
  doesn't beat seasonal-naive on WMAPE/MASE, don't ship it. The repo's notes already
  gate Phase 2 on "backtest vs. current engine (MAPE)"; upgrade that to WMAPE + MASE.

### Safety stock — move beyond a flat safety-days buffer
- A flat "X days of cover" buffer **over-stocks steady SKUs and under-stocks volatile
  ones**. The standard improvement is **service-level (quantile) driven safety stock**:
  size the buffer from the *variability* of demand and lead time and the *target service
  level*.
- Classic formula: `SS = z(service_level) × sqrt(LT × σ_demand² + demand² × σ_LT²)` —
  captures **both** demand variability and **lead-time variability** (which most simple
  tools ignore). Pick `z` from the desired in-stock probability (e.g. 95% → z≈1.65).
- **Quantile / probabilistic forecasting** (Lokad's whole pitch; also Amazon Forecast's
  wQL metric) does this directly: forecast the demand distribution and stock to the
  quantile where the cost of a stockout = the cost of overstock. For BICI, set a
  **higher service level for A-items and short-shelf-life nutrition**, lower for slow C
  movers — i.e. service level varies by ABC-XYZ class, not one global number.

Sources: [Amazon Forecast accuracy metrics (WAPE/MASE/wQL)](https://aws.amazon.com/blogs/machine-learning/measuring-forecast-model-accuracy-to-optimize-your-business-objectives-with-amazon-forecast/),
[MAPE vs WAPE vs WMAPE](https://www.baeldung.com/cs/mape-vs-wape-vs-wmape),
[MAPE, WMAPE & forecast bias in demand planning](https://demandplanning.net/mape-wmape-and-forecast-bias/),
[ABC-XYZ classification for inventory](https://ijrpr.com/uploads/V5ISSUE12/IJRPR36812.pdf).

---

## 5. UI patterns worth emulating (feeds the parallel UI effort)

What "turning forecasts into POs" looks like in the good tools:

- **Replenishment worksheet / requisition worksheet** (Business Central, Streamline,
  Netstock). One grid: each row a SKU needing action, with **suggested order qty,
  reorder point, lead time, on-hand, on-order, and the action message** ("create PO",
  "increase qty"). The planner reviews/edits suggestions, then bulk-converts to POs.
  This is the workhorse screen — emulate it.
  ([Business Central requisition worksheet](https://docs.wiise.com/use-requisition-worksheet),
  [BC planning functionality](https://learn.microsoft.com/en-us/dynamics365/business-central/production-about-planning-functionality))
- **"Replenish Now / Replenish Soon" buckets + ready-to-submit POs** (Cogsy). At-a-glance
  triage of what's urgent vs. upcoming, with one-click PO generation. Reduces the
  worksheet to a prioritized to-do list. ([Cogsy dashboard](https://help.cogsy.com/article/iw58ajr2mb-dashboard-overview))
- **Supplier / vendor grouping.** POs are organized by vendor with **MOQ, case-pack, and
  lead-time** constraints applied so the suggested order is actually placeable. Group the
  worksheet by vendor → one PO per vendor. (Essential given BICI's brand-sourcing rules,
  which the repo already models.)
- **Open-to-Buy (OTB) budget view** (retail-merchandising standard). Plan spend by
  category × month against a budget; the system shows **remaining OTB** so buyers don't
  over-commit working capital. **This pairs directly with category-level seasonal
  forecasts** — the seasonal index *is* the curve the OTB budget should follow
  (more trainer budget Oct–Feb, more nutrition budget May–Sep). Cogsy's
  "month-by-month working-capital snapshot" is a lightweight version of this.
- **Stock-health heatmap / 12-month coverage view** (Cogsy). Color-coded weeks-of-cover
  forward in time, surfacing future stockouts *before* they happen — especially valuable
  for seasonal ramps. ([Cogsy actionable dashboard](https://cogsy.com/features/inventory-management/actionable-dashboard/))
- **Scenario planning / what-if** (RELEX "uncertainty scenarios", Cogsy growth goals).
  Adjust a growth assumption or a seasonal-peak multiplier and see the PO plan and cash
  requirement update. Worth a simple version: a global/category demand multiplier slider
  on the worksheet.

**Features most worth emulating for BICI, in order:** (1) vendor-grouped replenishment
worksheet with editable suggested-order column → bulk PO; (2) forward weeks-of-cover
heatmap to catch seasonal stockouts; (3) category × month OTB budget driven by the
seasonal indices; (4) lightweight scenario multiplier.

---

## 6. Recommended approach for BICI

### Guiding principle
Every SKU is forecast through **two lenses simultaneously**: its **own history** (where
sufficient) and its **category's seasonal shape** (always available, always stable).
The category lens is what solves the opposite-peaking problem; the SKU lens captures
each item's individual level and trend. Blend them by how much history the SKU has.

### Phase 1 — Category seasonal profiles + robust baseline (build now)
Highest ROI, lowest risk, and reuses what's already in the repo
(`forecasting.seasonality_indices`, `crostons_method`/`tsb_method`, the engine's opt-in
hooks). Concrete steps:
1. **BigQuery aggregation**: produce `category × month_of_year × year → units`.
   (Generalize the existing `fetch_monthly_sales_history` to also group by category.)
2. **One seasonal index map per category** via `seasonality_indices` (mean ≈ 1.0). Each
   category gets its own peak — trainers Oct–Feb, nutrition May–Sep — automatically.
3. **Per-SKU seasonal source selection**:
   - ≥ 2 yrs clean history + clear own-seasonality → SKU-own indices;
   - else → category indices;
   - new SKU, no category match → attribute group (brand/price band) or neutral.
4. **History-weighted blend** of SKU-own and category indices:
   `w = clamp(months_history/24, 0, 1)`; blended index per month.
5. **Slow movers**: baseline daily rate from **TSB** (already coded), then multiply by
   the blended seasonal index.
6. **Lead-time-window application**: apply the *average index over the months the PO will
   cover*, so you buy *ahead of* the ramp (the trainers Aug-PO covers the Oct peak).
7. Keep everything **additive / opt-in** (the current design), and **backtest with
   WMAPE + MASE vs. seasonal-naive and the current engine** before enabling by default.

This alone gives BICI correct directional seasonality for every category and stops the
"average-across-the-year" error that wrecks opposite-peaking catalogs.

### Phase 2 — Per-item model selection + service-level safety stock
1. Add **Holt-Winters/ETS** for steady, high-value SKUs with ≥ 2 yrs; for thinner SKUs,
   **seed the seasonal component from the category profile**.
2. **Auto-select per SKU** (Streamline-style): flat / seasonal-only / trend+seasonal /
   intermittent — pick by backtest, default to the Phase-1 category-index method.
3. Replace flat safety-days with **service-level safety stock** including **lead-time
   variability**, with the target service level set by **ABC-XYZ class** (higher for
   A-items and perishable nutrition).
4. Add the **vendor-grouped replenishment worksheet** and **forward coverage heatmap** UI.

### Phase 3 — Global ML + hierarchical coherence + OTB
1. Train a **single global LightGBM** with `category`, `month`, `category×month`, price,
   promo, weather features — it learns category seasonality + per-SKU deviation in one
   model (the ML form of mixed effects) and replaces hand-built indices *only if it
   backtests better*.
2. Add **hierarchical reconciliation** (bottom-up first, then MinT) so SKU forecasts sum
   coherently to category/store totals.
3. Build the **category × month Open-to-Buy budget**, driven by the seasonal curves, with
   scenario multipliers.

### What to deliberately skip early
- SARIMA per SKU (too fragile/heavy at this scale).
- Full probabilistic/differentiable-programming forecasting (Lokad-grade) — borrow the
  *idea* (category seasonal shape as a prior; quantile-based safety stock) without the
  machinery.
- Treating "AI" as a goal. The transparent decomposition + category profiles will
  out-perform a black box on BICI's data volume and is far easier to trust and debug.

---

### One-line summary
**Build category-level multiplicative seasonal profiles, blend them into each SKU by how
much history it has, apply them across the lead-time window, and size safety stock by
service level — this is the named, proven "seasonality groups" pattern and it directly
fixes BICI's opposite-peaking-categories problem with code that's already 80% present in
the repo.**
