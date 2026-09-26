import asyncio
from typing import Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.model.models import AgentProfiles, Users
from app.services.auth.firebase_auth.auth_errors import (
    AccountDisabledError,
    PasswordChangeRequiredError,
    AgentAccountIncompleteError,
)
from app.services.auth.phone.token.jwt import JwtManagement

_bearer = HTTPBearer()

# Firebase signs its ID tokens with RS256; our own tokens use settings.ALGORITHM.
# The token header is only used to route the token to the right verifier -
# each verifier pins its own algorithm, so a token can't choose how it is checked.
FIREBASE_ALGORITHM = "RS256"

ACTIVE_STATUSES = (AccountStatus.ACTIVE, AccountStatus.REACTIVE)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def find_user_by_id(session: AsyncSession, user_id: str) -> Optional[Users]:
    result = await session.execute(select(Users).where(Users.id == user_id))
    return result.scalar_one_or_none()


async def find_user_by_firebase_uid(session: AsyncSession, firebase_uid: str) -> Optional[Users]:
    result = await session.execute(select(Users).where(Users.firebase_uid == firebase_uid))
    return result.scalar_one_or_none()


async def find_unlinked_user_by_email(session: AsyncSession, email: str) -> Optional[Users]:
    """
    A Firebase-provider account that was created ahead of time (for example a
    agent created by an admin) and hasn't signed in yet. Phone-registered
    accounts are excluded on purpose: their email was never verified, so it must
    not be usable to take the account over through Firebase.
    """
    result = await session.execute(
        select(Users).where(
            Users.email_address == email,
            Users.firebase_uid.is_(None),
            Users.auth_provider.in_([AuthProvider.FIREBASE_GOOGLE, AuthProvider.FIREBASE_SIMPLE]),
        )
    )
    return result.scalar_one_or_none()


async def has_agent_profile(session: AsyncSession, user_id: str) -> bool:
    """Agents are created by an admin together with their profile; without one the account is incomplete."""
    result = await session.execute(select(AgentProfiles.id).where(AgentProfiles.user_id == user_id))
    return result.first() is not None


async def user_from_own_token(token: str, session: AsyncSession) -> Users:
    """Verify a token issued by our phone/password login and load its account."""
    try:
        payload = await JwtManagement().decode_auth(token)
    except pyjwt.ExpiredSignatureError:
        raise unauthorized("Token has expired")
    except pyjwt.PyJWTError:
        raise unauthorized("Invalid or malformed token")

    user_id = payload.get("user_id")
    user = await find_user_by_id(session, str(user_id)) if user_id else None
    if user is None:
        raise unauthorized("Invalid or malformed token")

    # Tokens issued before the last password change / logout-everywhere carry
    # an older version and stop working immediately.
    if payload.get("token_version", 0) != (user.token_version or 0):
        raise unauthorized("Session is no longer valid. Please log in again")
    return user


async def user_from_firebase_token(token: str, session: AsyncSession) -> Users:
    """Verify a Firebase ID token (email / Google sign-in) and load its account."""
    try:
        decoded = await asyncio.to_thread(firebase_auth.verify_id_token, token)
    except Exception:
        raise unauthorized("Invalid or malformed token")

    uid = decoded.get("uid")
    if not uid:
        raise unauthorized("Invalid or malformed token")

    user = await find_user_by_firebase_uid(session, uid)
    if user is None and decoded.get("email") and decoded.get("email_verified"):
        # First sign-in of an account we created ahead of time: link it, but
        # only through a *verified* email that matches the one we invited.
        user = await find_unlinked_user_by_email(session, decoded["email"])
        if user is not None:
            user.firebase_uid = uid
            await session.commit()
    if user is None:
        raise unauthorized("Account not found")
    return user


async def get_current_user_any_state(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Authenticates the caller and checks the account is allowed in, but does
    NOT insist that a temporary password has been replaced. Only the endpoint
    that changes the password should use this; everything else uses
    get_current_user below.
    """
    token = credentials.credentials
    try:
        algorithm = pyjwt.get_unverified_header(token).get("alg")
    except pyjwt.PyJWTError:
        raise unauthorized("Invalid or malformed token")

    if algorithm == FIREBASE_ALGORITHM:
        user = await user_from_firebase_token(token, session)
    else:
        user = await user_from_own_token(token, session)

    if user.status not in ACTIVE_STATUSES:
        raise AccountDisabledError()

    role = UserRole(user.role)
    if role == UserRole.AGENT and not await has_agent_profile(session, str(user.id)):
        raise AgentAccountIncompleteError()

    return {
        "user_id": str(user.id),
        "role": role,
        "firebase_uid": user.firebase_uid,
        "must_change_password": bool(user.must_change_password),
    }


async def get_current_user(payload: dict = Depends(get_current_user_any_state)) -> dict:
    """
    The single gate for every protected endpoint.

    Accepts either token type (our phone/password JWT, or a Firebase ID token
    from email / Google sign-in), loads the matching account, and makes sure it
    is still allowed in. The role always comes from the database, never from
    the token, so a role change or a suspension applies on the very next request.
    An account still on a temporary password is refused until it changes it.

    Returns {"user_id", "role", "firebase_uid", "must_change_password"}.
    """
    if payload["must_change_password"]:
        raise PasswordChangeRequiredError()
    return payload
