from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.setting import get_settings
from app.database.database import get_session
from app.model.mod_enum import NotificationStatus
from app.routers.webhooks.africastalking_router import router
from app.services.notifications.notification_service import NotificationService
from app.services.sms.sms_result import SmsResult

TOKEN = "s3cret-token"
URL = f"/api/v1/webhooks/africastalking/sms/delivery/{TOKEN}"
REPORT = {"id": "ATXid_1", "status": "Success", "phoneNumber": "+256700000001", "networkCode": "64101"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "AT_CALLBACK_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: AsyncMock()
    return TestClient(app)


class TestWebhookEndpoint:
    def test_applies_the_report(self, client):
        with patch.object(NotificationService, "apply_delivery_report", AsyncMock(return_value=MagicMock())) as apply:
            response = client.post(URL, data={**REPORT, "status": "Failed", "failureReason": "UserInBlacklist"})

        assert response.status_code == 200
        apply.assert_awaited_once_with("ATXid_1", "Failed", "UserInBlacklist")

    def test_unknown_message_id_still_returns_200(self, client):
        with patch.object(NotificationService, "apply_delivery_report", AsyncMock(return_value=None)):
            response = client.post(URL, data=REPORT)
        assert response.status_code == 200

    def test_wrong_token_is_404(self, client):
        with patch.object(NotificationService, "apply_delivery_report", AsyncMock()) as apply:
            response = client.post("/api/v1/webhooks/africastalking/sms/delivery/wrong", data=REPORT)
        assert response.status_code == 404
        apply.assert_not_called()

    def test_no_token_configured_is_404(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "AT_CALLBACK_TOKEN", "")
        with patch.object(NotificationService, "apply_delivery_report", AsyncMock()) as apply:
            response = client.post(URL, data=REPORT)
        assert response.status_code == 404
        apply.assert_not_called()


def session_finding(notification):
    session = MagicMock(commit=AsyncMock())
    result = MagicMock()
    result.scalar_one_or_none.return_value = notification
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=notification)
    return session


class TestApplyDeliveryReport:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("at_status, expected", [
        ("Success", NotificationStatus.DELIVERED),
        ("Sent", NotificationStatus.SENT),
        ("Submitted", NotificationStatus.SENT),
        ("Buffered", NotificationStatus.SENT),
        ("Failed", NotificationStatus.FAILED),
        ("Rejected", NotificationStatus.FAILED),
        ("AbsentSubscriber", NotificationStatus.FAILED),
    ])
    async def test_status_mapping(self, at_status, expected):
        row = SimpleNamespace(status=NotificationStatus.SENT, delivered_at=None, failure_reason=None)
        await NotificationService(session_finding(row)).apply_delivery_report("m1", at_status)
        assert row.status == expected
        if expected == NotificationStatus.DELIVERED:
            assert row.delivered_at is not None
        if expected == NotificationStatus.FAILED:
            assert row.failure_reason == at_status

    @pytest.mark.asyncio
    @pytest.mark.parametrize("final", [NotificationStatus.DELIVERED, NotificationStatus.FAILED])
    async def test_final_states_are_never_overwritten(self, final):
        row = SimpleNamespace(status=final, delivered_at=None, failure_reason=None)
        session = session_finding(row)
        await NotificationService(session).apply_delivery_report("m1", "Sent")
        assert row.status == final
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_status_is_ignored(self):
        row = SimpleNamespace(status=NotificationStatus.SENT, delivered_at=None, failure_reason=None)
        await NotificationService(session_finding(row)).apply_delivery_report("m1", "SomethingNew")
        assert row.status == NotificationStatus.SENT

    @pytest.mark.asyncio
    async def test_unknown_message_id_returns_none(self):
        assert await NotificationService(session_finding(None)).apply_delivery_report("nope", "Success") is None

    @pytest.mark.asyncio
    async def test_mark_sent_records_provider_details(self):
        row = SimpleNamespace(status=NotificationStatus.PENDING, provider=None, provider_message_id=None, cost=None)
        await NotificationService(session_finding(row)).mark_sent(
            "n1", SmsResult(provider="africastalking", message_id="m1", cost="UGX 25.0000"),
        )
        assert (row.status, row.provider, row.provider_message_id, row.cost) == (
            NotificationStatus.SENT, "africastalking", "m1", "UGX 25.0000",
        )
