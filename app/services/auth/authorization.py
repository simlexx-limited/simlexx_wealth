from typing import Callable, Optional

from fastapi import Depends

from app.model.mod_enum import UserRole
from app.services.auth.firebase_auth.auth_errors import PermissionDeniedError
from app.services.auth.current_user import get_current_user

# Agents review client profiles.
REVIEWER_ROLES = (UserRole.AGENT,)


def get_role(payload: dict) -> Optional[UserRole]:
    """The role claim of a decoded token, or None if it's missing or not a real role."""
    try:
        return UserRole(payload.get("role"))
    except ValueError:
        return None


def require_roles(*allowed: UserRole) -> Callable:
    """
    Dependency factory: only let through callers whose token carries one of
    the allowed roles. Authentication (a valid token, an active account) is
    checked first by get_current_user; this adds the authorization step.

        @router.post("/create")
        async def create(payload: dict = Depends(require_roles(UserRole.CLIENT))): ...
    """
    async def check_role(payload: dict = Depends(get_current_user)) -> dict:
        if get_role(payload) not in allowed:
            raise PermissionDeniedError("Your account type is not allowed to do this")
        return payload

    return check_role


def ensure_owner(payload: dict, user_id: str) -> None:
    """Somebody may only act on their own profile."""
    if payload.get("user_id") != user_id:
        raise PermissionDeniedError("You can only access your own profile")
