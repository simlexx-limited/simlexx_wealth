import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.scripts.create_admin import (
    AdminSetupError,
    create_admin,
    main,
    normalize_phone_number,
)
from app.services.auth.phone.token.hash_password import compare_hash

PHONE = "+256700000001"
EMAIL = "boss@simlexx.com"
PASSWORD = "Adm1n!StrongPassw0rd"


def make_session(existing=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = existing
    session.execute = AsyncMock(return_value=result)
    return session


def run(session, **overrides):
    args = dict(phone_number=PHONE, email_address=EMAIL, password=PASSWORD)
    args.update(overrides)
    return asyncio.run(create_admin(session, **args))


def logs(session):
    return [c.args[0] for c in session.add.call_args_list if hasattr(c.args[0], "action")]


class TestCreate:
    def test_creates_a_verified_active_admin(self):
        session = make_session()

        admin, created = run(session)

        assert created is True
        assert admin.role == UserRole.ADMIN
        assert admin.auth_provider == AuthProvider.PHONE_NUMBER
        assert admin.status == AccountStatus.ACTIVE
        assert admin.phone_number == PHONE
        assert admin.email_address == EMAIL
        assert admin.phone_number_verified is True

    def test_the_password_is_stored_hashed(self):
        admin, _ = run(make_session())
        assert admin.password != PASSWORD
        assert compare_hash(PASSWORD, admin.password)

    def test_the_creation_is_logged(self):
        session = make_session()
        run(session)
        entry = logs(session)[0]
        assert entry.action == "create_admin"
        assert entry.endpoint == "cli:create_admin"

    def test_the_phone_number_is_normalised(self):
        admin, _ = run(make_session(), phone_number="+256 700 000 001")
        assert admin.phone_number == PHONE

    def test_a_second_admin_can_be_created(self):
        """Only the same phone/email conflicts; a different number is just another admin."""
        session = make_session(existing=None)
        admin, created = run(session, phone_number="+256700000002", email_address="second@simlexx.com")
        assert created is True


class TestExisting:
    def test_an_existing_admin_is_not_recreated_or_changed(self):
        existing = SimpleNamespace(id="a1", role=UserRole.ADMIN, password="old", token_version=0)
        session = make_session(existing)

        with pytest.raises(AdminSetupError, match="already exists"):
            run(session)

        assert existing.password == "old"
        session.add.assert_not_called()

    def test_an_existing_non_admin_is_never_promoted(self):
        existing = SimpleNamespace(id="u1", role=UserRole.CLIENT, password="old", token_version=0)
        session = make_session(existing)

        with pytest.raises(AdminSetupError, match="non-admin"):
            run(session)
        with pytest.raises(AdminSetupError, match="non-admin"):
            run(session, reset_password=True)

        assert existing.role == UserRole.CLIENT
        assert existing.password == "old"


class TestReset:
    def test_reset_sets_a_new_password_and_signs_the_admin_out(self):
        existing = SimpleNamespace(id="a1", role=UserRole.ADMIN, password="old", token_version=4)
        session = make_session(existing)

        admin, created = run(session, reset_password=True)

        assert created is False
        assert compare_hash(PASSWORD, admin.password)
        assert admin.token_version == 5
        assert logs(session)[0].action == "reset_admin_password"

    def test_reset_with_nobody_to_reset_is_an_error(self):
        with pytest.raises(AdminSetupError, match="No admin"):
            run(make_session(None), reset_password=True)


class TestValidation:
    @pytest.mark.parametrize("password", [
        "Sh0rt!Pw", "alllowercase!!!!!1", "NOLOWERCASE!!!!!1", "NoDigits!!!!!!!!!", "NoSpecial123456789",
    ])
    def test_weak_passwords_are_refused(self, password):
        with pytest.raises(AdminSetupError):
            run(make_session(), password=password)

    @pytest.mark.parametrize("phone", ["0700000001", "not-a-number", "+256123"])
    def test_bad_phone_numbers_are_refused(self, phone):
        with pytest.raises(AdminSetupError):
            normalize_phone_number(phone)

    def test_a_bad_email_is_refused(self):
        with pytest.raises(AdminSetupError, match="email"):
            run(make_session(), email_address="nope")

    def test_nothing_is_saved_when_validation_fails(self):
        session = make_session()
        with pytest.raises(AdminSetupError):
            run(session, password="weak")
        session.add.assert_not_called()
        session.commit.assert_not_called()


class TestCommandLine:
    def test_a_setup_error_becomes_a_message_and_exit_code_1(self, monkeypatch, capsys):
        monkeypatch.setattr("app.scripts.create_admin.getpass.getpass", lambda prompt: "weak")
        assert main(["--phone", PHONE, "--email", EMAIL]) == 1
        assert "Error:" in capsys.readouterr().err
