import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database.database import get_session
from app.model.mod_enum import AccountStatus, AuthProvider, UserAccountStatus, UserRole
from app.routers.simlexx.admin_router import router
from app.schema.admin_schema import AdminSignup
from app.services.admin.admin_service import AdminService
from app.services.auth.firebase_auth.auth_errors import (
    AuthError,
    DuplicateAccountError,
    PermissionDeniedError,
    UnmatchedPassword,
    auth_error_handler,
)
from app.services.auth.phone.token.hash_password import compare_hash
from app.services.rate_limiter.limiter import limiter

PHONE = "+256700000001"
EMAIL = "boss@simlexx.com"
PASSWORD = "Adm1n!StrongPassw0rd"
FORM = {"phone_number": PHONE, "email_address": EMAIL, "password": PASSWORD, "confirm_password": PASSWORD}


def make_data(**overrides) -> AdminSignup:
    return AdminSignup(**{**FORM, **overrides})


def make_session(admins_existing: int = 0) -> AsyncMock:
    """execute() is called first for the advisory lock, then for the admin count."""
    session = AsyncMock()
    session.add = MagicMock()
    lock, count = MagicMock(), MagicMock()
    count.scalar_one.return_value = admins_existing

    async def refresh(user):
        # like a real refresh: only fill what the database would default, never overwrite
        user.id = "admin-1"
        if getattr(user, "status", None) is None:
            user.status = AccountStatus.ACTIVE
        if getattr(user, "profile_verification_complete", None) is None:
            user.profile_verification_complete = UserAccountStatus.PENDING
        user.created_at = datetime.now(timezone.utc)

    session.execute = AsyncMock(side_effect=[lock, count])
    session.refresh = AsyncMock(side_effect=refresh)
    return session


@pytest.fixture
def service_parts():
    """The parts of the register flow that talk to Redis / SMS / email."""
    with patch.object(AdminService, "check_existance", AsyncMock()) as check, \
         patch.object(AdminService, "send_verification_otp", AsyncMock()) as otp:
        yield SimpleNamespace(check=check, otp=otp)



def register(session, data=None):
    return asyncio.run(AdminService(session).register_first_admin(
        data=data or make_data(), ip_address="203.0.113.7", endpoint="/api/v1/simlexx/admin/register"
    ))


def added(session):
    return [c.args[0] for c in session.add.call_args_list]


class TestFirstAdmin:
    def test_creates_a_verified_and_approved_admin(self, service_parts):
        """An admin has no profile to review, and SMS can't verify a phone yet, so it starts fully verified."""
        session = make_session()

        result = register(session)

        admin = added(session)[0]
        assert admin.role == UserRole.ADMIN
        assert admin.auth_provider == AuthProvider.PHONE_NUMBER
        assert admin.phone_number == PHONE
        assert admin.email_address == EMAIL
        assert admin.phone_number_verified is True
        assert admin.email_verified is True
        assert admin.profile_verification_complete == UserAccountStatus.APPROVED
        assert result["status"] is True

    def test_the_password_is_stored_hashed(self, service_parts):
        session = make_session()
        register(session)
        admin = added(session)[0]
        assert admin.password != PASSWORD
        assert compare_hash(PASSWORD, admin.password)

    def test_an_otp_is_sent_for_the_new_account(self, service_parts):
        register(make_session())
        service_parts.otp.assert_awaited_once()
        assert service_parts.otp.await_args.kwargs["phone_number"] == PHONE

    def test_the_creation_is_logged_with_the_ip(self, service_parts):
        session = make_session()
        register(session)
        entry = [a for a in added(session) if hasattr(a, "action")][0]
        assert entry.action == "register_admin"
        assert entry.ip_address == "203.0.113.7"

    def test_the_lock_is_taken_before_the_admin_check(self, service_parts):
        session = make_session()
        register(session)
        first_statement = str(session.execute.await_args_list[0].args[0])
        assert "pg_advisory_xact_lock" in first_statement


class TestClosedAfterTheFirst:
    def test_refuses_once_an_admin_exists(self, service_parts):
        session = make_session(admins_existing=1)

        with pytest.raises(PermissionDeniedError) as exc_info:
            register(session)

        assert exc_info.value.status_code == 403
        assert "already complete" in exc_info.value.detail
        assert not [a for a in added(session) if hasattr(a, "role")]
        service_parts.otp.assert_not_awaited()

    def test_a_refused_attempt_is_still_logged(self, service_parts):
        session = make_session(admins_existing=1)
        with pytest.raises(PermissionDeniedError):
            register(session)
        entry = [a for a in added(session) if hasattr(a, "action")][0]
        assert "failed" in entry.description.lower()


