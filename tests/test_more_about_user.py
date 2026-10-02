from datetime import date, datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database.database import get_session
from app.model.mod_enum import DocumentType, UserAccountStatus
from app.routers.more_about_user import more_about_user_router as router_module
from app.routers.more_about_user.more_about_user_router import router
from app.schema.more_about_user_schema import MoreAboutUserCreate, UploadedFile
from app.services.auth.firebase_auth.auth_errors import (
    AuthError,
    DuplicateAccountError,
    UnsupportedCountryError,
    auth_error_handler,
)
from app.services.auth.current_user import get_current_user
from app.services.more_about_user.more_about_user_service import MoreAboutUserService
from app.services.reference import country_service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
TEXT = b"this is not an image, just some text"

DOC_KEY = "users/user123/about/user123_doc.pdf"
PIC_KEY = "users/user123/profile/user123_pic.png"
DUMMY_ENDPOINT = "https://test-account.r2.cloudflarestorage.com"   # see offline_signing in conftest.py


def make_profile_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "first_name": "Test",
        "last_name": "User",
        "date_of_birth": date(1990, 5, 1),
        "nationality": "UG",
        "country_of_residence": "UG",
        "city_of_residence": "KAMPALA",
        "full_address": "plot 1, kampala road",
        "profession": "ENGINEER",
        "document_type": DocumentType.NATIONAL_ID,
        "document_id": "CM12345678ABCD",
        "document_expiry_date": date(2030, 1, 1),
    }
    payload.update(overrides)
    return payload


def make_session(existing_profile: bool = False) -> AsyncMock:
    """A fake AsyncSession: `existing_profile` controls the duplicate check, and
    refresh() fills the columns Postgres would populate on insert."""
    session = AsyncMock()
    session.add = MagicMock()
    lookup = MagicMock()
    lookup.scalar_one_or_none.return_value = MagicMock() if existing_profile else None
    session.execute = AsyncMock(return_value=lookup)

    async def fake_refresh(profile):
        profile.id = "profile-1"
        profile.profile_verification_complete = UserAccountStatus.PENDING
        profile.updated_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=fake_refresh)
    return session


@pytest.fixture
def served_countries(monkeypatch):
    def set_served(codes: list[str]) -> None:
        monkeypatch.setattr(
            country_service, "get_settings", lambda: SimpleNamespace(SERVED_COUNTRIES=codes)
        )
    set_served(["UG"])
    return set_served


# ---------------------------------------------------------------------------
# UploadedFile schema: where an uploaded file lives in the private bucket
# ---------------------------------------------------------------------------

class TestUploadedFile:
    def test_accepts_a_key_we_uploaded(self):
        assert UploadedFile(key=DOC_KEY).key == DOC_KEY

    @pytest.mark.parametrize("bad", [
        "", "not a key", "https://files.example.com/users/u1/about/f.pdf",
        "/users/u1/about/f.pdf", "other/u1/about/f.pdf", "users/../secrets.txt", "users//u1/f.pdf",
    ])
    def test_rejects_anything_that_is_not_one_of_our_keys(self, bad):
        with pytest.raises(ValidationError):
            UploadedFile(key=bad)

    def test_key_is_required(self):
        with pytest.raises(ValidationError):
            UploadedFile()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# MoreAboutUserService.create
# ---------------------------------------------------------------------------

