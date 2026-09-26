import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config.setting import get_settings
from app.database.database import get_session
from app.model.mod_enum import AccountStatus, AuthProvider, UserRole
from app.services.auth import current_user
from app.services.auth.current_user import get_current_user, get_current_user_any_state
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler
from app.services.auth.phone.token.jwt import JwtManagement
from app.services.auth.sessions import revoke_sessions


def make_user(**overrides) -> SimpleNamespace:
    fields = dict(
        id="user-1", role=UserRole.CLIENT, status=AccountStatus.ACTIVE,
        token_version=0, firebase_uid=None, email_address="a@example.com",
        auth_provider=AuthProvider.PHONE_NUMBER, must_change_password=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def own_token(user_id="user-1", token_version=0, role="User", **claims) -> str:
    return asyncio.run(JwtManagement().encode_auth(
        {"user_id": user_id, "role": role, "token_version": token_version, **claims}
    ))


# A throwaway RSA key so tokens carry a genuine RS256 header, like Firebase's.
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PEM = RSA_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def firebase_style_token() -> str:
    return pyjwt.encode({"sub": "fb-uid"}, RSA_PEM, algorithm="RS256")


@pytest.fixture
def world(monkeypatch):
    """A tiny app behind the gate, with the database lookups replaced."""
    state = SimpleNamespace(
        users={}, by_uid={}, unlinked={}, without_profile=set(), session=AsyncMock(),
        firebase_result={"uid": "fb-uid", "email": "a@example.com", "email_verified": True},
        firebase_error=None,
    )

    async def find_user_by_id(session, user_id):
        return state.users.get(user_id)

    async def find_user_by_firebase_uid(session, uid):
        return state.by_uid.get(uid)

    async def find_unlinked_user_by_email(session, email):
        return state.unlinked.get(email)

    async def has_agent_profile(session, user_id):
        return user_id not in state.without_profile

    def verify_id_token(token):
        if state.firebase_error:
            raise state.firebase_error
        return state.firebase_result

    monkeypatch.setattr(current_user, "find_user_by_id", find_user_by_id)
    monkeypatch.setattr(current_user, "find_user_by_firebase_uid", find_user_by_firebase_uid)
    monkeypatch.setattr(current_user, "has_agent_profile", has_agent_profile)
    monkeypatch.setattr(current_user, "find_unlinked_user_by_email", find_unlinked_user_by_email)
    monkeypatch.setattr(current_user.firebase_auth, "verify_id_token", verify_id_token)

    app = FastAPI()
    app.add_exception_handler(AuthError, auth_error_handler)

    def readable(who: dict) -> dict:
        return {**who, "role": who["role"].value}

    @app.get("/me")
    async def me(who: dict = Depends(get_current_user)):
        return readable(who)

    @app.get("/me/any-state")
    async def me_any_state(who: dict = Depends(get_current_user_any_state)):
        return readable(who)

    app.dependency_overrides[get_session] = lambda: state.session
    state.client = TestClient(app)
    state.get = lambda token: state.client.get("/me", headers={"Authorization": f"Bearer {token}"})
    return state


class TestOwnTokens:
    def test_a_valid_token_resolves_to_the_account(self, world):
        world.users["user-1"] = make_user(role=UserRole.AGENT)

        response = world.get(own_token())

        assert response.status_code == 200
        assert response.json() == {
            "user_id": "user-1", "role": "Agent", "firebase_uid": None, "must_change_password": False,
        }

    def test_no_token_is_rejected(self, world):
        assert world.client.get("/me").status_code in (401, 403)

    def test_garbage_is_401(self, world):
        assert world.get("not-a-token").status_code == 401

    def test_an_expired_token_is_401(self, world):
        world.users["user-1"] = make_user()
        token = asyncio.run(JwtManagement().encode_auth(
            {"user_id": "user-1", "token_version": 0}, expires_delta=timedelta(seconds=-5)
        ))
        response = world.get(token)
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    def test_a_token_for_an_account_that_no_longer_exists_is_401(self, world):
        assert world.get(own_token("ghost")).status_code == 401

    def test_a_token_without_a_user_id_is_401(self, world):
        token = asyncio.run(JwtManagement().encode_auth({"role": "User"}))
        assert world.get(token).status_code == 401

    def test_wrong_secret_is_401(self, world):
        world.users["user-1"] = make_user()
        forged = pyjwt.encode(
            {"user_id": "user-1", "token_version": 0}, "x" * 40, algorithm=get_settings().ALGORITHM
        )
        assert world.get(forged).status_code == 401


class TestAlgorithmPinning:
    """The token header is used to route the token, never to choose how it's checked."""

    def test_an_unsigned_token_is_rejected(self, world):
        world.users["user-1"] = make_user()
        unsigned = pyjwt.encode({"user_id": "user-1", "token_version": 0}, key=None, algorithm="none")
        assert world.get(unsigned).status_code == 401

    def test_a_token_signed_with_another_hmac_algorithm_is_rejected(self, world):
        """Correct secret, but not the algorithm we issue tokens with."""
        world.users["user-1"] = make_user()
        secret = get_settings().SECRET_ACCESS_KEY
        other = "HS512" if get_settings().ALGORITHM != "HS512" else "HS384"
        forged = pyjwt.encode({"user_id": "user-1", "token_version": 0}, secret, algorithm=other)
        assert world.get(forged).status_code == 401

    def test_an_rs256_token_is_only_ever_checked_by_firebase(self, world):
        """A forged RS256 token can't be passed off as one of ours; Firebase rejects it."""
        world.users["user-1"] = make_user()
        world.firebase_error = ValueError("bad signature")
        assert world.get(firebase_style_token()).status_code == 401


class TestRevocation:
    def test_bumping_the_token_version_kills_older_tokens(self, world):
        user = make_user(token_version=0)
        world.users["user-1"] = user
        token = own_token(token_version=0)
        assert world.get(token).status_code == 200

        user.token_version = 1  # what a password change or logout-everywhere does

        response = world.get(token)
        assert response.status_code == 401
        assert "log in again" in response.json()["detail"].lower()
        assert world.get(own_token(token_version=1)).status_code == 200

    def test_a_token_without_a_version_only_works_at_version_zero(self, world):
        world.users["user-1"] = make_user(token_version=0)
        old = asyncio.run(JwtManagement().encode_auth({"user_id": "user-1"}))
        assert world.get(old).status_code == 200

        world.users["user-1"].token_version = 2
        assert world.get(old).status_code == 401

    def test_revoke_sessions_bumps_the_version_in_one_update(self):
        session = AsyncMock()
        asyncio.run(revoke_sessions(session, "user-1"))

        statement = str(session.execute.await_args.args[0].compile())
        assert "token_version" in statement and "+" in statement
        session.commit.assert_awaited_once()


class TestAccountState:
    @pytest.mark.parametrize("state", [AccountStatus.ACTIVE, AccountStatus.REACTIVE])
    def test_active_and_reactivated_accounts_get_in(self, world, state):
        world.users["user-1"] = make_user(status=state)
        assert world.get(own_token()).status_code == 200

    def test_a_disabled_account_is_refused_with_a_valid_token(self, world):
        world.users["user-1"] = make_user(status=AccountStatus.DISABLE)
        response = world.get(own_token())
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    def test_a_role_change_applies_to_tokens_already_issued(self, world):
        user = make_user(role=UserRole.CLIENT)
        world.users["user-1"] = user
        token = own_token(role="User")
        assert world.get(token).json()["role"] == "User"

        user.role = UserRole.AGENT

        assert world.get(token).json()["role"] == "Agent"

    def test_the_role_claim_inside_the_token_is_ignored(self, world):
        world.users["user-1"] = make_user(role=UserRole.CLIENT)
        assert world.get(own_token(role="Admin")).json()["role"] == "User"


class TestFirebaseTokens:
    def test_a_known_firebase_account_resolves(self, world):
        world.by_uid["fb-uid"] = make_user(
            id="fb-user", firebase_uid="fb-uid", auth_provider=AuthProvider.FIREBASE_GOOGLE
        )

        response = world.get(firebase_style_token())

        assert response.status_code == 200
        body = response.json()
        assert (body["user_id"], body["role"], body["firebase_uid"]) == ("fb-user", "User", "fb-uid")

    def test_an_invalid_firebase_token_is_401(self, world):
        world.firebase_error = ValueError("expired")
        assert world.get(firebase_style_token()).status_code == 401

    def test_a_firebase_token_without_a_uid_is_401(self, world):
        world.firebase_result = {"email": "a@example.com", "email_verified": True}
        assert world.get(firebase_style_token()).status_code == 401

    def test_a_firebase_account_with_no_row_is_401(self, world):
        response = world.get(firebase_style_token())
        assert response.status_code == 401
        assert "not found" in response.json()["detail"].lower()

    def test_a_disabled_firebase_account_is_refused(self, world):
        world.by_uid["fb-uid"] = make_user(firebase_uid="fb-uid", status=AccountStatus.DISABLE)
        assert world.get(firebase_style_token()).status_code == 403


class TestFirstFirebaseSignIn:
    """An account created ahead of time (an agent created by an admin) is linked on first sign-in."""

    def test_it_links_through_a_verified_matching_email(self, world):
        invited = make_user(
            id="agent-1", role=UserRole.AGENT, firebase_uid=None,
            auth_provider=AuthProvider.FIREBASE_GOOGLE,
        )
        world.unlinked["a@example.com"] = invited

        response = world.get(firebase_style_token())

        assert response.status_code == 200
        assert response.json()["user_id"] == "agent-1"
        assert invited.firebase_uid == "fb-uid"
        world.session.commit.assert_awaited()

    def test_an_unverified_email_never_links(self, world):
        invited = make_user(firebase_uid=None, auth_provider=AuthProvider.FIREBASE_GOOGLE)
        world.unlinked["a@example.com"] = invited
        world.firebase_result = {"uid": "fb-uid", "email": "a@example.com", "email_verified": False}

        assert world.get(firebase_style_token()).status_code == 401
        assert invited.firebase_uid is None

    def test_a_token_without_an_email_never_links(self, world):
        invited = make_user(firebase_uid=None)
        world.unlinked["a@example.com"] = invited
        world.firebase_result = {"uid": "fb-uid", "email_verified": True}

        assert world.get(firebase_style_token()).status_code == 401
        assert invited.firebase_uid is None

    def test_a_different_email_never_links(self, world):
        invited = make_user(firebase_uid=None)
        world.unlinked["a@example.com"] = invited
        world.firebase_result = {"uid": "fb-uid", "email": "other@example.com", "email_verified": True}

        assert world.get(firebase_style_token()).status_code == 401
        assert invited.firebase_uid is None

    def test_the_lookup_only_matches_unlinked_firebase_provider_accounts(self):
        """Phone-registered accounts (unverified email) and already-linked accounts must not match."""
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        asyncio.run(current_user.find_unlinked_user_by_email(session, "a@example.com"))

        sql = str(session.execute.await_args.args[0].compile()).lower()
        assert "firebase_uid is null" in sql
        assert "auth_provider in" in sql


class TestTemporaryPassword:
    def test_an_account_on_a_temporary_password_is_held_at_the_gate(self, world):
        world.users["user-1"] = make_user(must_change_password=True)

        response = world.get(own_token())

        assert response.status_code == 403
        assert "temporary password" in response.json()["detail"].lower()

    def test_but_it_can_still_reach_the_password_change(self, world):
        world.users["user-1"] = make_user(must_change_password=True)

        response = world.client.get("/me/any-state", headers={"Authorization": f"Bearer {own_token()}"})

        assert response.status_code == 200
        assert response.json()["must_change_password"] is True

    def test_after_the_change_the_gate_opens(self, world):
        user = make_user(must_change_password=True)
        world.users["user-1"] = user
        assert world.get(own_token()).status_code == 403

        user.must_change_password = False

        assert world.get(own_token()).status_code == 200

    def test_a_firebase_account_on_a_temporary_password_is_held_too(self, world):
        world.by_uid["fb-uid"] = make_user(firebase_uid="fb-uid", must_change_password=True)
        assert world.get(firebase_style_token()).status_code == 403


class TestAgentProfile:
    def test_an_agent_with_a_profile_gets_in(self, world):
        world.users["user-1"] = make_user(role=UserRole.AGENT)
        assert world.get(own_token()).status_code == 200

    def test_an_agent_without_a_profile_is_refused(self, world):
        """Being an Agent isn't enough: agents are created by an admin, with a profile."""
        world.users["user-1"] = make_user(role=UserRole.AGENT)
        world.without_profile.add("user-1")

        response = world.get(own_token())

        assert response.status_code == 403
        assert "not fully set up" in response.json()["detail"]

    def test_clients_and_admins_never_need_a_profile(self, world):
        world.users["user-1"] = make_user(role=UserRole.CLIENT)
        world.users["boss"] = make_user(id="boss", role=UserRole.ADMIN)
        world.without_profile.update({"user-1", "boss"})

        assert world.get(own_token()).status_code == 200
        assert world.get(own_token("boss")).status_code == 200

    def test_the_profile_lookup_is_only_made_for_agents(self, world, monkeypatch):
        lookups = []

        async def spy(session, user_id):
            lookups.append(user_id)
            return True

        monkeypatch.setattr(current_user, "has_agent_profile", spy)
        world.users["user-1"] = make_user(role=UserRole.CLIENT)
        world.users["a1"] = make_user(id="a1", role=UserRole.AGENT)

        world.get(own_token())
        world.get(own_token("a1"))

        assert lookups == ["a1"]
