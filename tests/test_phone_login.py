import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi.security import HTTPBearer

from app.config.setting import get_settings
from app.model.mod_enum import AccountStatus, UserRole
from app.schema.phone_auth_schema import PhoneLogin
from app.services.auth import login_lockout
from app.services.auth.firebase_auth.auth_errors import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    UnverifiedAccountError,
)
from app.services.auth.phone.phone_auth import PhoneAuthentication
from app.services.auth.phone.token.hash_password import generate_hash

PHONE = "+250781234567"
PASSWORD = "Str0ng!Passw0rd"
IP = "203.0.113.7"


def make_account(**overrides) -> SimpleNamespace:
    fields = dict(
        id="user-1", phone_number=PHONE, password=generate_hash(PASSWORD, rounds=4),
        phone_number_verified=True, status=AccountStatus.ACTIVE, role=UserRole.CLIENT,
        firebase_uid=None, token_version=0, must_change_password=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_service(account) -> tuple[PhoneAuthentication, AsyncMock]:
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = account
    session.execute = AsyncMock(return_value=result)
    return PhoneAuthentication(security=HTTPBearer(), session=session), session


def login(service, password=PASSWORD, phone=PHONE, ip=IP):
    return asyncio.run(service.login(
        user_data=PhoneLogin(phone_number=phone, password=password),
        ip_address=ip, endpoint="/api/v1/auth/phone_auth/login",
    ))


def claims(token: str) -> dict:
    settings = get_settings()
    return pyjwt.decode(token, settings.SECRET_ACCESS_KEY, algorithms=[settings.ALGORITHM])


class TestLoginSuccess:
    def test_returns_a_token_carrying_the_version_and_role(self, redis):
        service, _ = make_service(make_account(token_version=3, role=UserRole.AGENT))

        response = login(service)

        assert response["status"] is True
        payload = claims(response["token"])
        assert payload["user_id"] == "user-1"
        assert payload["token_version"] == 3
        assert payload["role"] == "Agent"
        assert response["role"] == "Agent"
        assert response["must_change_password"] is False

    def test_a_normal_session_lasts_the_configured_time(self, redis):
        service, _ = make_service(make_account())
        exp = claims(login(service)["token"])["exp"]
        minutes = (datetime.fromtimestamp(exp, timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 60
        assert abs(minutes - get_settings().JWT_EXPIRE_MINUTES) < 1

    def test_an_admin_session_follows_its_own_setting(self, redis):
        service, _ = make_service(make_account(role=UserRole.ADMIN))
        exp = claims(login(service)["token"])["exp"]
        minutes = (datetime.fromtimestamp(exp, timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 60
        assert abs(minutes - get_settings().ADMIN_JWT_EXPIRE_MINUTES) < 1

    def test_a_success_clears_earlier_failures(self, redis):
        service, _ = make_service(make_account())
        for _ in range(3):
            with pytest.raises(InvalidCredentialsError):
                login(service, password="Wr0ng!Passw0rd")
        assert redis.values[login_lockout.phone_key(PHONE)] == 3

        login(service)

        assert login_lockout.phone_key(PHONE) not in redis.values


class TestGenericFailures:
    """Nothing in the answer reveals whether a phone number has an account."""

    def test_wrong_password_and_unknown_number_look_identical(self, redis):
        known, _ = make_service(make_account())
        unknown, _ = make_service(None)

        with pytest.raises(InvalidCredentialsError) as wrong_password:
            login(known, password="Wr0ng!Passw0rd")
        with pytest.raises(InvalidCredentialsError) as no_account:
            login(unknown, phone="+250781111111")

        assert wrong_password.value.status_code == no_account.value.status_code == 401
        assert wrong_password.value.detail == no_account.value.detail

    def test_an_account_without_a_password_yet_looks_the_same(self, redis):
        """An invited account that hasn't set its password can't log in, and doesn't say why."""
        service, _ = make_service(make_account(password=None))
        with pytest.raises(InvalidCredentialsError):
            login(service)

    def test_an_unknown_number_still_costs_a_password_comparison(self, redis):
        service, _ = make_service(None)
        with patch("app.services.auth.phone.phone_auth.compare_hash", return_value=False) as compare:
            with pytest.raises(InvalidCredentialsError):
                login(service)
        compare.assert_called_once()

    def test_the_unverified_message_is_only_shown_after_a_correct_password(self, redis):
        service, _ = make_service(make_account(phone_number_verified=False))

        with pytest.raises(InvalidCredentialsError):
            login(service, password="Wr0ng!Passw0rd")
        with pytest.raises(UnverifiedAccountError):
            login(service)

    def test_a_disabled_account_cannot_log_in(self, redis):
        service, _ = make_service(make_account(status=AccountStatus.DISABLE))
        with pytest.raises(AccountDisabledError):
            login(service)

    def test_failures_are_logged_with_the_ip(self, redis):
        service, session = make_service(make_account())
        with pytest.raises(InvalidCredentialsError):
            login(service, password="Wr0ng!Passw0rd")

        logged = session.add.call_args.args[0]
        assert logged.action == "login"
        assert logged.ip_address == IP
        assert logged.user_id == "user-1"
        assert "failed" in logged.description.lower()


class TestLoginLockout:
    def test_five_wrong_passwords_lock_the_account_even_for_the_right_one(self, redis):
        service, _ = make_service(make_account())
        for _ in range(5):
            with pytest.raises(InvalidCredentialsError):
                login(service, password="Wr0ng!Passw0rd")

        with pytest.raises(AccountLockedError):
            login(service)  # the correct password is refused while locked

    def test_a_locked_number_is_refused_before_the_database_is_touched(self, redis):
        service, session = make_service(make_account())
        for _ in range(5):
            asyncio.run(login_lockout.record_failure(PHONE, IP))
        session.execute.reset_mock()

        with pytest.raises(AccountLockedError):
            login(service)

        session.execute.assert_not_called()

    def test_guessing_unknown_numbers_counts_too(self, redis):
        service, _ = make_service(None)
        for _ in range(5):
            with pytest.raises(InvalidCredentialsError):
                login(service)
        with pytest.raises(AccountLockedError):
            login(service)

    def test_the_lockout_attempt_is_logged(self, redis):
        service, session = make_service(make_account())
        for _ in range(5):
            asyncio.run(login_lockout.record_failure(PHONE, IP))

        with pytest.raises(AccountLockedError):
            login(service)

        assert "too many" in session.add.call_args.args[0].description.lower()


class TestChangePasswordEndsSessions:
    def test_changing_the_password_bumps_the_token_version(self):
        account = make_account(token_version=2)
        service, _ = make_service(account)
        from app.schema.phone_auth_schema import ChangePassword

        data = ChangePassword(
            phone_number=PHONE, otp_code="123456",
            new_password="N3w!Passw0rd#", confirm_password="N3w!Passw0rd#",
        )
        target = "app.services.auth.phone.phone_auth"
        with patch(f"{target}.get_otp_code", AsyncMock(return_value="123456")), \
             patch(f"{target}.delete_otp_code", AsyncMock()), \
             patch(f"{target}.OtpManagementService") as otp_service:
            otp_service.return_value.get_latest_unverified_otp = AsyncMock(return_value=None)
            asyncio.run(service.change_password(user_data=data, ip_address=IP, endpoint="/x"))

        assert account.token_version == 3