class TestServiceCreate:
    @pytest.mark.asyncio
    async def test_creates_profile_with_the_uploaded_keys(self, served_countries):
        session = make_session()

        profile = await MoreAboutUserService(session).create(
            user_id="user123",
            data=MoreAboutUserCreate(**make_profile_payload()),
            document_file=UploadedFile(key=DOC_KEY),
            picture_file=UploadedFile(key=PIC_KEY),
        )

        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        assert profile.user_id == "user123"
        assert profile.document_keys == [DOC_KEY]
        assert profile.profile_picture_keys == [PIC_KEY]
        assert isinstance(profile.document_keys, list)
        assert profile.document_type == DocumentType.NATIONAL_ID
        assert profile.country_of_residence == "UG"
        assert profile.profile_verification_complete == UserAccountStatus.PENDING
        assert profile.onboarding_complete is False   # only an agent's approval sets this

    @pytest.mark.asyncio
    async def test_duplicate_profile_is_rejected_and_nothing_is_saved(self, served_countries):
        session = make_session(existing_profile=True)

        with pytest.raises(DuplicateAccountError):
            await MoreAboutUserService(session).create(
                user_id="user123",
                data=MoreAboutUserCreate(**make_profile_payload()),
                document_file=UploadedFile(key=DOC_KEY),
                picture_file=UploadedFile(key=PIC_KEY),
            )

        session.add.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_unserved_country_is_rejected_and_nothing_is_saved(self, served_countries):
        session = make_session()

        with pytest.raises(UnsupportedCountryError):
            await MoreAboutUserService(session).create(
                user_id="user123",
                data=MoreAboutUserCreate(**make_profile_payload(country_of_residence="KE")),
                document_file=UploadedFile(key=DOC_KEY),
                picture_file=UploadedFile(key=PIC_KEY),
            )

        session.add.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/v1/more_about_user/create - real multipart requests
# ---------------------------------------------------------------------------

FORM_FIELDS = {
    "first_name": "Test",
    "last_name": "User",
    "date_of_birth": "1990-05-01",
    "nationality": "ug",
    "country_of_residence": "UG",
    "city_of_residence": "Kampala",
    "full_address": "Plot 1, Kampala Road",
    "profession": "Engineer",
    "document_type": "national_id",
    "document_id": "CM12345678ABCD",
    "document_expiry_date": "2030-01-01",
}


@pytest.fixture
def api(monkeypatch, served_countries):
    """The router mounted on a minimal app, with auth, the DB session and the
    bucket faked. Returns the client, the session, and the upload mock."""
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)

    session = make_session()
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user123", "role": "User"}
    app.dependency_overrides[get_session] = lambda: session

    async def fake_upload(file, folder_name, username, allowed_extensions=None):
        return DOC_KEY if folder_name == "about" else PIC_KEY

    upload_mock = AsyncMock(side_effect=fake_upload)
    monkeypatch.setattr(router_module, "upload", upload_mock)

    return SimpleNamespace(client=TestClient(app), app=app, session=session, upload=upload_mock)


def post_create(api, form=None, id_document=("id.pdf", PDF, "application/pdf"),
                profile_picture=("me.png", PNG, "image/png")):
    files = {}
    if id_document is not None:
        files["id_document"] = id_document
    if profile_picture is not None:
        files["profile_picture"] = profile_picture
    return api.client.post(
        "/api/v1/more_about_user/create",
        data=FORM_FIELDS if form is None else form,
        files=files,
    )


