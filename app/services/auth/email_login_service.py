from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schema.email_auth_schema import EmailLogin
from app.services.auth import login_lockout
from app.services.auth.current_user import ACTIVE_STATUSES, find_user_by_firebase_uid
from app.services.auth.firebase_auth import firebase_accounts
from app.services.auth.firebase_auth.auth_errors import (
    AccountDisabledError,
    AuthError,
    InvalidCredentialsError,
)
from app.services.logs.system_log_service import SystemLogService


async def email_login(
    session: AsyncSession,
    data: EmailLogin,
    ip_address: Optional[str] = None,
    endpoint: str = "",
) -> dict:
    """
    Log in with an email address and password: Firebase checks the password
    and we make sure the account is known to us and active. Returns the
    Firebase ID token to send as the bearer token.
    """
    email = str(data.email).lower()
    logs = SystemLogService(session)
    user_id: Optional[str] = None
    try:
        await login_lockout.ensure_not_locked(email, ip_address)
        try:
            result = await firebase_accounts.sign_in_with_password(email, data.password)
        except InvalidCredentialsError:
            await login_lockout.record_failure(email, ip_address)
            raise

        user = await find_user_by_firebase_uid(session, result["localId"])
        if user is None:
            raise InvalidCredentialsError("Invalid email or password")
        user_id = str(user.id)
        if user.status not in ACTIVE_STATUSES:
            raise AccountDisabledError()
    except AuthError as exc:
        await logs.create_log(
            action="login", entity="Users", user_id=user_id, entity_id=user_id,
            description=f"Login failed: {exc.detail}", ip_address=ip_address, endpoint=endpoint,
        )
        raise

    await login_lockout.clear_failures(email)
    await logs.create_log(
        action="login", entity="Users", user_id=user_id, entity_id=user_id,
        description="Login successful", ip_address=ip_address, endpoint=endpoint,
    )
    return {
        "status": True,
        "message": "Login successful",
        "token": result["idToken"],
        "refresh_token": result.get("refreshToken"),
        "expires_in": result.get("expiresIn"),
        "role": user.role.value,
        "must_change_password": bool(user.must_change_password),
    }
