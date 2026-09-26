import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config.setting import Settings
from app.database.database import get_session
from app.model.mod_enum import DocumentType, UserAccountStatus, UserRole
from app.routers.more_about_user.more_about_user_router import router
from app.schema import role as role_module
from app.schema.email_auth_schema import User_signup_schema
from app.schema.phone_auth_schema import PhoneNumberSignup
from app.model.mod_enum import AccountStatus
from app.services.auth import current_user
from app.services.auth.authorization import get_role
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler
from app.services.auth.phone.token.jwt import JwtManagement
from app.services.reference import country_service

BASE = "/api/v1/more_about_user"
CLIENT, AGENT, BROKER, FUND = (
    UserRole.CLIENT, UserRole.AGENT, UserRole.BROKER, UserRole.FUNDMANAGER,
)


# What the fake database knows: user_id -> (role, status). The gate takes the
# role from here, exactly as it does from the Users table in production.
ACCOUNTS: dict = {}


def make_token(user_id: str, role) -> str:
    """A real token, as PhoneAuthentication.login issues it, for an account that exists."""
    ACCOUNTS[user_id] = (UserRole(role) if role in [r.value for r in UserRole] else role, AccountStatus.ACTIVE)
    return asyncio.run(JwtManagement().encode_auth(
        {"user_id": user_id, "firebase_uid": None, "role": role, "token_version": 0}
    ))


@pytest.fixture(autouse=True)
def fake_accounts(monkeypatch):
    ACCOUNTS.clear()

    async def fake_find_user_by_id(session, user_id):
        if user_id not in ACCOUNTS:
            return None
        role, account_status = ACCOUNTS[user_id]
        return SimpleNamespace(
            id=user_id, role=role, status=account_status, token_version=0, firebase_uid=None,
            must_change_password=False,
        )

    async def fake_has_agent_profile(session, user_id):
        return True

    monkeypatch.setattr(current_user, "find_user_by_id", fake_find_user_by_id)
    monkeypatch.setattr(current_user, "has_agent_profile", fake_has_agent_profile)


def auth(user_id: str, role) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role)}"}


def make_profile_row(user_id: str = "user123") -> SimpleNamespace:
    return SimpleNamespace(
        id="profile-1", user_id=user_id, first_name="Test", last_name="User",
        middle_name=None, date_of_birth=datetime(1990, 5, 1), nationality="UG",
        country_of_residence="UG", city_of_residence="KAMPALA", full_address="plot 1",
        profession="ENGINEER", document_type=DocumentType.NATIONAL_ID,
        document_id="CM123", document_link="https://files.example.com/doc.pdf",
        document_expiry_date=date(2030, 1, 1),
        profile_picture="https://files.example.com/pic.png",
        profile_verification_complete=UserAccountStatus.PENDING,
        reviewer_id=None, reviewed_by=None, reviewed_at=None, review_comment=None,
        updated_at=datetime.now(timezone.utc),
    )


def make_session(profile) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = profile
    result.scalars.return_value.all.return_value = [profile] if profile else []
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(
        country_service, "get_settings", lambda: SimpleNamespace(SERVED_COUNTRIES=["UG"])
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)

    profile = make_profile_row("user123")
    session = make_session(profile)
    app.dependency_overrides[get_session] = lambda: session
    return SimpleNamespace(client=TestClient(app), profile=profile, session=session, app=app)


# ---------------------------------------------------------------------------
# The role claim in real tokens
# ---------------------------------------------------------------------------

class TestRoleClaim:
    @pytest.mark.parametrize("role", [CLIENT, AGENT, BROKER, FUND])
    def test_role_survives_a_token_round_trip(self, role):
        payload = asyncio.run(JwtManagement().decode_auth(make_token("u1", role)))
        assert get_role(payload) == role

    @pytest.mark.parametrize("payload", [{}, {"role": None}, {"role": "Superuser"}, {"role": ""}])
    def test_missing_or_unknown_role_is_none(self, payload):
        assert get_role(payload) is None


# ---------------------------------------------------------------------------
# Authentication comes first
# ---------------------------------------------------------------------------

