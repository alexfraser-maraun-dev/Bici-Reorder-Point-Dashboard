"""Resolves what a given user is allowed to see.

Two layers, in order:

1. **Global flags** — is the feature switched on at all? A feature that is off is
   off for everyone, admins included. That is what makes "off" mean dormant:
   nothing renders it, and the API middleware refuses its endpoints, so it costs
   no BigQuery, no Lightspeed calls, and no scheduler work.
2. **Per-user overrides** — within the features that are on, an admin can grant
   or revoke individual surfaces per login email.

Admins always keep the Admin page (registry.ALWAYS_ON), so access can never be
configured into a dead end. APP_ADMIN_EMAILS is the second escape hatch: those
emails are admins no matter what the database says.
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import registry

_CACHE_TTL_SECONDS = 30.0
_lock = threading.Lock()
_cache: Dict[str, Any] = {"expires_at": 0.0, "flags": None, "users": None}

ROLES = ("admin", "member")
DEFAULT_ROLE = "member"


def bootstrap_admins() -> set:
    """Emails that are admins regardless of stored config — the way in on a fresh
    database, and the way back in if someone demotes the last admin."""
    raw = os.getenv("APP_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def bootstrap_mode() -> bool:
    """True when no admin exists anywhere — no APP_ADMIN_EMAILS and no stored row
    with role 'admin'.

    In that state every signed-in user is treated as an admin, so a fresh
    deployment is never locked out of its own settings. This is safe because the
    whole app already sits behind Google OAuth restricted to the company domain:
    reaching here at all means the visitor is staff. The Admin page says loudly
    that it is in this state, and naming the first admin ends it.
    """
    if bootstrap_admins():
        return False
    return not any(
        (u.get("role") or "").lower() == "admin" for u in _load()["users"].values()
    )


def _store():
    from ..planning_store import get_planning_store
    return get_planning_store()


def _load() -> Dict[str, Any]:
    with _lock:
        if _cache["flags"] is not None and time.monotonic() < _cache["expires_at"]:
            return {"flags": _cache["flags"], "users": _cache["users"]}
    store = _store()
    flags = dict(registry.defaults())
    flags.update(store.list_feature_flags())
    for key in registry.ALWAYS_ON:
        flags[key] = True
    users = {u["email"].lower(): u for u in store.list_user_access()}
    with _lock:
        _cache.update({"flags": flags, "users": users,
                       "expires_at": time.monotonic() + _CACHE_TTL_SECONDS})
    return {"flags": flags, "users": users}


def invalidate() -> None:
    with _lock:
        _cache.update({"expires_at": 0.0, "flags": None, "users": None})


def global_flags() -> Dict[str, bool]:
    """Effective on/off per feature, ignoring any per-user rules."""
    return dict(_load()["flags"])


def is_feature_enabled(feature_key: str) -> bool:
    return bool(_load()["flags"].get(feature_key, True))


def _user_record(email: Optional[str]) -> Dict[str, Any]:
    if not email:
        return {}
    return _load()["users"].get(email.lower(), {})


def role_for(email: Optional[str]) -> str:
    if not email:
        return DEFAULT_ROLE
    if email.lower() in bootstrap_admins():
        return "admin"
    role = (_user_record(email).get("role") or DEFAULT_ROLE).lower()
    if role not in ROLES:
        role = DEFAULT_ROLE
    # First run: nobody is an admin yet, so everyone is, until one is named.
    if role != "admin" and bootstrap_mode():
        return "admin"
    return role


def is_admin(email: Optional[str]) -> bool:
    return role_for(email) == "admin"


def resolve(email: Optional[str]) -> Dict[str, Any]:
    """Everything the frontend needs to render the right shell for this user."""
    data = _load()
    flags = data["flags"]
    admin = is_admin(email)
    overrides = _user_record(email).get("overrides") or {}

    features: Dict[str, bool] = {}
    # Pages before tabs, so a tab can read its parent's resolved value below.
    ordered = sorted(registry.FEATURES, key=lambda f: f.parent is not None)
    for feature in ordered:
        enabled = bool(flags.get(feature.key, True))
        if feature.key in registry.ALWAYS_ON:
            enabled = True
        # A switched-off feature stays off for everyone; per-user rules only
        # subdivide what is already on.
        if enabled and feature.key in overrides:
            enabled = bool(overrides[feature.key])
        if feature.admin_only and not admin:
            enabled = False
        # A tab is only reachable when its page is.
        if enabled and feature.parent:
            enabled = features.get(feature.parent, True)
        features[feature.key] = enabled

    return {
        "email": email,
        "role": "admin" if admin else role_for(email),
        "is_admin": admin,
        "bootstrap_mode": bootstrap_mode(),
        "features": features,
        "default_ordering_tab": registry.DEFAULT_ORDERING_TAB,
    }


def can_access(email: Optional[str], feature_key: str) -> bool:
    return bool(resolve(email)["features"].get(feature_key, True))


def feature_for_path(path: str) -> Optional[str]:
    """The feature that exclusively owns this API path, if any."""
    for prefix, key in registry.prefix_owners():
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


# ---------------------------------------------------------------------------
# Admin console operations
# ---------------------------------------------------------------------------

def describe_features() -> List[Dict[str, Any]]:
    stored = _store().list_feature_flags()
    flags = global_flags()
    out = []
    for feature in registry.FEATURES:
        row = feature.to_dict()
        row["enabled"] = bool(flags.get(feature.key, True))
        row["customized"] = feature.key in stored
        out.append(row)
    return out


def set_features(changes: Dict[str, Any], actor: str = "Dashboard") -> None:
    """Applies a partial update. A value of None clears the override so the
    feature reverts to its registry default. Validates everything before writing
    so a bad key rejects the whole request instead of half-applying."""
    validated: Dict[str, Optional[bool]] = {}
    for key, value in changes.items():
        if key not in registry.BY_KEY:
            raise ValueError(f"Unknown feature '{key}'")
        if key in registry.ALWAYS_ON:
            raise ValueError(f"'{key}' cannot be switched off")
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{key} must be true, false, or null")
        validated[key] = value
    store = _store()
    for key, value in validated.items():
        if value is None:
            store.clear_feature_flag(key)
        else:
            store.set_feature_flag(key, value, updated_by=actor)
    invalidate()


def list_users() -> List[Dict[str, Any]]:
    bootstrap = bootstrap_admins()
    users = {u["email"].lower(): dict(u) for u in _store().list_user_access()}
    for email in bootstrap:
        record = users.setdefault(email, {"email": email, "overrides": {}, "updated_by": "APP_ADMIN_EMAILS"})
        record["role"] = "admin"
        record["locked"] = True  # pinned by env; the console can't demote them
    for record in users.values():
        record.setdefault("locked", False)
        record.setdefault("overrides", {})
        record.setdefault("role", DEFAULT_ROLE)
    return sorted(users.values(), key=lambda r: r["email"])


def save_user(email: str, role: str, overrides: Optional[Dict[str, Any]] = None,
              actor: str = "Dashboard") -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValueError("A valid login email is required")
    role = (role or DEFAULT_ROLE).lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    clean: Dict[str, bool] = {}
    for key, value in (overrides or {}).items():
        if key not in registry.BY_KEY:
            raise ValueError(f"Unknown feature '{key}'")
        if value is None:
            continue  # no override: inherit the global flag
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true, false, or null")
        clean[key] = value
    record = _store().upsert_user_access(email, role, clean, updated_by=actor)
    invalidate()
    return record


def delete_user(email: str) -> None:
    """Removes the user's row; they fall back to the default role and the global
    flags. Bootstrap admins stay admins — that's the point of the env var."""
    _store().delete_user_access((email or "").strip().lower())
    invalidate()
