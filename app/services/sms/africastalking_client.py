import logging
from typing import Any

import httpx

from app.config.setting import get_settings
from app.services.sms.sms_errors import (
    InvalidSmsRecipientError,
    SmsRejectedError,
    SmsServiceUnavailableError,
)
from app.services.sms.sms_result import SmsResult

logger = logging.getLogger(__name__)

# Per-recipient statusCode values from Africa's Talking's bulk SMS API.
ACCEPTED_CODES = {100, 101, 102}               # Processed, Sent, Queued
INVALID_RECIPIENT_CODES = {403, 404, 406}      # InvalidPhoneNumber, UnsupportedNumberType, UserInBlacklist
REJECTED_CODES = {401, 402, 405, 407}          # RiskHold, InvalidSenderId, InsufficientBalance, CouldNotRoute
UNAVAILABLE_CODES = {500, 501, 502}            # InternalServerError, GatewayError, RejectedByGateway


async def send_via_africastalking(to: str, message: str) -> SmsResult:
    """
    Send one SMS through Africa's Talking's REST API.

    Raises SmsServiceUnavailableError (retryable), InvalidSmsRecipientError
    or SmsRejectedError.
    """
    settings = get_settings()
    form = {"username": settings.AT_USERNAME, "to": to, "message": message}
    if settings.AT_SENDER_ID:
        form["from"] = settings.AT_SENDER_ID
    headers = {"apiKey": settings.AT_API_KEY, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=settings.SMS_TIMEOUT_SECONDS) as client:
            response = await client.post(settings.at_sms_url, data=form, headers=headers)
    except httpx.HTTPError as exc:
        raise SmsServiceUnavailableError() from exc

    return parse_response(response)


def parse_response(response: httpx.Response) -> SmsResult:
    if response.status_code >= 500:
        raise SmsServiceUnavailableError()
    if response.status_code >= 400:
        # 401 bad API key, 400 bad username/params - our config, not transient
        logger.error("Africa's Talking rejected the request: HTTP %s %s", response.status_code, response.text[:200])
        raise SmsRejectedError()

    try:
        data: Any = response.json()
        recipient = data["SMSMessageData"]["Recipients"][0]
        code = int(recipient["statusCode"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.error("Unexpected Africa's Talking response: %s", response.text[:200])
        raise SmsServiceUnavailableError() from exc

    status = str(recipient.get("status", ""))
    if code in ACCEPTED_CODES:
        return SmsResult(
            provider="africastalking",
            message_id=recipient.get("messageId"),
            cost=recipient.get("cost"),
            status=status,
        )
    if code in INVALID_RECIPIENT_CODES:
        raise InvalidSmsRecipientError()
    if code in UNAVAILABLE_CODES:
        raise SmsServiceUnavailableError()
    # REJECTED_CODES and anything unknown: needs attention on our side
    logger.error("Africa's Talking refused the SMS: statusCode=%s status=%s", code, status)
    raise SmsRejectedError()
