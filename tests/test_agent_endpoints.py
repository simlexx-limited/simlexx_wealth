from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.database import get_session
from app.model.mod_enum import AccountStatus, UserRole
from app.routers.simlexx.admin_router import router
from app.schema.agent_schema import AgentResponse
from app.services.admin.admin_service import AdminService
from app.services.agents.agent_service import AgentService
from app.services.auth.firebase_auth.auth_errors import AuthError, auth_error_handler
from app.services.rate_limiter.limiter import limiter

BASE = "/api/v1/simlexx/admin"
NOW = datetime.now(timezone.utc)
AGENT_BODY = {"first_name": "Jane", "last_name": "Doe", "email_address": "jane@simlexx.com", "phone_number": "+256700000010"}


def agent_out(**overrides) -> AgentResponse:
    fields: dict[str, Any] = dict(user_id="u1", first_name="Jane", last_name="Doe", email_address="jane@simlexx.com",
                                  phone_number="+256700000010", job_title=None, status=AccountStatus.ACTIVE,
                                  must_change_password=True, created_at=NOW)
    fields.update(overrides)
    return AgentResponse(**fields)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AuthError, auth_error_handler)
    app.dependency_overrides[get_session] = lambda: AsyncMock()
    return TestClient(app)


@pytest.fixture
def services():
    user, profile = SimpleNamespace(id="u1"), SimpleNamespace(id="p1")
    with patch.object(AgentService, "create_agent", AsyncMock(return_value=(user, profile, True, "Str0ng!TempPass1"))) as create, \
         patch.object(AgentService, "list_agents", AsyncMock(return_value=[agent_out()])) as listing, \
         patch.object(AgentService, "get_agent", AsyncMock(return_value=agent_out())) as get, \
         patch.object(AgentService, "set_status", AsyncMock(return_value=agent_out(status=AccountStatus.DISABLE))) as status, \
         patch.object(AgentService, "reset_password", AsyncMock(return_value={"status": True, "message": "sent"})) as reset, \
         patch.object(AdminService, "create_admin", AsyncMock(return_value={"status": True, "temporary_password": "Str0ng!TempPass2"})) as create_admin, \
         patch("app.routers.simlexx.admin_router.agent_response", return_value=agent_out()):
        yield SimpleNamespace(create=create, list=listing, get=get, status=status, reset=reset, create_admin=create_admin)


CALLS = [
    ("post", f"{BASE}/admins", AGENT_BODY),
    ("post", f"{BASE}/agents", AGENT_BODY),
    ("get", f"{BASE}/agents", None),
    ("get", f"{BASE}/agents/u1", None),
    ("patch", f"{BASE}/agents/u1/status", {"active": False, "reason": "left"}),
    ("post", f"{BASE}/agents/u1/reset-password", None),
]


def call(client, method, path, body=None, headers=None):
    return client.request(method.upper(), path, json=body, headers=headers or {})


class TestAdminAccess:
    @pytest.mark.parametrize("method,path,body", CALLS)
    def test_an_admin_gets_through(self, client, services, gate, method, path, body):
        response = call(client, method, path, body, gate.headers("boss", UserRole.ADMIN))
        assert response.status_code in (200, 201), response.text

    @pytest.mark.parametrize("method,path,body", CALLS)
    def test_nobody_else_does(self, client, services, gate, method, path, body):
        outsiders = {
            "no token": None,
            "client": gate.headers("c1", UserRole.CLIENT),
            "fund manager": gate.headers("f1", UserRole.FUNDMANAGER),
            "agent": gate.agent(),
            "old broker role": gate.headers("b1", UserRole.BROKER),
        }
        for who, headers in outsiders.items():
            assert call(client, method, path, body, headers).status_code in (401, 403), who
        services.create.assert_not_awaited()
        services.status.assert_not_awaited()
        services.reset.assert_not_awaited()

    def test_an_admin_on_a_temporary_password_is_held_at_the_gate(self, client, services, gate):
        response = call(client, "get", f"{BASE}/agents", None, gate.headers("boss", UserRole.ADMIN, must_change_password=True))
        assert response.status_code == 403
        services.list.assert_not_awaited()

    def test_a_deactivated_admin_is_locked_out(self, client, services, gate):
        response = call(client, "get", f"{BASE}/agents", None, gate.headers("boss", UserRole.ADMIN, status=AccountStatus.DISABLE))
        assert response.status_code == 403

    def test_the_actor_and_target_come_from_the_token_and_path(self, client, services, gate):
        headers = gate.headers("boss", UserRole.ADMIN)

        call(client, "post", f"{BASE}/agents", AGENT_BODY, headers)
        assert services.create.await_args.kwargs["created_by"] == "boss"

        call(client, "patch", f"{BASE}/agents/target-9/status", {"active": False, "reason": "x"}, headers)
        assert services.status.await_args.args[:3] == ("target-9", False, "x")
        assert services.status.await_args.kwargs["actor_id"] == "boss"

        call(client, "post", f"{BASE}/agents/target-9/reset-password", None, headers)
        assert services.reset.await_args.args[0] == "target-9"
        assert services.reset.await_args.kwargs["actor_id"] == "boss"

        call(client, "post", f"{BASE}/admins", AGENT_BODY, headers)
        assert services.create_admin.await_args.kwargs["created_by"] == "boss"

    @pytest.mark.parametrize("body", [
        {**AGENT_BODY, "email_address": "nope"},
        {**AGENT_BODY, "phone_number": "123"},
        {k: v for k, v in AGENT_BODY.items() if k != "first_name"},
        {**AGENT_BODY, "last_name": ""},
    ])
    def test_bad_bodies_are_422(self, client, services, gate, body):
        assert call(client, "post", f"{BASE}/agents", body, gate.headers("boss", UserRole.ADMIN)).status_code == 422
        services.create.assert_not_awaited()

    def test_a_status_change_needs_a_boolean(self, client, services, gate):
        headers = gate.headers("boss", UserRole.ADMIN)
        assert call(client, "patch", f"{BASE}/agents/u1/status", {"reason": "x"}, headers).status_code == 422
        assert call(client, "patch", f"{BASE}/agents/u1/status", {"active": "maybe"}, headers).status_code == 422

    def test_the_temporary_password_only_appears_on_creation_responses(self, client, services, gate):
        """
        Temporary, while the deployed email service is unreliable: /admins and
        /agents creation responses carry the raw temporary password so an
        admin can relay it by hand - everywhere else, it must never leak.
        """
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        headers = gate.headers("boss", UserRole.ADMIN)
        creation_calls = {("post", f"{BASE}/admins"), ("post", f"{BASE}/agents")}
        for method, path, body in CALLS:
            response = call(client, method, path, body, headers)
            assert "http" not in response.text.lower()
            offending = [
                k for k in keys(response.json())
                if "password" in k and k not in ("must_change_password", "temporary_password")
            ]
            assert offending == [], f"{path}: {offending}"
            has_temp_password = isinstance(response.json(), dict) and "temporary_password" in response.json()
            assert has_temp_password == ((method, path) in creation_calls), f"{path}: {has_temp_password}"
