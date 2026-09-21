from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config.setting import Settings
from app.model.mod_enum import DocumentType
from app.routers.reference.reference_router import router as reference_router
from app.schema.country import country_name
from app.schema.more_about_user_schema import MoreAboutUserCreate, MoreAboutUserUpdate
from app.services.auth.firebase_auth.auth_errors import DuplicateAccountError, UnsupportedCountryError
from app.services.more_about_user.more_about_user_service import MoreAboutUserService
from app.services.reference import country_service


def make_profile_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "first_name": "Test",
        "last_name": "User",
        "date_of_birth": date(1990, 5, 1),
        "nationality": "UG",
        "country_of_residence": "UG",
        "city_of_residence": "Kampala",
        "full_address": "Plot 1, Kampala Road",
        "profession": "Engineer",
        "document_type": DocumentType.NATIONAL_ID,
        "document_id": "CM12345678ABCD",
        "document_expiry_date": date(2030, 1, 1),
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def served_countries(monkeypatch):
    """Point the country service at a fixed served list, no .env needed."""
    def set_served(codes: list[str]) -> None:
        monkeypatch.setattr(
            country_service, "get_settings", lambda: SimpleNamespace(SERVED_COUNTRIES=codes)
        )
    return set_served


# ---------------------------------------------------------------------------
# CountryCode validation in the profile schemas
# ---------------------------------------------------------------------------

class TestCountryCodeValidation:
    def test_codes_are_trimmed_and_uppercased(self):
        data = MoreAboutUserCreate(**make_profile_payload(nationality="ug", country_of_residence=" rw "))
        assert data.nationality == "UG"
        assert data.country_of_residence == "RW"

    @pytest.mark.parametrize("bad", ["XX", "UGA", "", "1A", "Uganda"])
    def test_invalid_codes_are_rejected(self, bad):
        with pytest.raises(ValidationError):
            MoreAboutUserCreate(**make_profile_payload(nationality=bad))
        with pytest.raises(ValidationError):
            MoreAboutUserCreate(**make_profile_payload(country_of_residence=bad))

    def test_update_accepts_omitted_and_valid_codes(self):
        assert MoreAboutUserUpdate().country_of_residence is None
        assert MoreAboutUserUpdate(country_of_residence="ke").country_of_residence == "KE"

    def test_update_rejects_invalid_code(self):
        with pytest.raises(ValidationError):
            MoreAboutUserUpdate(nationality="ZZ")

    def test_country_name_lookup(self):
        assert country_name("UG") == "Uganda"
        assert country_name("RW") == "Rwanda"


# ---------------------------------------------------------------------------
# SERVED_COUNTRIES setting parsing
# ---------------------------------------------------------------------------

class TestServedCountriesSetting:
    parse = staticmethod(Settings.parse_served_countries)

    def test_comma_separated_string_becomes_list(self):
        assert self.parse("UG,RW") == ["UG", "RW"]

    def test_whitespace_case_and_duplicates_are_normalized(self):
        assert self.parse(" ug , rw ,UG,") == ["UG", "RW"]

    def test_empty_string_means_no_countries(self):
        assert self.parse("") == []

    def test_invalid_code_fails_fast(self):
        with pytest.raises(ValueError, match="UGA"):
            self.parse("UG,UGA")


# ---------------------------------------------------------------------------
# Country service and reference endpoint
# ---------------------------------------------------------------------------

class TestCountryService:
    def test_served_country_passes(self, served_countries):
        served_countries(["UG", "RW"])
        country_service.ensure_country_served("RW")

    def test_unserved_country_raises(self, served_countries):
        served_countries(["UG"])
        with pytest.raises(UnsupportedCountryError):
            country_service.ensure_country_served("KE")

    def test_list_marks_only_served_countries(self, served_countries):
        served_countries(["UG", "RW"])
        countries = country_service.list_countries()

        assert len(countries) > 200
        assert countries == sorted(countries, key=lambda c: c["name"])
        assert {c["code"] for c in countries if c["served"]} == {"UG", "RW"}


class TestReferenceEndpoint:
    def test_countries_endpoint_returns_codes_names_and_served_flag(self, served_countries):
        served_countries(["UG"])
        app = FastAPI()
        app.include_router(reference_router)

        response = TestClient(app).get("/api/v1/reference/countries")

        assert response.status_code == 200
        by_code = {c["code"]: c for c in response.json()}
        assert by_code["UG"] == {"code": "UG", "name": "Uganda", "served": True}
        assert by_code["KE"]["served"] is False


# ---------------------------------------------------------------------------
# MoreAboutUserService enforces the served-countries rule
# ---------------------------------------------------------------------------

class TestServiceEnforcesServedCountries:
    @pytest.mark.asyncio
    async def test_unserved_residence_is_rejected_before_touching_the_db(self, served_countries):
        served_countries(["UG"])
        session = AsyncMock()
        service = MoreAboutUserService(session)

        with pytest.raises(UnsupportedCountryError):
            await service.check_can_create(
                user_id="u1",
                data=MoreAboutUserCreate(**make_profile_payload(country_of_residence="KE")),
            )

        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_nationality_is_not_restricted(self, served_countries):
        """Any nationality is fine as long as the person lives in a served country."""
        served_countries(["UG"])
        session = AsyncMock()
        already_has_profile = MagicMock()
        already_has_profile.scalar_one_or_none.return_value = MagicMock()
        session.execute = AsyncMock(return_value=already_has_profile)
        service = MoreAboutUserService(session)

        # Getting as far as the duplicate-profile check proves the country
        # check passed for a Kenyan citizen living in Uganda.
        with pytest.raises(DuplicateAccountError):
            await service.check_can_create(
                user_id="u1",
                data=MoreAboutUserCreate(**make_profile_payload(nationality="KE", country_of_residence="UG")),
            )

    @pytest.mark.asyncio
    async def test_update_rejects_changing_residence_to_unserved_country(self, served_countries):
        served_countries(["UG"])
        session = AsyncMock()
        existing = MagicMock()
        found = MagicMock()
        found.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=found)
        service = MoreAboutUserService(session)

        with pytest.raises(UnsupportedCountryError):
            await service.update(user_id="u1", data=MoreAboutUserUpdate(country_of_residence="KE"))

        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_allows_served_residence_and_other_fields(self, served_countries):
        served_countries(["UG", "RW"])
        session = AsyncMock()
        existing = MagicMock()
        found = MagicMock()
        found.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=found)
        service = MoreAboutUserService(session)

        await service.update(user_id="u1", data=MoreAboutUserUpdate(country_of_residence="rw", profession="Nurse"))

        assert existing.country_of_residence == "RW"
        assert existing.profession == "Nurse"
        session.commit.assert_awaited_once()
