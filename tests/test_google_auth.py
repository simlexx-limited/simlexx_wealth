from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
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
from app.routers.auth.google_signin_router import register as google_register
from app.schema.email_auth_schema import EmailSignINSchema, User_signup_schema
from app.services.auth.firebase_auth.auth_errors import DuplicateAccountError, UnverifiedAccountError
from app.services.auth.firebase_auth.google_auth import Google_Auth


def make_signup_schema(**overrides: Any) -> User_signup_schema:
    defaults: dict[str, Any] = {"phone_number": "+250250123456", "role": UserRole.CLIENT}
    defaults.update(overrides)
    return User_signup_schema(**defaults)


def make_google_signin_schema(**overrides: Any) -> EmailSignINSchema:
    defaults: dict[str, Any] = {
        "firebase_uid": "fake-google-uid",
        "email": "googleuser@example.com",
        "email_verified": True,
        "auth_provider": AuthProvider.FIREBASE_GOOGLE,
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


def make_decoded_google_token(**overrides: Any) -> dict[str, Any]:
    """
    Realistic shape of a Firebase-decoded Google-provider token, based on
    real tokens seen against a live Firebase project this session. Note
    sign_in_provider lives nested under "firebase", not top-level - that
    nesting is exactly what broke before.
    """
    token: dict[str, Any] = {
        "user_id": "fake-google-uid",
        "email": "googleuser@example.com",
        "email_verified": True,
        "name": "Bode Murairi",
        "picture": "https://example.com/photo.jpg",
        "firebase": {
            "identities": {"google.com": ["1234567890"]},
            "sign_in_provider": "google.com",
        },
    }
    token.update(overrides)
    return token


def fake_refresh_factory():
    async def fake_refresh(user_obj):
        # simulates what a real commit+refresh against Postgres would
        # populate: defaults that only exist after the row round-trips
        # through the database.
        user_obj.id = "fake-generated-id"
        user_obj.status = AccountStatus.ACTIVE
        user_obj.profile_verification_complete = UserAccountStatus.PENDING
        user_obj.created_at = datetime.now(timezone.utc)

    return fake_refresh


# ---------------------------------------------------------------------------
# Google_Auth.register - registration logic (mirrors Email_Auth's behaviour:
# duplicate detection, IntegrityError fallback, response shape)
# ---------------------------------------------------------------------------

class TestGoogleAuthRegister:
    @pytest.mark.asyncio
    async def test_rejects_unverified_email(self):
        session = make_fake_session([])
        service = Google_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(UnverifiedAccountError):
            await service.register(
                new_user=make_signup_schema(),
                email_sigin=make_google_signin_schema(email_verified=False),
            )

        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_phone_only(self):
        session = make_fake_session([True, False])
        service = Google_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_google_signin_schema())

        assert "phone number" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_email_only(self):
        session = make_fake_session([False, True])
        service = Google_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_google_signin_schema())

        assert "email" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_both(self):
        session = make_fake_session([True, True])
        service = Google_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(new_user=make_signup_schema(), email_sigin=make_google_signin_schema())

        assert "both" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_integrity_error_on_commit_raises_duplicate_account_error(self):
        session = make_fake_session([False, False])
        session.commit = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("unique violation")))
        service = Google_Auth(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError):
            await service.register(new_user=make_signup_schema(), email_sigin=make_google_signin_schema())

        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_registration_returns_expected_shape(self):
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())
        service = Google_Auth(security=HTTPBearer(), session=session)

        result = await service.register(
            new_user=make_signup_schema(phone_number="+250250123456"),
            email_sigin=make_google_signin_schema(email="new.google.user@example.com"),
        )

        assert result["status"] is True
        assert result["message"] == "User created successfully"
        data = result["data"]
        assert data.id == "fake-generated-id"
        assert data.phone_number == "+250250123456"
        assert data.email_address == "new.google.user@example.com"
        assert data.role == UserRole.CLIENT
        assert data.status == AccountStatus.ACTIVE
        assert data.profile_verification_complete == UserAccountStatus.PENDING

        # Users row + the OtpManagementService/SystemLogService audit records
        # created after registration succeeds.
        assert session.add.call_count == 3
        assert session.commit.await_count == 3

    @pytest.mark.asyncio
    async def test_stores_google_auth_provider_not_password(self):
        """Regression check: an account created via Google sign-in should be
        tagged FIREBASE_GOOGLE, not silently default to something else."""
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())
        service = Google_Auth(security=HTTPBearer(), session=session)

        await service.register(new_user=make_signup_schema(), email_sigin=make_google_signin_schema())

        created_user = session.add.call_args_list[0][0][0]
        assert created_user.auth_provider == AuthProvider.FIREBASE_GOOGLE

    @pytest.mark.asyncio
    async def test_creates_verified_otp_audit_and_system_log_records(self):
        """Firebase already verifies the email before this call, so the
        OTP row created here should record that fact immediately rather
        than represent a pending, unverified code."""
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())
        service = Google_Auth(security=HTTPBearer(), session=session)

        await service.register(
            new_user=make_signup_schema(phone_number="+250250123458"),
            email_sigin=make_google_signin_schema(email="audit.check@example.com"),
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
        assert "google" in log_record.description.lower()


# ---------------------------------------------------------------------------
# google_signin_router.register - the route function itself: covers the
# claim-extraction logic that lives only here (signin_provider/user_name),
# including the exact regression that broke before (the nested "firebase"
# claim path, and the request body being overwritten before use).
# ---------------------------------------------------------------------------

class TestGoogleSigninRoute:
    @pytest.mark.asyncio
    async def test_extracts_nested_signin_provider_and_merges_response(self):
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())

        result = await google_register(
            user_data=make_signup_schema(phone_number="+250780111222"),
            db_session=session,
            user_signin=make_decoded_google_token(),
        )

        assert result["status"] is True
        assert result["signin_provider"] == "google.com"
        assert result["user_name"] == "Bode Murairi"
        assert result["data"].phone_number == "+250780111222"
        assert result["data"].email_address == "googleuser@example.com"

    @pytest.mark.asyncio
    async def test_missing_firebase_claim_block_degrades_to_empty_string(self):
        """If the nested "firebase" claim block is ever missing, this should
        degrade to an empty string, not raise KeyError."""
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())

        token_without_firebase_block = make_decoded_google_token()
        del token_without_firebase_block["firebase"]

        result = await google_register(
            user_data=make_signup_schema(),
            db_session=session,
            user_signin=token_without_firebase_block,
        )

        assert result["signin_provider"] == ""