class TestAuthentication:
    @pytest.mark.parametrize("method,path", [("get", "/user123"), ("put", "/user123")])
    def test_no_token_is_rejected(self, api, method, path):
        assert getattr(api.client, method)(BASE + path).status_code in (401, 403)

    def test_garbage_token_is_401(self, api):
        response = api.client.get(BASE + "/user123", headers={"Authorization": "Bearer not-a-real-token"})
        assert response.status_code == 401

    def test_an_admin_account_has_no_access_to_client_profiles(self, api):
        """Admins manage accounts; they don't read client KYC."""
        response = api.client.get(BASE + "/user123", headers=auth("boss1", UserRole.ADMIN))
        assert response.status_code == 403

    def test_the_role_comes_from_the_database_not_the_token(self, api):
        """A token that claims Agent for an account that is really a client gets client rights."""
        ACCOUNTS["user123"] = (CLIENT, AccountStatus.ACTIVE)
        token = asyncio.run(JwtManagement().encode_auth(
            {"user_id": "user123", "role": "Agent", "token_version": 0}
        ))
        # a client reading someone else's profile is refused, whatever the token claims
        response = api.client.get(BASE + "/somebody-else", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403

    def test_a_disabled_account_is_locked_out_immediately(self, api):
        headers = auth("user123", CLIENT)
        ACCOUNTS["user123"] = (CLIENT, AccountStatus.DISABLE)
        assert api.client.get(BASE + "/user123", headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# GET /{user_id} - a client reads their own profile
# ---------------------------------------------------------------------------

class TestReadProfile:
    def test_client_reads_their_own(self, api):
        response = api.client.get(BASE + "/user123", headers=auth("user123", CLIENT))
        assert response.status_code == 200
        assert response.json()["user_id"] == "user123"

    def test_the_review_status_and_comment_are_visible_to_the_client(self, api):
        api.profile.profile_verification_complete = UserAccountStatus.REJECTED
        api.profile.review_comment = "ID photo is blurry"
        body = api.client.get(BASE + "/user123", headers=auth("user123", CLIENT)).json()
        assert body["profile_verification_complete"] == "rejected"
        assert body["review_comment"] == "ID photo is blurry"

    def test_client_cannot_read_someone_elses(self, api):
        response = api.client.get(BASE + "/user123", headers=auth("intruder", CLIENT))
        assert response.status_code == 403

    @pytest.mark.parametrize("role", [AGENT, FUND, BROKER, UserRole.ADMIN])
    def test_agents_and_admins_do_not_use_this_endpoint(self, api, role):
        """Reviewers load profiles through /api/v1/reviews instead."""
        assert api.client.get(BASE + "/user123", headers=auth("other1", role)).status_code == 403


# ---------------------------------------------------------------------------
# PUT /{user_id} - a client edits their own profile (same shape as create)
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PDF = b"%PDF-1.7\n" + b"x" * 32


@pytest.fixture
def uploads(monkeypatch):
    """Replace the bucket upload, and record what would have been uploaded."""
    target = "app.routers.more_about_user.more_about_user_router.upload"
    with patch(target, AsyncMock(side_effect=lambda f, **kw: f"https://files.example.com/{kw['folder_name']}/new")) as up:
        yield up


class TestUpdateProfile:
    def put(self, api, user="user123", role=CLIENT, **kwargs):
        return api.client.put(BASE + "/user123", headers=auth(user, role), **kwargs)

    def test_client_updates_their_own(self, api):
        response = self.put(api, data={"first_name": "Bob"})
        assert response.status_code == 200
        assert api.profile.first_name == "Bob"

    def test_it_is_normalised_like_create(self, api):
        self.put(api, data={"city_of_residence": "Entebbe", "full_address": "Plot 7 Main ST", "profession": "nurse"})
        assert api.profile.city_of_residence == "ENTEBBE"
        assert api.profile.full_address == "plot 7 main st"
        assert api.profile.profession == "NURSE"

    def test_only_what_is_sent_changes(self, api):
        self.put(api, data={"first_name": "Bob"})
        assert api.profile.last_name == "User" and api.profile.profession == "ENGINEER"

    def test_blank_fields_mean_not_provided(self, api):
        response = self.put(api, data={"first_name": "Bob", "profession": "", "date_of_birth": ""})
        assert response.status_code == 200
        assert api.profile.profession == "ENGINEER"

    def test_client_cannot_update_someone_elses(self, api):
        response = self.put(api, user="intruder", data={"first_name": "Bob"})
        assert response.status_code == 403
        assert api.profile.first_name == "Test"
        api.session.commit.assert_not_called()

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND, UserRole.ADMIN])
    def test_non_clients_cannot_use_the_client_update(self, api, role):
        assert self.put(api, user="other1", role=role, data={"first_name": "Bob"}).status_code == 403

    def test_client_cannot_approve_their_own_profile_or_swap_links(self, api):
        """Review fields and file links are not accepted as form fields at all."""
        response = self.put(api, data={
            "first_name": "Bob",
            "profile_verification_complete": "approved",
            "review_comment": "looks good",
            "reviewer_id": "me", "reviewed_by": "me",
            "document_link": "https://evil.example.com/x.pdf",
            "profile_picture_link": "https://evil.example.com/y.png",
        })

        assert response.status_code == 200
        assert api.profile.first_name == "Bob"
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING
        assert api.profile.review_comment is None
        assert api.profile.reviewer_id is None and api.profile.reviewed_by is None
        assert api.profile.document_link == "https://files.example.com/doc.pdf"

    def test_residence_must_still_be_a_served_country(self, api):
        assert self.put(api, data={"country_of_residence": "KE"}).status_code == 403

    def test_sending_nothing_is_a_400(self, api):
        response = self.put(api, data={})
        assert response.status_code == 400
        assert "nothing to update" in response.json()["detail"].lower()

    def test_a_bad_value_is_a_422_like_everywhere_else(self, api):
        response = self.put(api, data={"date_of_birth": "not-a-date"})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"][:2] == ["body", "date_of_birth"]

    def test_a_missing_profile_is_404(self, api):
        api.app.dependency_overrides[get_session] = lambda: make_session(None)
        assert self.put(api, data={"first_name": "Bob"}).status_code == 404


