from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.security import HTTPBearer
from sqlalchemy.exc import IntegrityError

from app.model.mod_enum import (
    AccountStatus,
    NotificationChannel,
    OtpPurpose,
    UserAccountStatus,
    UserRole,
)
from app.schema.phone_auth_schema import PhoneNumberSignup
from app.services.auth.firebase_auth.auth_errors import DuplicateAccountError, UnmatchedPassword
from app.services.auth.phone.phone_auth import PhoneAuthentication

PATCH_TARGET = "app.services.auth.phone.phone_auth"


def make_signup_schema(**overrides: Any) -> PhoneNumberSignup:
    defaults: dict[str, Any] = {
        "phone_number": "+250781234567",
        "password": "Str0ng!Passw0rd",
        "confirm_password": "Str0ng!Passw0rd",
        "email_address": "phoneuser@example.com",
        "role": UserRole.CLIENT,
    }
    defaults.update(overrides)
    return PhoneNumberSignup(**defaults)


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
# PhoneAuthentication.register - duplicate detection, password checks,
# hashing, and the send-then-persist OTP/notification/system-log wiring.
# ---------------------------------------------------------------------------

class TestPhoneAuthRegister:
    @pytest.mark.asyncio
    async def test_rejects_duplicate_phone_only(self):
        session = make_fake_session([True, False])
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(register_data=make_signup_schema())

        assert "phone number" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_email_only(self):
        session = make_fake_session([False, True])
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(register_data=make_signup_schema())

        assert "email" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_both(self):
        session = make_fake_session([True, True])
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError) as exc_info:
            await service.register(register_data=make_signup_schema())

        assert "both" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_mismatched_passwords(self):
        session = make_fake_session([False, False])
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with pytest.raises(UnmatchedPassword):
            await service.register(
                register_data=make_signup_schema(confirm_password="S0mething!Else99")
            )

    @pytest.mark.asyncio
    async def test_integrity_error_on_commit_raises_duplicate_account_error(self):
        session = make_fake_session([False, False])
        # Only the registration insert should fail - after the rollback, the
        # session is usable again (as it would be against real Postgres),
        # including for the failure-path system-log entry register() now
        # writes via log_attempt().
        session.commit = AsyncMock(
            side_effect=[IntegrityError("INSERT", {}, Exception("unique violation")), None]
            )
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with pytest.raises(DuplicateAccountError):
            await service.register(register_data=make_signup_schema())

        session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_password_is_hashed_before_storage(self):
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        with patch(f"{PATCH_TARGET}.generate_otp_code", return_value=12345678), \
             patch(f"{PATCH_TARGET}.save_otp_code", new=AsyncMock()), \
             patch(f"{PATCH_TARGET}.send_otp_sms", new=AsyncMock()), \
             patch(f"{PATCH_TARGET}.queue_email", new=MagicMock()):
            await service.register(
                register_data=make_signup_schema(
                    password="Str0ng!Passw0rd", confirm_password="Str0ng!Passw0rd"
                )
            )

        created_user = session.add.call_args_list[0][0][0]
        assert created_user.password != "Str0ng!Passw0rd"
        assert created_user.password

    @pytest.mark.asyncio
    async def test_successful_registration_sends_before_it_persists(self):
        """Redis/notification/OTP-record persistence should only happen
        after the code has actually been dispatched to every applicable
        channel (phone always, email when provided) - not interleaved."""
        session = make_fake_session([False, False])
        session.refresh = AsyncMock(side_effect=fake_refresh_factory())
        service = PhoneAuthentication(security=HTTPBearer(), session=session)

        call_order: list[str] = []

        async def fake_send_sms(phone_number: str, code: str) -> None:
            call_order.append("send_sms")

        async def fake_save_otp_code(identifier: str, purpose: OtpPurpose, code: str) -> None:
            call_order.append("save_redis")

        mock_email_task = MagicMock()
        mock_email_task.side_effect = lambda *a, **k: call_order.append("send_email")

        with patch(f"{PATCH_TARGET}.generate_otp_code", return_value=99999999), \
             patch(f"{PATCH_TARGET}.send_otp_sms", side_effect=fake_send_sms), \
             patch(f"{PATCH_TARGET}.save_otp_code", side_effect=fake_save_otp_code), \
             patch(f"{PATCH_TARGET}.queue_email", mock_email_task):
            result = await service.register(
                register_data=make_signup_schema(
                    phone_number="+250781234567", email_address="phoneuser@example.com"
                )
            )

        assert result["status"] is True
        data = result["data"]
        assert data.id == "fake-generated-id"
        assert data.phone_number == "+250781234567"

        assert call_order.index("send_sms") < call_order.index("save_redis")
        assert call_order.index("send_email") < call_order.index("save_redis")

        added = [call.args[0] for call in session.add.call_args_list]
        assert len(added) == 4  # Users, OtpVerification, Notifications, SystemLogs

        otp_record = added[1]
        assert otp_record.channel == NotificationChannel.SMS
        assert otp_record.purpose == OtpPurpose.PHONE_VERIFICATION
        assert otp_record.code == "99999999"

        notification_record = added[2]
        assert notification_record.channel == NotificationChannel.SMS
        assert notification_record.type == "welcome_otp"

        log_record = added[3]
        assert log_record.action == "register"
        assert log_record.entity == "Users"
        assert log_record.entity_id == "fake-generated-id"
