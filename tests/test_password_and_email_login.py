import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.schema.email_auth_schema import EmailLogin
from app.schema.password_schema import PasswordChange
from app.services.auth import email_login_service, login_lockout, password_service
from app.services.auth.firebase_auth import firebase_accounts
from app.services.auth.firebase_auth.auth_errors import (
    AccountDisabledError,
    AccountLockedError,
    AuthError,
    DuplicateAccountError,
    InvalidCredentialsError,
    PasswordReuseError,
    UnmatchedPassword,
    WeakPasswordError,
)
from app.services.auth.phone.token.hash_password import compare_hash, generate_hash
from app.services.auth.temp_password import generate_temporary_password

OLD = "0ld!Passw0rd#1"
NEW = "N3w!Passw0rd#2"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- temp password

class TestTemporaryPassword:
    def test_always_meets_the_password_rules(self):
        from app.schema.phone_auth_schema import match_pattern
        for _ in range(200):
            password = generate_temporary_password()
            assert len(password) == 16
            assert match_pattern().match(password)

    def test_is_random(self):
        assert len({generate_temporary_password() for _ in range(50)}) == 50

    def test_refuses_a_short_length(self):
        with pytest.raises(ValueError):
            generate_temporary_password(8)


# ------------------------------------------------------------- Firebase helpers

class TestFirebaseAccounts:
    def test_create_returns_the_uid(self):
        with patch.object(firebase_accounts.auth, "create_user", return_value=SimpleNamespace(uid="uid-1")) as create:
            uid = run(firebase_accounts.create_email_account("a@b.com", "Pw!", "Jane Doe"))
        assert uid == "uid-1"
        create.assert_called_once_with(email="a@b.com", password="Pw!", display_name="Jane Doe")

    def test_an_existing_email_is_a_409(self):
        error = firebase_accounts.auth.EmailAlreadyExistsError("exists", None, None)
        with patch.object(firebase_accounts.auth, "create_user", side_effect=error):
            with pytest.raises(DuplicateAccountError) as exc_info:
                run(firebase_accounts.create_email_account("a@b.com", "Pw!", "Jane"))
        assert exc_info.value.status_code == 409

    def test_disabling_also_revokes_sessions_but_enabling_does_not(self):
        with patch.object(firebase_accounts.auth, "update_user") as update, \
             patch.object(firebase_accounts.auth, "revoke_refresh_tokens") as revoke:
            run(firebase_accounts.set_account_disabled("uid-1", True))
            update.assert_called_once_with("uid-1", disabled=True)
            revoke.assert_called_once_with("uid-1")
            revoke.reset_mock()
            run(firebase_accounts.set_account_disabled("uid-1", False))
            revoke.assert_not_called()

    def test_setting_a_password_revokes_sessions(self):
        with patch.object(firebase_accounts.auth, "update_user") as update, \
             patch.object(firebase_accounts.auth, "revoke_refresh_tokens") as revoke:
            run(firebase_accounts.set_account_password("uid-1", "Pw!"))
        update.assert_called_once_with("uid-1", password="Pw!")
        revoke.assert_called_once_with("uid-1")

    def test_reset_link(self):
        with patch.object(firebase_accounts.auth, "generate_password_reset_link", return_value="https://x/reset") as gen:
            assert run(firebase_accounts.make_password_reset_link("a@b.com")) == "https://x/reset"
        gen.assert_called_once_with("a@b.com")


def fake_http(status, body=None, error=None):
    """Patch httpx.AsyncClient.post to return a canned Firebase REST response."""
    async def post(self, url, params=None, json=None):
        if error:
            raise error
        return httpx.Response(status, json=body)
    return patch.object(httpx.AsyncClient, "post", post)


