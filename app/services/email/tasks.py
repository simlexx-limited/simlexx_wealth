import logging
import threading

from app.config.setting import get_settings
from app.queue.celery_app import celery_app
from app.services.email.email_service import EmailServiceUnavailableError, build_and_send_email

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def send_email_task(self, to_address: str, subject: str, body: str) -> None:
    """
    Background email delivery - retries on transient SMTP/connection
    failures (EmailServiceUnavailableError). A rejected recipient
    (InvalidRecipientError) is not retried since it won't fix itself.
    """
    try:
        build_and_send_email(to_address, subject, body)
    except EmailServiceUnavailableError as exc:
        raise self.retry(exc=exc)


def send_on_a_plain_thread(to_address: str, subject: str, body: str) -> None:
    try:
        build_and_send_email(to_address, subject, body)
    except Exception:
        logger.exception("Background email to %s failed", to_address)


def queue_email(to_address: str, subject: str, body: str) -> None:
    """
    Send this email in the background - use this everywhere instead of
    send_email_task.delay() directly.

    Normally this hands off to the Celery broker, so a separate worker
    process sends it, with retries on transient failure. On a deployment
    with no worker process (CELERY_TASK_ALWAYS_EAGER=true, e.g. the
    free-tier Render instance), there's nothing to consume the broker, so it
    sends on a plain OS thread instead: a slow or unreachable SMTP server
    then only blocks that one background thread, not the single event loop
    serving every other request. No retry on that path - acceptable for a
    temporary, no-worker deployment, not for the long run.
    """
    if get_settings().CELERY_TASK_ALWAYS_EAGER:
        threading.Thread(target=send_on_a_plain_thread, args=(to_address, subject, body), daemon=True).start()
    else:
        send_email_task.delay(to_address, subject, body)
