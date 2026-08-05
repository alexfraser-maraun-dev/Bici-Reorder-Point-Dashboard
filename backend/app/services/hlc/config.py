import os


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Master switch. po_watch_service only imports this package when the flag is on,
# so a disabled deployment never loads it and the PO tracker behaves exactly as
# it did before HLC tracking existed.
HLC_ENABLED = _flag("HLC_ENABLED")

# Sent as the header `Authorization: ApiKey <key>` (HLC's scheme — not Bearer).
API_KEY = os.getenv("HLC_API_KEY", "")

# Canada runs on the v3.0 "legacy" API. The v4.x endpoints (notably
# /Orders/Packages) 404 here, so don't reach for them.
BASE_URL = os.getenv("HLC_BASE_URL", "https://api.hlc.bike/ca/v3.0").rstrip("/")

# Language header HLC requires on every request.
LANGUAGE = os.getenv("HLC_LANGUAGE", "en")

# How far back to walk /Orders when building the PoNumber -> OrderNumber map.
# Cost scales hard with this: 7 days measured ~2s/77 orders, 45 days ~12.5s/452
# orders. Tracking only matters for POs still in flight, so 60 days is generous.
LOOKBACK_DAYS = int(os.getenv("HLC_LOOKBACK_DAYS", "60"))

# HLC refreshes tracking data every 15 minutes, so polling faster just burns time.
CACHE_TTL_SECONDS = int(os.getenv("HLC_CACHE_TTL_SECONDS", "900"))

# The 45-day /Orders call measured 12.5s, so the default read timeout is well
# above the Lightspeed client's 15s.
TIMEOUT_SECONDS = float(os.getenv("HLC_TIMEOUT_SECONDS", "30"))

# Only stocking POs carry a Lightspeed order id in PoNumber. "Fulfillment" orders
# are dropship and use '#'-prefixed Shopify-style numbers, so they're excluded.
TRACKED_ORDER_TYPES = {"Season", "Booking"}
