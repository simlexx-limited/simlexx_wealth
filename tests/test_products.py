from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from app.ai_agents.orchestrator.orchestrator import describe_product
from app.database.database import get_session
from app.model.mod_enum import CurrencyType, ProductType, UserRole
from app.routers.products.product_router import router
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler

BASE = "/api/v1/products"


def make_product(**overrides):
    fields = dict(
        id="p1", company_name="Acme Capital", product_name="Growth Fund",
        product_type=ProductType.UNIT_TRUST, description="A balanced unit trust.",
        minimum_investment=Decimal("10000"), currency=CurrencyType.UGX, is_active=True,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def client(gate):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)

    session = AsyncMock()
    found = MagicMock()
    found.scalars.return_value.all.return_value = [make_product()]
    session.execute = AsyncMock(return_value=found)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app), gate


class TestProductListAccess:
    @pytest.mark.parametrize("role", [UserRole.CLIENT, UserRole.AGENT, UserRole.ADMIN])
    def test_clients_agents_and_admins_can_list(self, client, role):
        test_client, gate = client
        response = test_client.get(BASE, headers=gate.headers("u1", role))

        assert response.status_code == 200
        assert response.json()[0]["product_name"] == "Growth Fund"

    @pytest.mark.parametrize("role", [UserRole.FUNDMANAGER, UserRole.BROKER])
    def test_other_roles_cannot_list(self, client, role):
        test_client, gate = client
        assert test_client.get(BASE, headers=gate.headers("x1", role)).status_code == 403

    def test_anonymous_callers_cannot_list(self, client):
        test_client, _ = client
        assert test_client.get(BASE).status_code in (401, 403)

    def test_the_document_link_is_not_exposed(self, client):
        test_client, gate = client
        body = test_client.get(BASE, headers=gate.headers("u1", UserRole.CLIENT)).json()[0]
        assert "document_link" not in body and "document_id" not in body


class TestDescribeProduct:
    def test_no_product_says_none(self):
        assert describe_product(None) == "none"

    def test_describes_the_product_for_the_agent(self):
        text = describe_product(make_product())
        assert "Growth Fund by Acme Capital (unit_trust)" in text
        assert "minimum investment 10000 UGX" in text
        assert "A balanced unit trust." in text

    def test_a_missing_minimum_is_said_plainly(self):
        text = describe_product(make_product(minimum_investment=None))
        assert "no stated minimum investment" in text
