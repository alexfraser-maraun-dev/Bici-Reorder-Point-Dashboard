"""Admin API for feature visibility and per-user permissions.

Every endpoint here is admin-gated. The caller's identity arrives as the
X-User-Email header, set server-side by the Next.js proxy from the NextAuth
session — the browser never supplies it, and the shared-secret middleware in
main.py means only that proxy can reach this API at all.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from . import registry, service

router = APIRouter(prefix="/api/admin", tags=["admin"])

USER_EMAIL_HEADER = "x-user-email"


def caller_email(request: Request) -> Optional[str]:
    value = (request.headers.get(USER_EMAIL_HEADER) or "").strip()
    return value.lower() or None


def require_admin(request: Request) -> str:
    email = caller_email(request)
    if not service.is_admin(email):
        raise HTTPException(status_code=403, detail="Admin access required")
    return email or "Dashboard"


@router.get("/access")
def get_access(request: Request):
    """What the signed-in user may see. Called once per page load by the shell,
    so it stays cheap: a cached read of two small tables, no BigQuery."""
    return {"status": "success", "data": service.resolve(caller_email(request))}


@router.get("/features")
def get_features(request: Request):
    require_admin(request)
    return {
        "status": "success",
        "data": {
            "features": service.describe_features(),
            "default_ordering_tab": registry.DEFAULT_ORDERING_TAB,
        },
    }


@router.put("/features")
def put_features(request: Request, payload: Dict[str, Any]):
    """Partial update, {feature_key: bool | null}. null clears the override."""
    actor = require_admin(request)
    changes = payload.get("features")
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=400, detail="features must be a non-empty object")
    try:
        service.set_features(changes, actor=actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": {"features": service.describe_features()}}


@router.get("/users")
def get_users(request: Request):
    require_admin(request)
    return {
        "status": "success",
        "data": {"users": service.list_users(), "roles": list(service.ROLES)},
    }


@router.put("/users")
def put_user(request: Request, payload: Dict[str, Any]):
    actor = require_admin(request)
    try:
        record = service.save_user(
            payload.get("email"),
            payload.get("role"),
            payload.get("overrides"),
            actor=actor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": {"user": record, "users": service.list_users()}}


@router.delete("/users/{email}")
def remove_user(request: Request, email: str):
    actor = require_admin(request)
    if email.strip().lower() == actor:
        raise HTTPException(status_code=400, detail="You cannot remove your own access row")
    service.delete_user(email)
    return {"status": "success", "data": {"users": service.list_users()}}
