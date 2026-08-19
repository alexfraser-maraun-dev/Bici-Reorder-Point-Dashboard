"""The single declaration of every gateable surface in the app.

Adding a feature means adding one entry here. The Admin page renders straight
from this list, the frontend hides nav entries and tabs by `key`, and the API
middleware refuses requests whose path starts with one of `api_prefixes` while
the feature is off.

`default_enabled` is what a deployment gets before anyone touches the Admin
page; an admin toggle stores an override in app_feature_flags, and clearing it
comes back here.
"""
from typing import Any, Dict, List, Optional

# Surfaces the user can never lock themselves out of. The Admin page itself is
# in here: switching it off would leave no way to switch anything back on.
ALWAYS_ON = {"admin"}


class Feature:
    def __init__(
        self,
        key: str,
        label: str,
        description: str,
        group: str,
        kind: str,
        default_enabled: bool = True,
        parent: Optional[str] = None,
        api_prefixes: Optional[List[str]] = None,
        admin_only: bool = False,
    ):
        self.key = key
        self.label = label
        self.description = description
        self.group = group
        # "page" = a top-level route + nav entry; "tab" = a tab inside a page.
        self.kind = kind
        self.default_enabled = default_enabled
        self.parent = parent
        # API paths owned exclusively by this feature. A request whose path starts
        # with any of these is refused while the feature is off. Only list paths no
        # other feature calls — a shared endpoint belongs to neither.
        self.api_prefixes = api_prefixes or []
        self.admin_only = admin_only

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "group": self.group,
            "kind": self.kind,
            "default_enabled": self.default_enabled,
            "parent": self.parent,
            "always_on": self.key in ALWAYS_ON,
            "admin_only": self.admin_only,
        }


FEATURES: List[Feature] = [
    # --- Pages (top nav) -----------------------------------------------------
    Feature(
        key="ordering",
        label="Ordering",
        description="The procurement cockpit at /. Turning this off hides the page and every tab inside it.",
        group="Pages",
        kind="page",
    ),
    Feature(
        key="special_orders",
        label="Special Orders",
        description="Customer special-order triage, Shopify matching, and workorder linkage.",
        group="Pages",
        kind="page",
        api_prefixes=["/api/special-orders"],
    ),
    Feature(
        key="price_intel",
        label="Price Intel",
        description="Competitor price tracking. Also requires PRICE_INTEL_ENABLED on the backend; "
                    "with that env var off the code is never imported at all.",
        group="Pages",
        kind="page",
        api_prefixes=["/api/price-intel"],
    ),
    Feature(
        key="admin",
        label="Admin",
        description="This page. Always available to admins so access can never be locked out.",
        group="Pages",
        kind="page",
        admin_only=True,
    ),

    # --- Ordering tabs -------------------------------------------------------
    Feature(
        key="ordering.inventory",
        label="Inventory Dashboard",
        description="The legacy reorder-point engine. Heavy BigQuery/Pandas workload; retired but kept.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        default_enabled=False,
        api_prefixes=[
            "/api/replenishment/data",
            "/api/replenishment/runs",
            "/api/replenishment/logs",
            "/api/skus",
        ],
    ),
    Feature(
        key="ordering.demand",
        label="Demand & Seasonality",
        description="Seasonal profiles, demand history, and coverage charts.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        default_enabled=False,
        api_prefixes=["/api/forecast"],
    ),
    Feature(
        key="ordering.purchase_orders",
        label="Purchase Orders",
        description="PO drafting, preview, and push to Lightspeed.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        default_enabled=False,
        api_prefixes=["/api/po/drafts", "/api/po/open-orders", "/api/health/lightspeed-po"],
    ),
    Feature(
        key="ordering.po_tracker",
        label="PO Tracker",
        description="Late-PO triage against expected arrival dates. The default landing tab.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        api_prefixes=["/api/po/watch"],
    ),
    Feature(
        key="ordering.vendors",
        label="Vendor Lead Times",
        description="Observed vendor lead times used by the reorder maths.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        api_prefixes=["/api/replenishment/active-vendor-lead-times"],
    ),
    Feature(
        key="ordering.brands",
        label="Brand Configuration",
        description="Brand-to-vendor sourcing rules.",
        group="Ordering tabs",
        kind="tab",
        parent="ordering",
        default_enabled=False,
        api_prefixes=["/api/replenishment/brand-sourcing-rules"],
    ),
]

BY_KEY: Dict[str, Feature] = {f.key: f for f in FEATURES}

# The tab the Ordering page opens on, and the app's landing surface. Falls back
# to whatever tab is enabled if this one is switched off.
DEFAULT_ORDERING_TAB = "ordering.po_tracker"


def defaults() -> Dict[str, bool]:
    return {f.key: f.default_enabled for f in FEATURES}


def prefix_owners() -> List[tuple]:
    """(api_prefix, feature_key) pairs, longest prefix first so the most specific
    owner wins when two prefixes nest."""
    pairs = [(p, f.key) for f in FEATURES for p in f.api_prefixes]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)
