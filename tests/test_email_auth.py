from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBearer
from sqlalchemy.exc import IntegrityError

from app.model.mod_enum import (
    AccountStatus,
    AuthProvider,
    NotificationChannel,
    OtpPurpose,
    UserAccountStatus,
    UserRole,
)
from app.schema.email_auth_schema import EmailSignINSchema, User_signup_schema
from app.services.firebase_auth.auth_errors import DuplicateAccountError, UnverifiedAccountError
from app.services.firebase_auth.email_Auth import Email_Auth
from app.services.firebase_auth.firebase_auth import Auth


def make_signup_schema(**overrides: Any) -> User_signup_schema:
    defaults: dict[str, Any] = {"phone_number": "+250250123456", "role": UserRole.CLIENT}
    defaults.update(overrides)
    return User_signup_schema(**defaults)


def make_signin_schema(**overrides: Any) -> EmailSignINSchema:
    defaults: dict[str, Any] = {
        "firebase_uid": "fake-firebase-uid",
        "email": "test@example.com",
        "email_verified": True,
        "auth_provider": AuthProvider.FIREBASE_SIMPLE,
    }
    defaults.update(overrides)
    return EmailSignINSchema(**defaults)


def make_fake_session(scalar_results: list[bool]) -> AsyncMock:
    """
    A fake AsyncSession whose successive `execute(...)` calls return
    `.scalar()` values from `scalar_results`, in order - e.g. [False, False]
    means "phone doesn't exist" then "email doesn't exist".
    """
    session = AsyncMock()
    session.add = MagicMock()  # real AsyncSession.add() is sync, not async
    results = []
    for value in scalar_results:
        result = MagicMock()
        result.scalar.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


# ---------------------------------------------------------------------------
# Auth.verify_credentials - the Firebase-touching piece
# ---------------------------------------------------------------------------

class TestVerifyCredentials:
    @pytest.mark.asyncio
    async def test_valid_token_returns_decoded_claims(self):
        auth_service = Auth(security=HTTPBearer(), session=None)
        fake_credentials = MagicMock()
        fake_credentials.credentials = "a-valid-looking-token"

        decoded = {"uid": "abc123", "email": "test@example.com", "email_verified": True}
        with patch(
            "app.services.firebase_auth.firebase_auth.auth.verify_id_token",
            return_value=decoded,
        ) as mock_verify:
            result = await auth_service.verify_credentials(credentials=fake_credentials)

        mock_verify.assert_called_once_with("a-valid-looking-token")
        assert result == decoded

    @pytest.mark.asyncio
    async def test_invalid_token_raises_401(self):
        auth_service = Auth(security=HTTPBearer(), session=None)
        fake_credentials = MagicMock()
        fake_credentials.credentials = "a-bad-token"

        with patch(
            "app.services.firebase_auth.firebase_auth.auth.verify_id_token",
            side_effect=Exception("Firebase rejected this token"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await auth_service.verify_credentials(credentials=fake_credentials)

        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Email_Auth.register - the actual registration business logic
# ---------------------------------------------------------------------------

class TestRegister:
    @pytest.mark.asyncio
    async def test_rejects_unverified_email(self):
        session = make_fake_session([])
        service = Email_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(UnverifiedAccountError):
            await service.register(
                new_user=make_signup_schema(),
                email_sigin=make_signin_schema(email_verified=False),
            )

        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_phone_only(self):
        session = make_fake_session([True, False])  # phone exists, email doesn't
        service = Email_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_signin_schema())

        assert "phone number" in exc_info.value.detail.lower()
        assert "email" not in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_email_only(self):
        session = make_fake_session([False, True])  # phone doesn't exist, email does
        service = Email_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_signin_schema())

        assert "email" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_both(self):
        session = make_fake_session([True, True])
        service = Email_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_signin_schema())

        assert "both" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_integrity_error_on_commit_raises_duplicate_account_error(self):
        session = make_fake_session([False, False])  # pre-checks both pass
        session.commit = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("unique violation")))
        service = Email_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError):
            await service.register(new_user=make_signup_schema(), email_sigin=make_signin_schema())

        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_registration_returns_expected_shape(self):
        session = make_fake_session([False, False])  # neither phone nor email exists

        async def fake_refresh(user_obj):
            user_obj.id = "fake-generated-id"
            user_obj.status = AccountStatus.ACTIVE
            user_obj.profile_verification_complete = UserAccountStatus.PENDING
            user_obj.created_at = datetime.now(timezone.utc)

        session.refresh = AsyncMock(side_effect=fake_refresh)
        service = Email_Auth(security=HTTPBearer(), session=session)

        result = await service.register(
            new_user=make_signup_schema(phone_number="+250250123456"),
            email_sigin=make_signin_schema(email="new.user@example.com"),
        )

        assert result["status"] is True
        assert result["message"] == "User created successfully"
        data = result["data"]
        assert data.id == "fake-generated-id"
        assert data.phone_number == "+250250123456"
        assert data.email_address == "new.user@example.com"
        assert data.role == UserRole.CLIENT
        assert data.status == AccountStatus.ACTIVE
        assert data.profile_verification_complete == UserAccountStatus.PENDING

        # Users row + the OtpManagementService/SystemLogService audit records
        # created after registration succeeds.
        assert session.add.call_count == 3
        assert session.commit.await_count == 3

    @pytest.mark.asyncio
    async def test_creates_verified_otp_audit_and_system_log_records(self):
        """Firebase already verifies the email before this call, so the
        OTP row created here should record that fact immediately rather
        than represent a pending, unverified code."""
        session = make_fake_session([False, False])

        async def fake_refresh(user_obj):
            user_obj.id = "fake-generated-id"
            user_obj.status = AccountStatus.ACTIVE
            user_obj.profile_verification_complete = UserAccountStatus.PENDING
            user_obj.created_at = datetime.now(timezone.utc)

        session.refresh = AsyncMock(side_effect=fake_refresh)
        service = Email_Auth(security=HTTPBearer(), session=session)

        await service.register(
            new_user=make_signup_schema(phone_number="+250250123458"),
            email_sigin=make_signin_schema(email="audit.check@example.com"),
        )

        added = [call.args[0] for call in session.add.call_args_list]
        assert len(added) == 3

        otp_record = added[1]
        assert otp_record.channel == NotificationChannel.EMAIL
        assert otp_record.purpose == OtpPurpose.EMAIL_VERIFICATION
        assert otp_record.code == "FIREBASE_VERIFIED"
        assert otp_record.verified_at is not None
        assert otp_record.expires_at == otp_record.verified_at

        log_record = added[2]
        assert log_record.action == "register"
        assert log_record.entity == "Users"
        assert log_record.entity_id == "fake-generated-id"
        assert "email/password" in log_record.description.lower()
