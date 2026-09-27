from app.queue.celery_app import celery_app
from app.services.email.email_service import EmailServiceUnavailableError, build_and_send_email


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
