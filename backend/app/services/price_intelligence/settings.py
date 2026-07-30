"""Runtime-editable settings for the price-intel admin console.

Each setting's *default* comes from the existing env-var config; the console
stores an override row in pi_settings (repository.get_settings_overrides) and
consumers read the effective value through get(). Clearing an override (value
None on update) reverts to the env default, so deployments configured purely
via env vars behave exactly as before the console existed.

Reads are cheap: overrides sit behind the repository's standard TTL cache, and
writes invalidate it, so the in-process scheduler / scrape threads see console
changes immediately.
"""
from zoneinfo import ZoneInfo

from . import config, repository

_MASK_SUFFIX_CHARS = 6


def _default_digest_prompt():
    from . import digest  # lazy: digest imports settings at module level
    return digest.DEFAULT_SYSTEM_PROMPT


# kind: bool | int | text | timezone | url. secret values are masked in
# describe() and never echoed back to the browser.
_SPECS = {
    # 1. LLM digest
    "digest_prompt": {"kind": "text", "default": _default_digest_prompt,
                      "max_len": 20000},
    # 2. Nightly run timing
    "schedule_enabled": {"kind": "bool", "default": lambda: config.SCHEDULE_ENABLED},
    "schedule_hour": {"kind": "int", "default": lambda: config.SCHEDULE_HOUR_LOCAL,
                      "min": 0, "max": 23},
    "schedule_minute": {"kind": "int", "default": lambda: config.SCHEDULE_MINUTE_LOCAL,
                        "min": 0, "max": 59},
    "schedule_timezone": {"kind": "timezone",
                          "default": lambda: config.SCHEDULE_TIMEZONE},
    # 3. Match confirmation policy (see config.AUTO_CONFIRM for semantics)
    "auto_confirm": {"kind": "bool", "default": lambda: config.AUTO_CONFIRM},
    # 4. Slack messaging job
    "slack_enabled": {"kind": "bool", "default": lambda: config.SLACK_ENABLED},
    "slack_webhook_url": {"kind": "url", "default": lambda: config.SLACK_WEBHOOK_URL,
                          "secret": True},
    "slack_alerts_webhook_url": {"kind": "url",
                                 "default": lambda: config.SLACK_ALERTS_WEBHOOK_URL,
                                 "secret": True},
    "slack_send_digest": {"kind": "bool", "default": lambda: True},
    "slack_map_pings": {"kind": "bool", "default": lambda: True},
    "slack_health_alerts": {"kind": "bool", "default": lambda: True},
    "slack_max_priority_pings": {"kind": "int", "default": lambda: 15,
                                 "min": 0, "max": 100},
    "slack_max_digest_moves": {"kind": "int", "default": lambda: 15,
                               "min": 0, "max": 100},
    # 5. Google Merchant Center benchmark phase. Toggleable here so the pull can
    # be stopped without a redeploy; the merchant id and credentials stay env-only
    # (they're deployment identity, not an operating preference).
    "google_benchmark_enabled": {"kind": "bool",
                                 "default": lambda: config.GOOGLE_BENCHMARK_ENABLED},
}


def _validate(key: str, spec: dict, value):
    """Returns the canonical value, or raises ValueError with a user-facing
    message. Runs on write, so get() can trust stored overrides."""
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    if kind == "timezone":
        try:
            ZoneInfo(value)
        except Exception:
            raise ValueError(f"{key}: unknown IANA timezone '{value}'")
    elif kind == "url":
        if not value.startswith("https://"):
            raise ValueError(f"{key} must be an https:// URL")
    elif kind == "text":
        if len(value) > spec.get("max_len", 20000):
            raise ValueError(f"{key} is too long (max {spec['max_len']} characters)")
    return value


def get(key: str):
    """Effective value: stored override if present and well-typed, else the
    env-var default."""
    spec = _SPECS[key]
    overrides = repository.get_settings_overrides()
    if key in overrides:
        try:
            return _validate(key, spec, overrides[key])
        except ValueError:
            pass  # stored under an older/looser rule: fall back to the default
    return spec["default"]()


def _mask(value):
    if not value:
        return None
    return f"••••{str(value)[-_MASK_SUFFIX_CHARS:]}"


def describe() -> list:
    """API payload for the console: effective value, default, and whether an
    override exists. Secrets are masked in both fields — the browser only ever
    learns the tail, enough to recognize which webhook is configured."""
    overrides = repository.get_settings_overrides()
    out = []
    for key, spec in _SPECS.items():
        secret = bool(spec.get("secret"))
        value, default = get(key), spec["default"]()
        out.append({
            "key": key,
            "value": _mask(value) if secret else value,
            "default": _mask(default) if secret else default,
            "overridden": key in overrides,
            "secret": secret,
        })
    return out


def update(changes: dict, actor: str = "Dashboard"):
    """Applies a partial update. value None clears the override (revert to env
    default). Validates everything first so a bad key/value rejects the whole
    request instead of half-applying."""
    validated = {}
    for key, value in changes.items():
        if key not in _SPECS:
            raise ValueError(f"Unknown setting '{key}'")
        validated[key] = None if value is None else _validate(key, _SPECS[key], value)
    for key, value in validated.items():
        if value is None:
            repository.delete_setting(key)
        else:
            repository.upsert_setting(key, value, updated_by=actor)
