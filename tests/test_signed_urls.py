import asyncio
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.datastructures import Headers

from app.config.setting import Settings, get_settings
from app.database.database import get_session
from app.model.mod_enum import DocumentType, UserAccountStatus, UserRole
from app.routers.more_about_user.more_about_user_router import router as client_router
from app.routers.reviews.review_router import router as review_router
from app.schema.more_about_user_schema import (
    FileLinks,
    MoreAboutUserResponse,
    MoreAboutUserSummary,
)
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler
from app.services.reviews.review_service import ReviewService
from app.services.storage import signed_urls, upload_to_cloudflare
from app.services.storage.signed_urls import is_valid_key, links_expire_at, sign_key

ENDPOINT = "https://test-account.r2.cloudflarestorage.com"   # see offline_signing in conftest.py
DOC = "users/client1/about/client1_aaa.pdf"
PIC = "users/client1/profile/client1_bbb.png"


def query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class TestSignKey:
    def test_the_link_points_at_the_bucket_and_the_file(self):
        url = sign_key(DOC)
        parsed = urlparse(url)
        assert url.startswith(ENDPOINT)
        assert unquote(parsed.path) == f"/{get_settings().r2_bucket_name}/{DOC}"

    def test_the_link_is_signed_and_expires_after_the_configured_time(self):
        params = query(sign_key(DOC))
        assert params["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
        assert re.fullmatch(r"[0-9a-f]{64}", params["X-Amz-Signature"])
        assert params["X-Amz-Expires"] == str(get_settings().SIGNED_URL_EXPIRE_SECONDS)

    def test_the_expiry_follows_the_setting(self, monkeypatch):
        monkeypatch.setattr(signed_urls, "get_settings", lambda: SimpleNamespace(
            r2_bucket_name="b", SIGNED_URL_EXPIRE_SECONDS=1234))
        assert query(sign_key(DOC))["X-Amz-Expires"] == "1234"

    def test_each_file_gets_its_own_signature(self):
        assert query(sign_key(DOC))["X-Amz-Signature"] != query(sign_key(PIC))["X-Amz-Signature"]

    @pytest.mark.parametrize("bad", [
        "", "https://pub.r2.dev/users/u1/about/f.pdf", "/users/u1/f.pdf", "other/u1/f.pdf",
        "users/../secret", "users//u1/f.pdf", "Users/u1/f.pdf",
    ])
    def test_only_keys_we_uploaded_can_be_signed(self, bad):
        assert is_valid_key(bad) is False
        with pytest.raises(ValueError):
            sign_key(bad)

    def test_our_keys_are_valid(self):
        assert is_valid_key(DOC) and is_valid_key(PIC)

    def test_links_expire_at_matches_the_setting(self):
        before = datetime.now(timezone.utc)
        expires = links_expire_at()
        expected = before + timedelta(seconds=get_settings().SIGNED_URL_EXPIRE_SECONDS)
        assert abs((expires - expected).total_seconds()) < 5
        assert expires.tzinfo is not None


class TestSetting:
    @pytest.mark.parametrize("value", ["59", "0", "-5", "604801"])
    def test_out_of_range_expiry_is_refused(self, monkeypatch, value):
        monkeypatch.setenv("SIGNED_URL_EXPIRE_SECONDS", value)
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    @pytest.mark.parametrize("value", ["60", "900", "3600", "604800"])
    def test_sensible_expiry_is_accepted(self, monkeypatch, value):
        monkeypatch.setenv("SIGNED_URL_EXPIRE_SECONDS", value)
        assert Settings().SIGNED_URL_EXPIRE_SECONDS == int(value)  # type: ignore[call-arg]

    def test_the_default_is_fifteen_minutes(self):
        assert Settings.model_fields["SIGNED_URL_EXPIRE_SECONDS"].default == 900


class TestUploadReturnsAKey:
    def upload(self, filename="scan.pdf", folder="about", username="client1"):
        upload_file = UploadFile(file=BytesIO(b"data"), filename=filename, headers=Headers({"content-type": "application/pdf"}))
        client = MagicMock()
        with patch.object(upload_to_cloudflare, "_get_s3_client", return_value=client):
            key = asyncio.run(upload_to_cloudflare.upload(upload_file, folder_name=folder, username=username))
        return key, client

    def test_returns_the_object_key_not_a_url(self):
        key, _ = self.upload()
        assert re.fullmatch(r"users/client1/about/client1_[0-9a-f]{32}\.pdf", key)
        assert "http" not in key

    def test_the_key_is_what_was_stored_in_the_bucket(self):
        key, client = self.upload(folder="profile", filename="me.png")
        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Key"] == key and kwargs["Bucket"] == get_settings().r2_bucket_name

    def test_what_upload_returns_can_be_signed(self):
        key, _ = self.upload()
        assert is_valid_key(key)
        assert key in unquote(sign_key(key))

    def test_two_uploads_never_share_a_key(self):
        assert self.upload()[0] != self.upload()[0]


def profile_row(**overrides) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        id="p1", user_id="client1", first_name="Test", last_name="Client", middle_name=None,
        date_of_birth=datetime(1990, 5, 1), nationality="UG", country_of_residence="UG",
        city_of_residence="KAMPALA", full_address="plot 1", profession="ENGINEER",
        document_type=DocumentType.NATIONAL_ID, document_id="CM1", document_keys=[DOC],
        document_expiry_date=datetime(2030, 1, 1).date(), profile_picture_keys=[PIC],
        profile_verification_complete=UserAccountStatus.PENDING, reviewer_id=None, reviewed_by=None,
        reviewed_at=None, review_comment=None, updated_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestResponseSchemas:
    def test_a_full_profile_carries_signed_links_and_an_expiry(self):
        body = MoreAboutUserResponse.model_validate(profile_row()).model_dump(mode="json")
        assert body["document_link"].startswith(ENDPOINT) and DOC in unquote(body["document_link"])
        assert body["profile_picture"].startswith(ENDPOINT) and PIC in unquote(body["profile_picture"])
        assert "links_expire_at" in body

    def test_the_raw_keys_are_never_in_a_response(self):
        body = MoreAboutUserResponse.model_validate(profile_row()).model_dump(mode="json")
        assert "document_keys" not in body and "profile_picture_keys" not in body
        # ...and the only place a key appears is inside the signed link's path
        flat = str({k: v for k, v in body.items() if k not in ("document_link", "profile_picture")})
        assert DOC not in flat and PIC not in flat

    def test_the_openapi_schema_does_not_offer_the_keys_either(self):
        schema = MoreAboutUserResponse.model_json_schema(mode="serialization")
        assert "document_keys" not in schema["properties"]
        assert {"document_link", "profile_picture", "links_expire_at"} <= set(schema["properties"])

    def test_the_summary_has_no_links_at_all(self):
        body = MoreAboutUserSummary.model_validate(profile_row()).model_dump(mode="json")
        assert not {"document_link", "profile_picture", "links_expire_at", "document_keys", "profile_picture_keys"} & set(body)
        assert body["user_id"] == "client1" and body["nationality_name"] == "Uganda"

    def test_a_row_still_holding_an_old_public_url_is_not_signed(self):
        """Old rows must be migrated: a stored URL is refused, not silently signed."""
        row = profile_row(document_keys=["https://pub.r2.dev/users/client1/about/x.pdf"])
        with pytest.raises(ValueError):
            MoreAboutUserResponse.model_validate(row).model_dump(mode="json")

    def test_file_links_for_keys(self):
        links = FileLinks.for_keys(DOC, PIC)
        assert DOC in unquote(links.document_link) and PIC in unquote(links.profile_picture)
        assert links.links_expire_at > datetime.now(timezone.utc)


# ---------------------------------------------------------------- endpoints

BASE_CLIENT = "/api/v1/more_about_user"
BASE_REVIEWS = "/api/v1/reviews"


@pytest.fixture
def client(monkeypatch):
    from app.services.rate_limiter.limiter import limiter
    monkeypatch.setattr(limiter, "enabled", False)
    app = FastAPI()
    app.include_router(client_router)
    app.include_router(review_router)
    app.add_exception_handler(AuthError, auth_error_handler)

    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = profile_row()
    session.execute = AsyncMock(return_value=found)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


class TestFileLinkEndpoints:
    def test_a_client_refreshes_their_own_links(self, client, gate):
        response = client.get(f"{BASE_CLIENT}/client1/files", headers=gate.headers("client1", UserRole.CLIENT))
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"document_link", "profile_picture", "links_expire_at"}
        assert DOC in unquote(body["document_link"])

    def test_a_client_cannot_get_someone_elses_links(self, client, gate):
        response = client.get(f"{BASE_CLIENT}/client1/files", headers=gate.headers("intruder", UserRole.CLIENT))
        assert response.status_code == 403
        assert "X-Amz" not in response.text

    @pytest.mark.parametrize("who", ["admin", "fund manager", "old broker role"])
    def test_other_roles_do_not_get_a_client_endpoint(self, client, gate, who):
        role = {"admin": UserRole.ADMIN, "fund manager": UserRole.FUNDMANAGER, "old broker role": UserRole.BROKER}[who]
        assert client.get(f"{BASE_CLIENT}/client1/files", headers=gate.headers("x1", role)).status_code == 403

    def test_an_agent_refreshes_any_clients_links(self, client, gate):
        response = client.get(f"{BASE_REVIEWS}/client1/files", headers=gate.agent())
        assert response.status_code == 200
        assert DOC in unquote(response.json()["document_link"])

    @pytest.mark.parametrize("who", ["client", "admin", "fund manager", "no token"])
    def test_nobody_else_gets_an_agent_link(self, client, gate, who):
        headers = {
            "client": gate.headers("client1", UserRole.CLIENT),
            "admin": gate.headers("boss", UserRole.ADMIN),
            "fund manager": gate.headers("f1", UserRole.FUNDMANAGER),
            "no token": None,
        }[who]
        response = client.get(f"{BASE_REVIEWS}/client1/files", headers=headers or {})
        assert response.status_code in (401, 403)
        assert "X-Amz" not in response.text

    def test_a_deactivated_agent_gets_no_links(self, client, gate):
        from app.model.mod_enum import AccountStatus
        response = client.get(f"{BASE_REVIEWS}/client1/files", headers=gate.agent(status=AccountStatus.DISABLE))
        assert response.status_code == 403

    def test_one_profile_has_links_and_the_list_does_not(self, client, gate):
        with patch.object(ReviewService, "list_profiles", AsyncMock(return_value=([profile_row()], 1))):
            listing = client.get(BASE_REVIEWS, headers=gate.agent()).json()
        assert "document_link" not in listing["items"][0] and "X-Amz" not in str(listing)

        single = client.get(f"{BASE_REVIEWS}/client1", headers=gate.agent()).json()
        assert "X-Amz-Signature" in single["document_link"] and "X-Amz-Signature" in single["profile_picture"]
        assert "document_keys" not in single

    def test_a_clients_own_profile_has_links_too(self, client, gate):
        body = client.get(f"{BASE_CLIENT}/client1", headers=gate.headers("client1", UserRole.CLIENT)).json()
        assert "X-Amz-Signature" in body["document_link"] and "links_expire_at" in body
        assert "document_keys" not in body and "profile_picture_keys" not in body
