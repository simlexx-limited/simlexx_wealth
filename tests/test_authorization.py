import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
from app.services.auth.authorization import get_role
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler
from app.services.auth.phone.token.jwt import JwtManagement
from app.services.reference import country_service

BASE = "/api/v1/more_about_user"
CLIENT, AGENT, BROKER, FUND = (
    UserRole.CLIENT, UserRole.AGENT, UserRole.BROKER, UserRole.FUNDMANAGER,
)


def make_token(user_id: str, role) -> str:
    """A real token, exactly as PhoneAuthentication.login issues it."""
    return asyncio.run(JwtManagement().encode_auth(
        {"user_id": user_id, "firebase_uid": None, "role": role}
    ))


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
        reviewer_id=None, reviewed_by=None, reviewed_at=None,
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

    @pytest.mark.parametrize("payload", [{}, {"role": None}, {"role": "Admin"}, {"role": ""}])
    def test_missing_or_unknown_role_is_none(self, payload):
        assert get_role(payload) is None


# ---------------------------------------------------------------------------
# Authentication comes first
# ---------------------------------------------------------------------------

class TestAuthentication:
    @pytest.mark.parametrize("method,path", [
        ("get", "/all"), ("get", "/user123"), ("put", "/user123"),
        ("put", "/user123/review"), ("delete", "/user123"),
    ])
    def test_no_token_is_rejected(self, api, method, path):
        assert getattr(api.client, method)(BASE + path).status_code in (401, 403)

    def test_garbage_token_is_401(self, api):
        response = api.client.get(BASE + "/all", headers={"Authorization": "Bearer not-a-real-token"})
        assert response.status_code == 401

    def test_token_with_an_unknown_role_is_403(self, api):
        response = api.client.get(BASE + "/all", headers=auth("u1", "Admin"))
        assert response.status_code == 403

    def test_token_with_no_role_is_403(self, api):
        token = asyncio.run(JwtManagement().encode_auth({"user_id": "u1"}))
        response = api.client.get(BASE + "/all", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /all - reviewers only
# ---------------------------------------------------------------------------

class TestListProfiles:
    @pytest.mark.parametrize("role", [AGENT, BROKER])
    def test_reviewers_can_list(self, api, role):
        response = api.client.get(BASE + "/all", headers=auth("staff1", role))
        assert response.status_code == 200
        assert len(response.json()) == 1

    @pytest.mark.parametrize("role", [CLIENT, FUND])
    def test_clients_and_fund_managers_cannot_list(self, api, role):
        response = api.client.get(BASE + "/all", headers=auth("user123", role))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /{user_id} - own profile, or any profile for reviewers
# ---------------------------------------------------------------------------

class TestReadProfile:
    def test_client_reads_their_own(self, api):
        response = api.client.get(BASE + "/user123", headers=auth("user123", CLIENT))
        assert response.status_code == 200
        assert response.json()["user_id"] == "user123"

    def test_client_cannot_read_someone_elses(self, api):
        response = api.client.get(BASE + "/user123", headers=auth("intruder", CLIENT))
        assert response.status_code == 403

    @pytest.mark.parametrize("role", [AGENT, BROKER])
    def test_reviewers_read_any_profile(self, api, role):
        assert api.client.get(BASE + "/user123", headers=auth("staff1", role)).status_code == 200

    def test_fund_manager_cannot_read_profiles(self, api):
        assert api.client.get(BASE + "/user123", headers=auth("fm1", FUND)).status_code == 403


# ---------------------------------------------------------------------------
# PUT /{user_id} - a client edits their own personal details
# ---------------------------------------------------------------------------

class TestUpdateProfile:
    def test_client_updates_their_own(self, api):
        response = api.client.put(
            BASE + "/user123", json={"profession": "NURSE"}, headers=auth("user123", CLIENT)
        )
        assert response.status_code == 200
        assert api.profile.profession == "NURSE"

    def test_client_cannot_update_someone_elses(self, api):
        response = api.client.put(
            BASE + "/user123", json={"profession": "NURSE"}, headers=auth("intruder", CLIENT)
        )
        assert response.status_code == 403
        assert api.profile.profession == "ENGINEER"
        api.session.commit.assert_not_called()

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND])
    def test_staff_cannot_use_the_client_update(self, api, role):
        response = api.client.put(
            BASE + "/user123", json={"profession": "NURSE"}, headers=auth("staff1", role)
        )
        assert response.status_code == 403

    def test_client_cannot_approve_their_own_profile(self, api):
        """Review fields and file links are not part of the client update at all."""
        response = api.client.put(
            BASE + "/user123",
            json={
                "profession": "NURSE",
                "profile_verification_complete": "approved",
                "reviewer_id": "me",
                "reviewed_by": "me",
                "document_link": "https://evil.example.com/x.pdf",
                "profile_picture": "https://evil.example.com/y.png",
            },
            headers=auth("user123", CLIENT),
        )

        assert response.status_code == 200
        assert api.profile.profession == "NURSE"  # the allowed field was applied
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING
        assert api.profile.reviewer_id is None
        assert api.profile.reviewed_by is None
        assert api.profile.document_link == "https://files.example.com/doc.pdf"
        assert api.profile.profile_picture == "https://files.example.com/pic.png"

    def test_residence_must_still_be_a_served_country(self, api):
        response = api.client.put(
            BASE + "/user123", json={"country_of_residence": "KE"}, headers=auth("user123", CLIENT)
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# PUT /{user_id}/review - reviewers decide
# ---------------------------------------------------------------------------

class TestReviewProfile:
    @pytest.mark.parametrize("role", [AGENT, BROKER])
    @pytest.mark.parametrize("decision", ["approved", "rejected"])
    def test_reviewers_record_a_decision(self, api, role, decision):
        response = api.client.put(
            BASE + "/user123/review",
            json={"profile_verification_complete": decision, "reviewed_by": "Simlexx Ltd"},
            headers=auth("staff1", role),
        )

        assert response.status_code == 200
        assert api.profile.profile_verification_complete == UserAccountStatus(decision)
        assert api.profile.reviewer_id == "staff1"
        assert api.profile.reviewed_by == "Simlexx Ltd"
        assert isinstance(api.profile.reviewed_at, datetime)
        api.session.commit.assert_awaited_once()

    def test_reviewer_identity_comes_from_the_token_not_the_body(self, api):
        api.client.put(
            BASE + "/user123/review",
            json={"profile_verification_complete": "approved", "reviewer_id": "someone-else"},
            headers=auth("staff1", AGENT),
        )
        assert api.profile.reviewer_id == "staff1"

    @pytest.mark.parametrize("role", [CLIENT, FUND])
    def test_clients_and_fund_managers_cannot_review(self, api, role):
        response = api.client.put(
            BASE + "/user123/review",
            json={"profile_verification_complete": "approved"},
            headers=auth("user123", role),
        )
        assert response.status_code == 403
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING

    def test_reviewer_cannot_review_their_own_profile(self, api):
        response = api.client.put(
            BASE + "/user123/review",
            json={"profile_verification_complete": "approved"},
            headers=auth("user123", AGENT),
        )
        assert response.status_code == 403
        assert api.profile.profile_verification_complete == UserAccountStatus.PENDING

    def test_a_review_must_approve_or_reject(self, api):
        response = api.client.put(
            BASE + "/user123/review",
            json={"profile_verification_complete": "pending"},
            headers=auth("staff1", AGENT),
        )
        assert response.status_code == 422

    def test_reviewing_a_missing_profile_is_404(self, api):
        api.app.dependency_overrides[get_session] = lambda: make_session(None)
        response = api.client.put(
            BASE + "/ghost/review",
            json={"profile_verification_complete": "approved"},
            headers=auth("staff1", AGENT),
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /{user_id} - a client removes their own profile
# ---------------------------------------------------------------------------

class TestDeleteProfile:
    def test_client_deletes_their_own(self, api):
        response = api.client.delete(BASE + "/user123", headers=auth("user123", CLIENT))
        assert response.status_code == 200
        api.session.delete.assert_awaited_once()

    def test_client_cannot_delete_someone_elses(self, api):
        response = api.client.delete(BASE + "/user123", headers=auth("intruder", CLIENT))
        assert response.status_code == 403
        api.session.delete.assert_not_called()

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND])
    def test_staff_cannot_delete_profiles(self, api, role):
        response = api.client.delete(BASE + "/user123", headers=auth("staff1", role))
        assert response.status_code == 403
        api.session.delete.assert_not_called()


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

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND])
    def test_staff_cannot_create_a_client_profile(self, api, role):
        response = api.client.post(
            BASE + "/create",
            data=self.FORM,
            files={
                "id_document": ("id.pdf", b"%PDF-1.7 x", "application/pdf"),
                "profile_picture": ("me.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
            },
            headers=auth("staff1", role),
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

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND])
    def test_staff_roles_cannot_be_self_assigned_by_phone_signup(self, self_registration, role):
        self_registration(["User"])
        with pytest.raises(ValidationError, match="self-registration"):
            PhoneNumberSignup(**PHONE_SIGNUP, role=role)

    @pytest.mark.parametrize("role", [AGENT, BROKER, FUND])
    def test_staff_roles_cannot_be_self_assigned_by_firebase_signup(self, self_registration, role):
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

    def test_unknown_role_fails_fast(self):
        with pytest.raises(ValueError, match="Admin"):
            self.parse("User,Admin")