class TestSignInWithPassword:
    def test_success_returns_the_tokens(self):
        body = {"idToken": "tok", "refreshToken": "ref", "expiresIn": "3600", "localId": "uid-1"}
        with fake_http(200, body):
            assert run(firebase_accounts.sign_in_with_password("a@b.com", "Pw!")) == body

    @pytest.mark.parametrize("message", ["INVALID_PASSWORD", "EMAIL_NOT_FOUND", "INVALID_LOGIN_CREDENTIALS", "SOMETHING_ELSE"])
    def test_every_bad_login_looks_the_same(self, message):
        with fake_http(400, {"error": {"message": message}}):
            with pytest.raises(InvalidCredentialsError) as exc_info:
                run(firebase_accounts.sign_in_with_password("a@b.com", "x"))
        assert exc_info.value.detail == "Invalid email or password"

    def test_a_disabled_account(self):
        with fake_http(400, {"error": {"message": "USER_DISABLED"}}):
            with pytest.raises(AccountDisabledError):
                run(firebase_accounts.sign_in_with_password("a@b.com", "x"))

    def test_firebase_throttling_is_a_429(self):
        with fake_http(400, {"error": {"message": "TOO_MANY_ATTEMPTS_TRY_LATER : ..."}}):
            with pytest.raises(AccountLockedError):
                run(firebase_accounts.sign_in_with_password("a@b.com", "x"))

    def test_an_unreachable_firebase_is_a_503(self):
        with fake_http(200, error=httpx.ConnectError("down")):
            with pytest.raises(firebase_accounts.FirebaseUnavailableError) as exc_info:
                run(firebase_accounts.sign_in_with_password("a@b.com", "x"))
        assert exc_info.value.status_code == 503


# ------------------------------------------------------------- password change

def make_user(**overrides):
    fields = dict(
        id="user-1", role=UserRole.AGENT, auth_provider=AuthProvider.PHONE_NUMBER,
        password=generate_hash(OLD, rounds=4), token_version=2, must_change_password=True,
        email_address="a@b.com", firebase_uid=None, status=AccountStatus.ACTIVE,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def change(user, **data):
    session = AsyncMock()
    session.add = MagicMock()
    body = {"current_password": OLD, "new_password": NEW, "confirm_password": NEW, **data}
    with patch.object(password_service, "find_user_by_id", AsyncMock(return_value=user)):
        result = run(password_service.change_password(session, "user-1", PasswordChange(**body), "1.2.3.4", "/x"))
    return result, session


class TestChangePasswordPhoneAccount:
    def test_replaces_the_password_and_clears_the_temporary_flag(self):
        user = make_user()

        result, session = change(user)

        assert result["status"] is True
        assert compare_hash(NEW, user.password)
        assert user.must_change_password is False
        session.commit.assert_awaited()

    def test_ends_every_other_session(self):
        user = make_user(token_version=2)
        change(user)
        assert user.token_version == 3

    def test_a_wrong_current_password_changes_nothing(self):
        user = make_user()
        original = user.password
        with pytest.raises(InvalidCredentialsError, match="Current password"):
            change(user, current_password="Wr0ng!Passw0rd")
        assert user.password == original
        assert user.must_change_password is True

    def test_the_new_password_must_differ(self):
        with pytest.raises(PasswordReuseError):
            change(make_user(), new_password=OLD, confirm_password=OLD)

    def test_the_confirmation_must_match(self):
        with pytest.raises(UnmatchedPassword):
            change(make_user(), confirm_password="Different!Passw0rd3")

    def test_an_admin_needs_a_longer_password(self):
        short = "Sh0rt!Pw1"
        with pytest.raises(WeakPasswordError):
            change(make_user(role=UserRole.ADMIN), new_password=short, confirm_password=short)

    def test_the_outcome_is_logged(self):
        _, session = change(make_user())
        entry = session.add.call_args.args[0]
        assert entry.action == "change_password" and entry.description == "Password changed"
        assert entry.ip_address == "1.2.3.4"

    def test_a_failure_is_logged_too(self):
        user = make_user()
        session = AsyncMock()
        session.add = MagicMock()
        with patch.object(password_service, "find_user_by_id", AsyncMock(return_value=user)):
            with pytest.raises(AuthError):
                run(password_service.change_password(
                    session, "user-1",
                    PasswordChange(current_password="Wr0ng!Passw0rd", new_password=NEW, confirm_password=NEW),
                ))
        assert "failed" in session.add.call_args.args[0].description.lower()


class TestChangePasswordFirebaseAccount:
    def firebase_user(self):
        return make_user(auth_provider=AuthProvider.FIREBASE_SIMPLE, password=None, firebase_uid="uid-1")

    def test_verifies_the_current_password_with_firebase_then_sets_the_new_one(self):
        user = self.firebase_user()
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value={})) as verify, \
             patch.object(firebase_accounts, "set_account_password", AsyncMock()) as setter:
            change(user)
        verify.assert_awaited_once_with("a@b.com", OLD)
        setter.assert_awaited_once_with("uid-1", NEW)
        assert user.must_change_password is False

    def test_a_wrong_current_password_is_refused_and_nothing_is_set(self):
        user = self.firebase_user()
        with patch.object(firebase_accounts, "sign_in_with_password",
                          AsyncMock(side_effect=InvalidCredentialsError("Invalid email or password"))), \
             patch.object(firebase_accounts, "set_account_password", AsyncMock()) as setter:
            with pytest.raises(InvalidCredentialsError, match="Current password"):
                change(user)
        setter.assert_not_awaited()
        assert user.must_change_password is True

    def test_google_accounts_have_no_password_to_change(self):
        user = make_user(auth_provider=AuthProvider.FIREBASE_GOOGLE, password=None, firebase_uid="uid-1")
        with pytest.raises(AuthError, match="Google"):
            change(user)


