"""Typed public contracts for forecast-to-purchase-order lineage."""

from typing import Any, Dict, List, Optional, Protocol, TypedDict


class ForecastMetrics(TypedDict, total=False):
    wape: float
    mase: float
    bias: float
    quantile_loss_p90: float
    inventory_cost: float


class ForecastPoint(TypedDict):
    week_start: str
    p50: float
    p80: float
    p90: float
    p95: float
    forecast_cogs: Optional[float]
    forecast_revenue: Optional[float]


class ForecastRun(TypedDict, total=False):
    run_id: str
    model_version: str
    assumption_version: str
    source_snapshot_at: str
    as_of_date: str
    horizon_weeks: int
    scope_type: str
    scope_value: Optional[str]
    config: Dict[str, Any]
    recommendations: List[Dict[str, Any]]


class PurchaseRecommendation(TypedDict, total=False):
    recommendation_id: str
    run_id: str
    model_version: str
    assumption_version: str
    source_snapshot_at: str
    item_id: str
    sku: Optional[str]
    description: Optional[str]
    brand: Optional[str]
    category_top_level: Optional[str]
    location_id: str
    vendor_id: Optional[str]
    forecast: List[ForecastPoint]
    recommended_quantity: int
    purchase_commitment_spend: Optional[float]
    blocked: bool


class PurchaseOrderSnapshot(TypedDict, total=False):
    order_id: str
    vendor_id: str
    shop_id: str
    state: str
    ordered_date: Optional[str]
    lines: List[Dict[str, Any]]


class PODraftLine(TypedDict, total=False):
    line_id: str
    recommendation_id: Optional[str]
    sku: Optional[str]
    description: Optional[str]
    brand: Optional[str]
    category_top_level: Optional[str]
    item_id: str
    location_id: str
    quantity: int
    landed_cost: Optional[float]
    need_by_week: Optional[str]


class PODraft(TypedDict, total=False):
    draft_id: str
    version: int
    status: str
    run_id: Optional[str]
    model_version: Optional[str]
    source_snapshot_at: Optional[str]
    lines: List[PODraftLine]


class PlannerOverride(TypedDict, total=False):
    override_id: str
    scope_type: str
    scope_id: str
    location_id: Optional[str]
    week_start: Optional[str]
    measure: str
    original_value: Optional[float]
    override_value: float
    reason: str
    created_by: str
    expires_at: Optional[str]


class OTBRecord(TypedDict):
    category: str
    location_id: str
    month: str
    currency: str
    measure: str
    budget: float
    committed_spend: float
    provider_reference: str


class OTBProvider(Protocol):
    def get_open_to_buy(self, start_month: str, end_month: str) -> List[OTBRecord]: ...