class TestCreateEndpoint:
    def test_creates_the_profile_and_returns_it(self, api):
        response = post_create(api)

        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == "user123"
        # the bucket is private: the response carries temporary signed links, never the raw keys
        assert body["document_link"].startswith(DUMMY_ENDPOINT) and DOC_KEY in body["document_link"]
        assert body["profile_picture"].startswith(DUMMY_ENDPOINT) and PIC_KEY in body["profile_picture"]
        assert "X-Amz-Signature=" in body["document_link"]
        assert "document_keys" not in body and "profile_picture_keys" not in body
        assert "links_expire_at" in body
        assert body["document_type"] == "national_id"
        assert body["profile_verification_complete"] == "pending"
        api.session.commit.assert_awaited_once()

    def test_response_includes_country_names_for_the_frontend(self, api):
        body = post_create(api).json()
        assert body["nationality"] == "UG"
        assert body["nationality_name"] == "Uganda"
        assert body["country_of_residence_name"] == "Uganda"

    def test_text_fields_are_normalized_before_saving(self, api):
        post_create(api)
        saved = api.session.add.call_args.args[0]
        assert saved.city_of_residence == "KAMPALA"
        assert saved.full_address == "plot 1, kampala road"
        assert saved.profession == "ENGINEER"
        assert saved.nationality == "UG"  # sent as "ug"

    def test_files_are_stored_per_user_with_the_expected_folders(self, api):
        post_create(api)

        calls = {c.kwargs["folder_name"]: c.kwargs for c in api.upload.call_args_list}
        assert set(calls) == {"about", "profile"}
        assert calls["about"]["username"] == "user123"
        assert calls["profile"]["username"] == "user123"
        assert ".pdf" in calls["about"]["allowed_extensions"]
        assert ".pdf" not in calls["profile"]["allowed_extensions"]

    def test_user_id_comes_from_the_token_not_the_form(self, api):
        form = {**FORM_FIELDS, "user_id": "someone-else"}
        post_create(api, form=form)
        assert api.session.add.call_args.args[0].user_id == "user123"

    def test_a_photo_of_the_id_is_accepted_too(self, api):
        assert post_create(api, id_document=("id.jpg", JPEG, "image/jpeg")).status_code == 201

    # --- rejected before anything is uploaded -----------------------------

    @pytest.mark.parametrize("field,value", [
        ("nationality", "Uganda"),
        ("country_of_residence", "XX"),
        ("date_of_birth", "not-a-date"),
        ("document_type", "library_card"),
    ])
    def test_invalid_form_values_are_422_not_500(self, api, field, value):
        response = post_create(api, form={**FORM_FIELDS, field: value})

        assert response.status_code == 422
        api.upload.assert_not_called()
        api.session.commit.assert_not_called()

    def test_validation_errors_use_the_same_shape_as_fastapis_own_422s(self, api):
        """Error locations must read ["body", <field>] whether FastAPI or our
        own model validation produced them, so a client handles one format."""
        invalid = post_create(api, form={**FORM_FIELDS, "nationality": "Uganda"}).json()
        missing = post_create(api, form={k: v for k, v in FORM_FIELDS.items() if k != "first_name"}).json()

        assert invalid["detail"][0]["loc"] == ["body", "nationality"]
        assert missing["detail"][0]["loc"] == ["body", "first_name"]

    def test_missing_form_field_is_422(self, api):
        form = {k: v for k, v in FORM_FIELDS.items() if k != "first_name"}
        assert post_create(api, form=form).status_code == 422
        api.upload.assert_not_called()

    @pytest.mark.parametrize("missing", ["id_document", "profile_picture"])
    def test_missing_file_is_422(self, api, missing):
        kwargs = {missing: None}
        assert post_create(api, **kwargs).status_code == 422
        api.upload.assert_not_called()

    def test_unserved_country_is_403_and_nothing_is_uploaded(self, api):
        response = post_create(api, form={**FORM_FIELDS, "country_of_residence": "KE"})

        assert response.status_code == 403
        api.upload.assert_not_called()
        api.session.add.assert_not_called()

    def test_existing_profile_is_409_and_nothing_is_uploaded(self, api):
        api.app.dependency_overrides[get_session] = lambda: make_session(existing_profile=True)

        response = post_create(api)

        assert response.status_code == 409
        api.upload.assert_not_called()

    def test_fake_image_is_rejected_and_neither_file_is_uploaded(self, api):
        response = post_create(api, profile_picture=("me.png", TEXT, "image/png"))

        assert response.status_code == 415
        api.upload.assert_not_called()

    def test_bad_id_document_is_rejected_and_neither_file_is_uploaded(self, api):
        response = post_create(api, id_document=("id.exe", b"MZ\x90\x00" * 8, "application/octet-stream"))

        assert response.status_code == 415
        api.upload.assert_not_called()

    def test_pdf_is_not_accepted_as_a_profile_picture(self, api):
        response = post_create(api, profile_picture=("me.pdf", PDF, "application/pdf"))

        assert response.status_code == 415
        api.upload.assert_not_called()

    def test_empty_file_is_400(self, api):
        response = post_create(api, id_document=("id.pdf", b"", "application/pdf"))

        assert response.status_code == 400
        api.upload.assert_not_called()


class TestCreateEndpointAuth:
    def test_requires_a_token(self, api):
        del api.app.dependency_overrides[get_current_user]

        response = post_create(api)

        assert response.status_code in (401, 403)
        api.upload.assert_not_called()