class TestEditingReopensTheReview:
    @pytest.mark.parametrize("decided", [UserAccountStatus.APPROVED, UserAccountStatus.REJECTED])
    def test_editing_a_decided_profile_sends_it_back_to_pending(self, api, decided):
        api.profile.profile_verification_complete = decided
        api.profile.review_comment = "earlier reason"

        response = api.client.put(BASE + "/user123", headers=auth("user123", CLIENT), data={"first_name": "Bob"})

        assert response.status_code == 200
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING
        assert response.json()["profile_verification_complete"] == "pending"
        assert api.profile.review_comment == "earlier reason"   # stays visible until the next decision

    def test_the_users_own_status_follows(self, api):
        api.profile.profile_verification_complete = UserAccountStatus.REJECTED
        account = SimpleNamespace(profile_verification_complete=UserAccountStatus.REJECTED)
        api.session.get = AsyncMock(return_value=account)

        api.client.put(BASE + "/user123", headers=auth("user123", CLIENT), data={"first_name": "Bob"})

        assert account.profile_verification_complete == UserAccountStatus.PENDING

    def test_a_pending_profile_stays_pending(self, api):
        api.session.get = AsyncMock()
        api.client.put(BASE + "/user123", headers=auth("user123", CLIENT), data={"first_name": "Bob"})
        api.session.get.assert_not_called()

    def test_a_rejected_request_does_not_reopen_anything(self, api):
        api.profile.profile_verification_complete = UserAccountStatus.APPROVED
        api.client.put(BASE + "/user123", headers=auth("user123", CLIENT), data={"country_of_residence": "KE"})
        assert api.profile.profile_verification_complete == UserAccountStatus.APPROVED


class TestUpdateFiles:
    def put(self, api, files, data=None):
        return api.client.put(BASE + "/user123", headers=auth("user123", CLIENT), data=data or {}, files=files)

    def test_a_new_document_replaces_the_link(self, api, uploads):
        response = self.put(api, {"id_document": ("id.pdf", PDF, "application/pdf")})
        assert response.status_code == 200
        assert api.profile.document_link == "https://files.example.com/about/new"
        assert api.profile.profile_picture == "https://files.example.com/pic.png"
        assert uploads.await_args.kwargs["folder_name"] == "about" and uploads.await_args.kwargs["username"] == "user123"

    def test_a_new_picture_replaces_the_link(self, api, uploads):
        self.put(api, {"profile_picture": ("me.png", PNG, "image/png")})
        assert api.profile.profile_picture == "https://files.example.com/profile/new"
        assert api.profile.document_link == "https://files.example.com/doc.pdf"

    def test_files_alone_count_as_an_update_and_reopen_the_review(self, api, uploads):
        api.profile.profile_verification_complete = UserAccountStatus.REJECTED
        assert self.put(api, {"id_document": ("id.pdf", PDF, "application/pdf")}).status_code == 200
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING

    def test_a_bad_file_is_refused_and_nothing_is_uploaded(self, api, uploads):
        response = self.put(api, {"id_document": ("evil.exe", b"MZ" + b"\x00" * 32, "application/octet-stream")})
        assert response.status_code in (400, 413, 415)
        uploads.assert_not_awaited()
        assert api.profile.document_link == "https://files.example.com/doc.pdf"

    def test_a_pdf_is_not_a_valid_profile_picture(self, api, uploads):
        response = self.put(api, {"profile_picture": ("me.pdf", PDF, "application/pdf")})
        assert response.status_code in (400, 415)
        uploads.assert_not_awaited()

    def test_nothing_is_uploaded_when_the_update_would_be_refused_anyway(self, api, uploads):
        response = self.put(api, {"id_document": ("id.pdf", PDF, "application/pdf")}, data={"country_of_residence": "KE"})
        assert response.status_code == 403
        uploads.assert_not_awaited()

    def test_an_empty_file_part_is_ignored(self, api, uploads):
        response = self.put(api, {"id_document": ("", b"", "application/octet-stream")}, data={"first_name": "Bob"})
        assert response.status_code == 200
        uploads.assert_not_awaited()


