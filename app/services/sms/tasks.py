import asyncio
import logging
import threading

from app.config.setting import get_settings
from app.database.database import background_session
from app.queue.celery_app import celery_app
from app.services.notifications.notification_service import NotificationService
from app.services.sms.sms_errors import SmsSendError, SmsServiceUnavailableError
from app.services.sms.sms_service import mask_phone, send_sms

logger = logging.getLogger(__name__)


async def send_and_record(notification_id: str, to: str, message: str, *, final_attempt: bool = True) -> None:
    """
    Send the SMS and record the outcome on its NotificationsLogs row.
    A retryable failure is only recorded as FAILED on the final attempt;
    earlier ones re-raise so the caller can retry.
    """
    try:
        result = await send_sms(to, message)
    except SmsServiceUnavailableError as exc:
        if final_attempt:
            async with background_session() as session:
                await NotificationService(session).mark_failed(notification_id, type(exc).__name__)
        raise
    except SmsSendError as exc:
        async with background_session() as session:
            await NotificationService(session).mark_failed(notification_id, type(exc).__name__)
        raise
    async with background_session() as session:
        await NotificationService(session).mark_sent(notification_id, result)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def send_sms_task(self, notification_id: str, to: str, message: str) -> None:
    """
    Background SMS delivery - retries on transient failures
    (SmsServiceUnavailableError). An invalid number or an account-side
    rejection is not retried since it won't fix itself.
    """
    final_attempt = self.request.retries >= self.max_retries
    try:
        asyncio.run(send_and_record(notification_id, to, message, final_attempt=final_attempt))
    except SmsServiceUnavailableError as exc:
        if final_attempt:
            raise
        raise self.retry(exc=exc)
    except SmsSendError:
        # already recorded as FAILED; nothing to retry
        logger.warning("SMS %s to %s was not sent", notification_id, mask_phone(to))


def send_on_a_plain_thread(notification_id: str, to: str, message: str) -> None:
    try:
        asyncio.run(send_and_record(notification_id, to, message))
    except Exception:
        logger.exception("Background SMS %s to %s failed", notification_id, mask_phone(to))


def queue_sms(notification_id: str, to: str, message: str) -> None:
    """
    Send this SMS in the background - for anything that doesn't need to
    reach the user before the request returns (confirmations, alerts).
    notification_id is the NotificationsLogs row created for it, which gets
    the provider's message id, or FAILED.

    Same delivery rules as queue_email: Celery with retries normally, a plain
    OS thread (no retry) when CELERY_TASK_ALWAYS_EAGER=true.
    """
    if get_settings().CELERY_TASK_ALWAYS_EAGER:
        threading.Thread(target=send_on_a_plain_thread, args=(notification_id, to, message), daemon=True).start()
    else:
        send_sms_task.delay(notification_id, to, message)