class TestRejections:
    def test_duplicate_phone_or_email_is_refused(self, service_parts):
        service_parts.check.side_effect = DuplicateAccountError("This phone number is already registered")
        session = make_session()
        with pytest.raises(DuplicateAccountError):
            register(session)
        assert not [a for a in added(session) if hasattr(a, "role")]

    def test_mismatched_passwords_are_refused(self, service_parts):
        session = make_session()
        with pytest.raises(UnmatchedPassword):
            register(session, make_data(confirm_password="Different!Passw0rd1"))
        assert not [a for a in added(session) if hasattr(a, "role")]


class TestSchema:
    @pytest.mark.parametrize("password", [
        "Sh0rt!Pw", "alllowercase!!!!!1", "NOLOWERCASE!!!!!1", "NoDigits!!!!!!!!!", "NoSpecial123456789",
    ])
    def test_weak_passwords_are_refused(self, password):
        with pytest.raises(ValidationError):
            make_data(password=password, confirm_password=password)

    @pytest.mark.parametrize("phone", ["0700000001", "not-a-number", "+256123"])
    def test_bad_phone_numbers_are_refused(self, phone):
        with pytest.raises(ValidationError):
            make_data(phone_number=phone)

    def test_the_email_is_required_and_validated(self):
        with pytest.raises(ValidationError):
            make_data(email_address="nope")
        with pytest.raises(ValidationError):
            AdminSignup(phone_number=PHONE, password=PASSWORD, confirm_password=PASSWORD)  # type: ignore[call-arg]

    def test_the_phone_number_is_normalised(self):
        assert make_data(phone_number="+256 700 000 001").phone_number == PHONE


class TestEndpoint:
    @pytest.fixture
    def client(self, monkeypatch, service_parts):
        monkeypatch.setattr(limiter, "enabled", False)  # no Redis in unit tests
        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(AuthError, auth_error_handler)
        holder = SimpleNamespace(session=make_session())
        app.dependency_overrides[get_session] = lambda: holder.session
        holder.client = TestClient(app)
        return holder

    def test_needs_no_token(self, client):
        response = client.client.post("/api/v1/simlexx/admin/register", json=FORM)
        assert response.status_code == 200
        assert response.json()["data"]["phone_number"] == PHONE

    def test_is_403_once_an_admin_exists(self, client):
        client.session = make_session(admins_existing=1)
        client.client.app.dependency_overrides[get_session] = lambda: client.session  # type: ignore[attr-defined]

        response = client.client.post("/api/v1/simlexx/admin/register", json=FORM)

        assert response.status_code == 403
        assert "already complete" in response.json()["detail"]

    def test_a_weak_password_is_a_422(self, client):
        response = client.client.post("/api/v1/simlexx/admin/register", json={**FORM, "password": "weak", "confirm_password": "weak"})
        assert response.status_code == 422


