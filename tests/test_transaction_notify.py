"""Transaction.notify_user - how deposit/withdraw notifications go out by SMS."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.mod_enum import NotificationChannel
from app.services.sms.sms_result import SmsResult
from app.services.transaction import transaction as tx_module
from app.services.transaction.transaction import Transaction

PHONE = "+256700000001"


@pytest.fixture
def mocks(monkeypatch):
    notifications = MagicMock(create_notification=AsyncMock(return_value=SimpleNamespace(id="n1")))
    m = SimpleNamespace(
        notifications=notifications,
        queue_email=MagicMock(),
        queue_sms=MagicMock(),
        send_otp_sms=AsyncMock(return_value=SmsResult(provider="africastalking", message_id="m1")),
    )
    monkeypatch.setattr(tx_module, "NotificationService", MagicMock(return_value=notifications))
    monkeypatch.setattr(tx_module, "queue_email", m.queue_email)
    monkeypatch.setattr(tx_module, "queue_sms", m.queue_sms)
    monkeypatch.setattr(tx_module, "send_otp_sms", m.send_otp_sms)
    return m


@pytest.mark.asyncio
async def test_email_is_queued_and_sms_untouched(mocks):
    await Transaction(AsyncMock()).notify_user(
        NotificationChannel.EMAIL, "a@b.com", "Subj", "Body", user_id="u1", notification_type="x",
    )
    mocks.queue_email.assert_called_once_with("a@b.com", "Subj", "Body")
    mocks.send_otp_sms.assert_not_called()
    mocks.queue_sms.assert_not_called()


@pytest.mark.asyncio
async def test_sms_otp_is_sent_inline_with_just_the_code_and_logged_without_it(mocks):
    await Transaction(AsyncMock()).notify_user(
        NotificationChannel.SMS, PHONE, "Confirm your deposit", "12345678",
        user_id="u1", notification_type="deposit_otp", otp_code="12345678",
    )
    mocks.send_otp_sms.assert_awaited_once_with(PHONE, "12345678")
    kwargs = mocks.notifications.create_notification.await_args.kwargs
    assert kwargs["sms_result"].message_id == "m1"
    assert "12345678" not in kwargs["content"]
    mocks.queue_sms.assert_not_called()


@pytest.mark.asyncio
async def test_sms_confirmation_is_logged_then_queued(mocks):
    await Transaction(AsyncMock()).notify_user(
        NotificationChannel.SMS, PHONE, "Deposit confirmed", "long email text",
        user_id="u1", notification_type="deposit_confirmed", sms_text="Simlexx: short text",
    )
    mocks.notifications.create_notification.assert_awaited_once()
    mocks.queue_sms.assert_called_once_with("n1", PHONE, "Simlexx: short text")
    mocks.send_otp_sms.assert_not_called()
