from unittest.mock import MagicMock

import pytest

from app.config.setting import get_settings
from app.services.email import tasks


class ImmediateThread:
    """Stands in for threading.Thread, but runs the target synchronously - so a test
    doesn't have to sleep/poll to see what a background thread did."""

    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class TestQueueEmail:
    def test_normal_mode_hands_off_to_the_celery_broker(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", False)
        mock_delay = MagicMock()
        monkeypatch.setattr(tasks.send_email_task, "delay", mock_delay)

        tasks.queue_email("to@example.com", "Subject", "Body")

        mock_delay.assert_called_once_with("to@example.com", "Subject", "Body")

    def test_eager_mode_sends_on_a_plain_thread_instead(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", True)
        monkeypatch.setattr(tasks, "threading", MagicMock(Thread=ImmediateThread))
        mock_delay = MagicMock()
        monkeypatch.setattr(tasks.send_email_task, "delay", mock_delay)
        mock_send = MagicMock()
        monkeypatch.setattr(tasks, "build_and_send_email", mock_send)

        tasks.queue_email("to@example.com", "Subject", "Body")

        mock_send.assert_called_once_with("to@example.com", "Subject", "Body")
        mock_delay.assert_not_called()

    def test_a_failure_on_the_thread_is_logged_not_raised(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "CELERY_TASK_ALWAYS_EAGER", True)
        monkeypatch.setattr(tasks, "threading", MagicMock(Thread=ImmediateThread))
        monkeypatch.setattr(tasks, "build_and_send_email", MagicMock(side_effect=RuntimeError("SMTP down")))

        tasks.queue_email("to@example.com", "Subject", "Body")   # must not raise
