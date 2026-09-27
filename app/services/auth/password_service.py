import asyncio
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import AuthProvider, UserRole
from app.schema.password_schema import PasswordChange
from app.services.auth.current_user import find_user_by_id
from app.services.auth.firebase_auth import firebase_accounts
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    InvalidCredentialsError,
    PasswordReuseError,
    UnmatchedPassword,
    WeakPasswordError,
)
from app.services.auth.phone.token.hash_password import compare_hash, generate_hash
from app.services.logs.system_log_service import SystemLogService

ADMIN_PASSWORD_MIN_LENGTH = 12


async def change_password(
    session: AsyncSession,
    user_id: str,
    data: PasswordChange,
    ip_address: Optional[str] = None,
    endpoint: str = "",
) -> dict:
    """
    Replace the signed-in account's password. The current password must be
    given and correct, which also proves the person holding a temporary
    password really is the one it was sent to. Clears the temporary-password
    flag and ends every other session.
    """
    logs = SystemLogService(session)
    try:
        user = await find_user_by_id(session, user_id)
        if user is None:
            raise AccountNotFoundError()

        if data.new_password != data.confirm_password:
            raise UnmatchedPassword(detail="Password did not match")
        if user.role == UserRole.ADMIN and len(data.new_password) < ADMIN_PASSWORD_MIN_LENGTH:
            raise WeakPasswordError(
                f"An admin password must be at least {ADMIN_PASSWORD_MIN_LENGTH} characters long"
            )

        if user.auth_provider == AuthProvider.PHONE_NUMBER:
            current_ok = await asyncio.to_thread(
                compare_hash, password=data.current_password, save_password=str(user.password)
            ) if user.password else False
            if not current_ok:
                raise InvalidCredentialsError("Current password is incorrect")
            if data.new_password == data.current_password:
                raise PasswordReuseError()
            user.password = await asyncio.to_thread(generate_hash, password=data.new_password, rounds=12)
            user.token_version = (user.token_version or 0) + 1  # sign out every other session

        elif user.auth_provider == AuthProvider.FIREBASE_SIMPLE and user.email_address and user.firebase_uid:
            try:
                await firebase_accounts.sign_in_with_password(str(user.email_address), data.current_password)
            except InvalidCredentialsError:
                raise InvalidCredentialsError("Current password is incorrect")
            if data.new_password == data.current_password:
                raise PasswordReuseError()
            await firebase_accounts.set_account_password(str(user.firebase_uid), data.new_password)

        else:
            raise AuthError("This account signs in with Google and has no password to change")

        user.must_change_password = False
        await session.commit()
    except AuthError as exc:
        await logs.create_log(
            action="change_password", entity="Users", user_id=user_id, entity_id=user_id,
            description=f"Password change failed: {exc.detail}",
            ip_address=ip_address, endpoint=endpoint,
        )
        raise

    await logs.create_log(
        action="change_password", entity="Users", user_id=user_id, entity_id=user_id,
        description="Password changed", ip_address=ip_address, endpoint=endpoint,
    )
    return {"status": True, "message": "Password changed. Log in again with your new password"}
