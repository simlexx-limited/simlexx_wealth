from typing import Optional

from app.services.email.tasks import send_email_task

SIGN_IN_NOTE = (
    "Sign in, then change this password straight away. Until you do, your account "
    "can't be used for anything else."
)


def send_account_created_email(
    to_address: str,
    first_name: str,
    account_label: str,
    login_id: str,
    temporary_password: str,
) -> None:
    """
    Tell somebody an account was created for them, with the temporary password
    they must replace at first sign-in. Delivered in the background.
    """
    body = (
        f"Hi {first_name},\n\n"
        f"A Simlexx Wealth {account_label} account has been created for you.\n\n"
        f"Sign in with: {login_id}\n"
        f"Temporary password: {temporary_password}\n\n"
        f"{SIGN_IN_NOTE}\n\n"
        "If you were not expecting this email, please ignore it and tell us."
    )
    send_email_task.delay(to_address, "Your Simlexx Wealth account", body)


def send_password_reset_email(to_address: str, first_name: str, reset_link: str) -> None:
    """Send the one-time link for choosing a new password, after a Simlexx administrator started the reset."""
    body = (
        f"Hi {first_name},\n\n"
        "A Simlexx administrator started a password reset for your Simlexx Wealth account.\n\n"
        f"Choose a new password with this one-time link:\n{reset_link}\n\n"
        "If you did not expect this, contact us before using the link."
    )
    send_email_task.delay(to_address, "Reset your Simlexx Wealth password", body)


def send_account_status_email(to_address: str, first_name: str, active: bool, reason: Optional[str] = None) -> None:
    """Let the account owner know their account was deactivated or reactivated."""
    state = "reactivated" if active else "deactivated"
    body = f"Hi {first_name},\n\nYour Simlexx Wealth account has been {state}."
    if reason:
        body += f"\n\nReason: {reason}"
    send_email_task.delay(to_address, f"Your Simlexx Wealth account was {state}", body)
