import asyncio

import httpx
from firebase_admin import auth

from app.config.setting import get_settings
from app.services.auth.firebase_auth.auth_errors import (
    AccountDisabledError,
    AccountLockedError,
    AuthError,
    DuplicateAccountError,
    InvalidCredentialsError,
)

SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"


class FirebaseUnavailableError(AuthError):
    status_code = 503
    detail = "The sign-in service is temporarily unavailable. Try again shortly"


async def create_email_account(email: str, password: str, display_name: str) -> str:
    """Create an email/password account in Firebase and return its uid."""
    try:
        record = await asyncio.to_thread(
            auth.create_user, email=email, password=password, display_name=display_name
        )
    except auth.EmailAlreadyExistsError:
        raise DuplicateAccountError("This email address is already registered")
    return str(record.uid)


async def delete_account(uid: str) -> None:
    """Remove a Firebase account (used to undo a creation that failed half way)."""
    await asyncio.to_thread(auth.delete_user, uid)


async def set_account_disabled(uid: str, disabled: bool) -> None:
    """Disable or re-enable sign-in. Disabling also ends refreshable sessions."""
    await asyncio.to_thread(auth.update_user, uid, disabled=disabled)
    if disabled:
        await asyncio.to_thread(auth.revoke_refresh_tokens, uid)


async def revoke_account_sessions(uid: str) -> None:
    """End every refreshable session of a Firebase account."""
    await asyncio.to_thread(auth.revoke_refresh_tokens, uid)


async def set_account_password(uid: str, password: str) -> None:
    """Set a new password and end every refreshable session."""
    await asyncio.to_thread(auth.update_user, uid, password=password)
    await asyncio.to_thread(auth.revoke_refresh_tokens, uid)


async def make_password_reset_link(email: str) -> str:
    """A one-time link that lets the person choose a new password."""
    return str(await asyncio.to_thread(auth.generate_password_reset_link, email))


async def sign_in_with_password(email: str, password: str) -> dict:
    """
    Check an email/password pair against Firebase (its REST API) and return the
    sign-in result: idToken, refreshToken, expiresIn, localId.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                SIGN_IN_URL,
                params={"key": get_settings().FIREBASE_WEB_API},
                json={"email": email, "password": password, "returnSecureToken": True},
            )
    except httpx.HTTPError:
        raise FirebaseUnavailableError()

    if response.status_code == 200:
        return response.json()

    message = str(response.json().get("error", {}).get("message", ""))
    if message.startswith("USER_DISABLED"):
        raise AccountDisabledError()
    if message.startswith("TOO_MANY_ATTEMPTS"):
        raise AccountLockedError("Too many failed login attempts. Try again later")
    # EMAIL_NOT_FOUND, INVALID_PASSWORD, INVALID_LOGIN_CREDENTIALS and anything
    # else all look the same, so the answer doesn't reveal who has an account.
    raise InvalidCredentialsError("Invalid email or password")
