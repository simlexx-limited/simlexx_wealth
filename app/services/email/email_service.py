import asyncio
import smtplib
import socket
from email.message import EmailMessage

from app.config.setting import Settings, get_settings
from app.services.email.email_error import (
    EmailSendError,
    InvalidRecipientError,
    EmailServiceUnavailableError,
)
from app.services.email.email_template import render_html_email

def send_message_via_smtp(message: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(message)


def build_and_send_email(to_address: str, subject: str, body: str) -> None:
    """
    Builds and sends an email synchronously - safe to call from a Celery
    worker task or via asyncio.to_thread from async code. Sends both a
    plain-text part (body as-is) and a styled HTML alternative, so clients
    that can't render HTML still get a readable email.
    """
    settings = get_settings()

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(render_html_email(subject, body), subtype="html")

    try:
        send_message_via_smtp(message, settings)
    except smtplib.SMTPRecipientsRefused as exc:
        raise InvalidRecipientError(f"Recipient address rejected: {to_address}") from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailServiceUnavailableError("SMTP authentication failed") from exc
    except (TimeoutError, ConnectionRefusedError, socket.gaierror) as exc:
        raise EmailServiceUnavailableError("Could not reach SMTP server") from exc
    except smtplib.SMTPException as exc:
        raise EmailServiceUnavailableError(f"Failed to send email: {exc}") from exc


async def send_email(to_address: str, subject: str, body: str) -> None:
    """
    Sends a plain-text email via SMTP without blocking the event loop.
    """
    await asyncio.to_thread(build_and_send_email, to_address, subject, body)
