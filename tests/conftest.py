import pytest

from app.services.auth import login_lockout


class FakeRedis:
    """Just enough of redis for the lockout counters; ttl is set once and never elapses."""

    def __init__(self):
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return str(self.values[key]) if key in self.values else None

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def ttl(self, key):
        if key not in self.values:
            return -2
        return self.ttls.get(key, -1)

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    """An in-memory stand-in for the login lockout counters."""
    fake = FakeRedis()
    monkeypatch.setattr(login_lockout, "redis_client", fake)
    return fake


@pytest.fixture
def gate(monkeypatch):
    """
    Real tokens for made-up accounts: the gate's database lookups are replaced by
    an in-memory account table, so endpoint tests exercise the real role checks.
    """
    import asyncio
    from types import SimpleNamespace

    from app.model.mod_enum import AccountStatus, UserRole
    from app.services.auth import current_user
    from app.services.auth.phone.token.jwt import JwtManagement

    users: dict = {}
    without_profile: set = set()

    async def find_user_by_id(session, user_id):
        return users.get(user_id)

    async def has_agent_profile(session, user_id):
        return user_id not in without_profile

    monkeypatch.setattr(current_user, "find_user_by_id", find_user_by_id)
    monkeypatch.setattr(current_user, "has_agent_profile", has_agent_profile)

    def headers(user_id, role, *, must_change_password=False, status=AccountStatus.ACTIVE, profile=True):
        users[user_id] = SimpleNamespace(
            id=user_id, role=role, status=status, token_version=0, firebase_uid=None,
            must_change_password=must_change_password,
        )
        if not profile:
            without_profile.add(user_id)
        token = asyncio.run(JwtManagement().encode_auth({"user_id": user_id, "token_version": 0}))
        return {"Authorization": f"Bearer {token}"}

    def agent(user_id="agent1", **kw):
        return headers(user_id, UserRole.AGENT, **kw)

    return SimpleNamespace(headers=headers, agent=agent)


DUMMY_ENDPOINT = "https://test-account.r2.cloudflarestorage.com"


@pytest.fixture(autouse=True)
def offline_signing(monkeypatch):
    """
    Signing a link is a local calculation, so tests do it for real, but with
    dummy credentials: the results never depend on (or leak) what is in .env.
    """
    import boto3
    from botocore.client import Config

    from app.services.storage import signed_urls

    client = boto3.client(
        "s3",
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        endpoint_url=DUMMY_ENDPOINT,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    monkeypatch.setattr(signed_urls, "signing_client", lambda: client)
