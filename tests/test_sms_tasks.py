from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.setting import get_settings
from app.services.sms import tasks
from app.services.sms.sms_errors import InvalidSmsRecipientError, SmsServiceUnavailableError
from app.services.sms.sms_result import SmsResult


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.fixture
def notifications(monkeypatch):
    """Replace the DB side: background_session yields a dummy, NotificationService is a mock."""
    service = MagicMock(mark_sent=AsyncMock(), mark_failed=AsyncMock())

    @asynccontextmanager
    async def fake_session():
        yield MagicMock()

    monkeypatch.setattr(tasks, "background_session", fake_session)
    monkeypatch.setattr(tasks, "NotificationService", MagicMock(return_value=service))
    return service


class TestQueueSms:
    def test_normal_mode_hands_off_to_celery(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", False)
        delay = MagicMock()
        monkeypatch.setattr(tasks.send_sms_task, "delay", delay)

        tasks.queue_sms("n1", "+256700000001", "hi")

        delay.assert_called_once_with("n1", "+256700000001", "hi")

    def test_eager_mode_sends_on_a_thread_and_records_it(self, monkeypatch, notifications):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", True)
        monkeypatch.setattr(tasks, "threading", MagicMock(Thread=ImmediateThread))
        result = SmsResult(provider="africastalking", message_id="m1")
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(return_value=result))

        tasks.queue_sms("n1", "+256700000001", "hi")

        notifications.mark_sent.assert_awaited_once_with("n1", result)

    def test_a_failure_on_the_thread_is_logged_not_raised(self, monkeypatch, notifications):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", True)
        monkeypatch.setattr(tasks, "threading", MagicMock(Thread=ImmediateThread))
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(side_effect=SmsServiceUnavailableError()))

        tasks.queue_sms("n1", "+256700000001", "hi")   # must not raise

        notifications.mark_failed.assert_awaited_once_with("n1", "SmsServiceUnavailableError")


class TestSendSmsTask:
    def run_task(self, retries=0):
        """Run the bound task body directly, with a fake request/retry."""
        task = tasks.send_sms_task
        task.push_request(retries=retries)
        try:
            return task.run("n1", "+256700000001", "hi")
        finally:
            task.pop_request()

    def test_success_marks_sent(self, monkeypatch, notifications):
        result = SmsResult(provider="africastalking", message_id="m1", cost="UGX 25")
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(return_value=result))

        self.run_task()

        notifications.mark_sent.assert_awaited_once_with("n1", result)
        notifications.mark_failed.assert_not_called()

    def test_transient_failure_retries_without_marking_failed(self, monkeypatch, notifications):
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(side_effect=SmsServiceUnavailableError()))
        retry = MagicMock(side_effect=RuntimeError("retry scheduled"))
        monkeypatch.setattr(tasks.send_sms_task, "retry", retry)

        with pytest.raises(RuntimeError, match="retry scheduled"):
            self.run_task(retries=0)

        retry.assert_called_once()
        notifications.mark_failed.assert_not_called()

    def test_transient_failure_on_last_attempt_marks_failed(self, monkeypatch, notifications):
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(side_effect=SmsServiceUnavailableError()))
        retry = MagicMock()
        monkeypatch.setattr(tasks.send_sms_task, "retry", retry)

        with pytest.raises(SmsServiceUnavailableError):
            self.run_task(retries=tasks.send_sms_task.max_retries)

        retry.assert_not_called()
        notifications.mark_failed.assert_awaited_once_with("n1", "SmsServiceUnavailableError")

    def test_permanent_failure_marks_failed_and_does_not_retry(self, monkeypatch, notifications):
        monkeypatch.setattr(tasks, "send_sms", AsyncMock(side_effect=InvalidSmsRecipientError()))
        retry = MagicMock()
        monkeypatch.setattr(tasks.send_sms_task, "retry", retry)

        self.run_task()

        retry.assert_not_called()
        notifications.mark_failed.assert_awaited_once_with("n1", "InvalidSmsRecipientError")