# ---------------------------------------------------------------------------
# There is no delete, no list and no review on the client's router any more
# ---------------------------------------------------------------------------

class TestRemovedEndpoints:
    @pytest.mark.parametrize("role", [CLIENT, AGENT, UserRole.ADMIN])
    def test_delete_is_gone_for_everybody(self, api, role):
        response = api.client.delete(BASE + "/user123", headers=auth("user123", role))
        assert response.status_code == 405
        assert api.profile is not None
        api.session.delete.assert_not_called()

    def test_reviewing_is_not_offered_here(self, api):
        response = api.client.put(
            BASE + "/user123/review", headers=auth("other1", AGENT),
            json={"decision": "approved", "comment": "fine"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Create is for clients only (the full flow is covered in test_more_about_user)
# ---------------------------------------------------------------------------

class TestCreateRoleGuard:
    FORM = {
        "first_name": "T", "last_name": "U", "date_of_birth": "1990-05-01",
        "nationality": "UG", "country_of_residence": "UG", "city_of_residence": "K",
        "full_address": "x", "profession": "x", "document_type": "national_id",
        "document_id": "1", "document_expiry_date": "2030-01-01",
    }

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND, UserRole.ADMIN])
    def test_non_clients_cannot_create_a_client_profile(self, api, role):
        response = api.client.post(
            BASE + "/create",
            data=self.FORM,
            files={
                "id_document": ("id.pdf", b"%PDF-1.7 x", "application/pdf"),
                "profile_picture": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
            },
            headers=auth("other1", role),
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Self-registration is limited to the configured roles (client only by default)
# ---------------------------------------------------------------------------

@pytest.fixture
def self_registration(monkeypatch):
    def allow(roles: list[str]) -> None:
        monkeypatch.setattr(
            role_module, "get_settings", lambda: SimpleNamespace(SELF_REGISTRATION_ROLES=roles)
        )
    return allow


PHONE_SIGNUP = {
    "phone_number": "+250781234567", "password": "Str0ng!Passw0rd",
    "confirm_password": "Str0ng!Passw0rd",
}


class TestSelfRegistrationRoles:
    def test_a_client_can_register(self, self_registration):
        self_registration(["User"])
        assert PhoneNumberSignup(**PHONE_SIGNUP, role=CLIENT).role == CLIENT

    @pytest.mark.parametrize("role", [AGENT, FUND, BROKER])
    def test_agent_and_other_roles_cannot_be_self_assigned_by_phone_signup(self, self_registration, role):
        self_registration(["User"])
        with pytest.raises(ValidationError, match="self-registration"):
            PhoneNumberSignup(**PHONE_SIGNUP, role=role)

    @pytest.mark.parametrize("role", [AGENT, FUND, BROKER])
    def test_agent_and_other_roles_cannot_be_self_assigned_by_firebase_signup(self, self_registration, role):
        self_registration(["User"])
        with pytest.raises(ValidationError, match="self-registration"):
            User_signup_schema(phone_number="+250781234567", role=role)

    def test_config_can_open_more_roles(self, self_registration):
        self_registration(["User", "Agent"])
        assert PhoneNumberSignup(**PHONE_SIGNUP, role=AGENT).role == AGENT
        with pytest.raises(ValidationError):
            PhoneNumberSignup(**PHONE_SIGNUP, role=BROKER)

    def test_default_setting_is_client_only(self):
        assert Settings.model_fields["SELF_REGISTRATION_ROLES"].default == ["User"]


class TestSelfRegistrationSettingParsing:
    parse = staticmethod(Settings.parse_self_registration_roles)

    def test_comma_separated_string_becomes_a_list(self):
        assert self.parse("User, Agent ,User") == ["User", "Agent"]

    def test_empty_means_nobody_can_self_register(self):
        assert self.parse("") == []

    def test_admin_can_never_be_opened_to_self_registration(self):
        with pytest.raises(ValueError, match="Admin"):
            self.parse("User,Admin")

    def test_unknown_role_fails_fast(self):
        with pytest.raises(ValueError, match="Superuser"):
            self.parse("User,Superuser")
