import logging

from app.config.setting import get_settings
from app.services.sms import sms_templates
from app.services.sms.africastalking_client import send_via_africastalking
from app.services.sms.console_client import send_via_console
from app.services.sms.sms_errors import SmsSendError
from app.services.sms.sms_result import SmsResult

logger = logging.getLogger(__name__)


def mask_phone(phone_number: str) -> str:
    """+256712345123 -> +2567*****123 - safe to put in logs."""
    if len(phone_number) <= 7:
        return "*" * len(phone_number)
    return phone_number[:5] + "*" * (len(phone_number) - 8) + phone_number[-3:]


async def send_sms(to: str, message: str) -> SmsResult:
    """
    Send one SMS now through the configured provider (SMS_PROVIDER). Use
    queue_sms (app.services.sms.tasks) for anything that doesn't need to
    reach the user before the request returns.

    Raises an SmsSendError subclass on failure.
    """
    provider = get_settings().SMS_PROVIDER
    try:
        if provider == "africastalking":
            result = await send_via_africastalking(to, message)
        else:
            result = await send_via_console(to, message)
    except SmsSendError as exc:
        logger.warning("SMS to %s via %s failed: %s", mask_phone(to), provider, type(exc).__name__)
        raise
    logger.info("SMS to %s via %s accepted (id=%s)", mask_phone(to), provider, result.message_id)
    return result


async def send_otp_sms(phone_number: str, code: str) -> SmsResult:
    """Send a one-time code by SMS, inline."""
    return await send_sms(phone_number, sms_templates.otp_message(code))