class TestEmailOnlyOtp:
    """Admins log in by phone, but their codes are delivered by email only."""

    TARGET = "app.services.auth.phone.phone_auth"

    def run_send(self, email_only):
        session = AsyncMock()
        service = AdminService(session)
        with patch(f"{self.TARGET}.send_otp_sms", AsyncMock()) as sms, \
             patch(f"{self.TARGET}.save_otp_code", AsyncMock()), \
             patch(f"{self.TARGET}.send_email_task") as email, \
             patch(f"{self.TARGET}.OtpManagementService") as otp_service, \
             patch(f"{self.TARGET}.NotificationService") as notifications:
            otp_service.return_value.create_otp = AsyncMock()
            notifications.return_value.create_notification = AsyncMock()
            asyncio.run(service.send_verification_otp(
                user_id="admin-1", phone_number=PHONE, email_address=EMAIL, email_only=email_only
            ))
        return SimpleNamespace(
            sms=sms, email=email,
            otp_channel=otp_service.return_value.create_otp.await_args.kwargs["channel"],
            notification_channel=notifications.return_value.create_notification.await_args.kwargs["channel"],
        )

    def test_email_only_sends_no_sms(self):
        sent = self.run_send(email_only=True)
        sent.sms.assert_not_awaited()
        sent.email.delay.assert_called_once()
        assert sent.email.delay.call_args.args[0] == EMAIL

    def test_email_only_records_the_email_channel(self):
        sent = self.run_send(email_only=True)
        assert sent.otp_channel.value == "email"
        assert sent.notification_channel.value == "email"

    def test_clients_still_get_the_sms(self):
        sent = self.run_send(email_only=False)
        sent.sms.assert_awaited_once()
        assert sent.otp_channel.value == "sms"

    def test_first_admin_registration_asks_for_email_only(self, service_parts):
        register(make_session())
        assert service_parts.otp.await_args.kwargs["email_only"] is True

    def test_an_admin_password_reset_code_goes_by_email_only(self):
        from app.schema.phone_auth_schema import RequestOTP
        admin = SimpleNamespace(id="admin-1", role=UserRole.ADMIN, phone_number=PHONE, email_address=EMAIL)
        client_user = SimpleNamespace(id="user-1", role=UserRole.CLIENT, phone_number=PHONE, email_address=EMAIL)

        for account, expect_sms in ((admin, False), (client_user, True)):
            service = AdminService(AsyncMock())
            with patch.object(AdminService, "find_user_by_phone_or_email", AsyncMock(return_value=account)), \
                 patch.object(AdminService, "log_attempt", AsyncMock()), \
                 patch(f"{self.TARGET}.send_otp_sms", AsyncMock()) as sms, \
                 patch(f"{self.TARGET}.save_otp_code", AsyncMock()), \
                 patch(f"{self.TARGET}.send_email_task") as email, \
                 patch(f"{self.TARGET}.OtpManagementService") as otp_service:
                otp_service.return_value.create_otp = AsyncMock()
                asyncio.run(service.request_otp(RequestOTP(phone_number=PHONE), ip_address=None, endpoint="/x"))

            assert (sms.await_count == 1) is expect_sms
            email.delay.assert_called_once()


class TestCreateAnotherAdmin:
    """An admin creates another admin: phone login, temporary password by email."""

    def make(self, existing_rows=None):
        session = AsyncMock()
        session.add = MagicMock()
        result = MagicMock()
        result.all.return_value = existing_rows or []
        session.execute = AsyncMock(return_value=result)

        async def refresh(user):
            user.id = "admin-2"
            user.created_at = datetime.now(timezone.utc)

        session.refresh = AsyncMock(side_effect=refresh)
        return session

    def create(self, session, **overrides):
        from app.schema.admin_schema import AdminCreate
        fields = dict(first_name="Sam", last_name="Ok", email_address="Sam@Simlexx.com", phone_number="+256700000042")
        fields.update(overrides)
        return asyncio.run(AdminService(session).create_admin(AdminCreate(**fields), created_by="admin-1", ip_address="1.2.3.4", endpoint="/x"))

    def test_creates_a_verified_admin_on_a_temporary_password(self):
        session = self.make()
        with patch("app.services.admin.admin_service.agent_emails.send_account_created_email") as email:
            result = self.create(session)

        admin = [c.args[0] for c in session.add.call_args_list if hasattr(c.args[0], "role")][0]
        assert admin.role == UserRole.ADMIN and admin.auth_provider == AuthProvider.PHONE_NUMBER
        assert admin.phone_number_verified is True and admin.must_change_password is True
        assert admin.email_address == "sam@simlexx.com"
        assert result["credentials_email_queued"] is True

        kwargs = email.call_args.kwargs
        assert kwargs["login_id"] == "+256700000042" and kwargs["account_label"] == "administrator"
        assert compare_hash(kwargs["temporary_password"], admin.password)   # the emailed password is the stored one

    def test_the_response_never_contains_the_password(self):
        with patch("app.services.admin.admin_service.agent_emails.send_account_created_email") as email:
            result = self.create(self.make())
        assert email.call_args.kwargs["temporary_password"] not in str(result)

    def test_a_duplicate_is_refused(self):
        session = self.make(existing_rows=[("sam@simlexx.com", "+256700000099")])
        with pytest.raises(DuplicateAccountError):
            self.create(session)
        assert not [c for c in session.add.call_args_list if hasattr(c.args[0], "role")]

    def test_an_email_that_cannot_be_queued_still_creates_the_admin(self):
        with patch("app.services.admin.admin_service.agent_emails.send_account_created_email", side_effect=RuntimeError("down")):
            result = self.create(self.make())
        assert result["credentials_email_queued"] is False

    def test_it_is_logged_with_the_creator(self):
        session = self.make()
        with patch("app.services.admin.admin_service.agent_emails.send_account_created_email"):
            self.create(session)
        entry = [c.args[0] for c in session.add.call_args_list if hasattr(c.args[0], "action")][0]
        assert entry.action == "create_admin" and entry.user_id == "admin-1" and entry.ip_address == "1.2.3.4"