# ----------------------------------------------------------------- email login

def login(email="a@b.com", password="Pw!", ip="1.2.3.4"):
    session = AsyncMock()
    session.add = MagicMock()
    return run(email_login_service.email_login(session, EmailLogin(email=email, password=password), ip, "/login")), session


@pytest.fixture
def user_lookup():
    user = SimpleNamespace(id="user-1", role=UserRole.AGENT, status=AccountStatus.ACTIVE, must_change_password=True)
    with patch.object(email_login_service, "find_user_by_firebase_uid", AsyncMock(return_value=user)) as find:
        yield SimpleNamespace(user=user, find=find)


SIGN_IN = {"idToken": "tok", "refreshToken": "ref", "expiresIn": "3600", "localId": "uid-1"}


class TestEmailLogin:
    def test_returns_the_token_role_and_temporary_flag(self, redis, user_lookup):
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value=SIGN_IN)):
            result, _ = login()
        assert result["token"] == "tok"
        assert result["role"] == "Agent"
        assert result["must_change_password"] is True
        assert result["refresh_token"] == "ref"

    def test_the_email_is_matched_case_insensitively(self, redis, user_lookup):
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value=SIGN_IN)) as verify:
            login(email="A@B.com")
        assert verify.await_args.args[0] == "a@b.com"

    def test_a_wrong_password_counts_toward_the_lockout(self, redis, user_lookup):
        with patch.object(firebase_accounts, "sign_in_with_password",
                          AsyncMock(side_effect=InvalidCredentialsError("Invalid email or password"))):
            for _ in range(5):
                with pytest.raises(InvalidCredentialsError):
                    login()
            with pytest.raises(AccountLockedError):
                login()

    def test_a_locked_email_never_reaches_firebase(self, redis, user_lookup):
        for _ in range(5):
            run(login_lockout.record_failure("a@b.com", "1.2.3.4"))
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock()) as verify:
            with pytest.raises(AccountLockedError):
                login()
        verify.assert_not_awaited()

    def test_a_firebase_account_we_do_not_know_is_refused(self, redis, user_lookup):
        user_lookup.find.return_value = None
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value=SIGN_IN)):
            with pytest.raises(InvalidCredentialsError):
                login()

    def test_a_disabled_account_is_refused(self, redis, user_lookup):
        user_lookup.user.status = AccountStatus.DISABLE
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value=SIGN_IN)):
            with pytest.raises(AccountDisabledError):
                login()

    def test_success_clears_earlier_failures_and_is_logged(self, redis, user_lookup):
        run(login_lockout.record_failure("a@b.com", "1.2.3.4"))
        with patch.object(firebase_accounts, "sign_in_with_password", AsyncMock(return_value=SIGN_IN)):
            _, session = login()
        assert login_lockout.phone_key("a@b.com") not in redis.values
        assert session.add.call_args.args[0].description == "Login successful"


class TestSchemas:
    def test_a_weak_new_password_is_refused(self):
        for weak in ("short", "alllowercase1!", "NOLOWERCASE1!", "NoDigits!!!!!", "NoSpecial1234"):
            with pytest.raises(ValidationError):
                PasswordChange(current_password="x", new_password=weak, confirm_password=weak)
