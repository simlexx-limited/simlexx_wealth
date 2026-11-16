"""SMS texts. Keep each under 160 GSM-7 characters so it's billed as one part."""
from decimal import Decimal

from app.config.setting import get_settings
from app.services.otp_management.otp_store import format_ttl_duration

SMS_MAX_LENGTH = 160


def otp_message(code: str) -> str:
    validity = format_ttl_duration(get_settings().OTP_TTL_SECONDS)
    return f"Your Simlexx code is {code}. It expires in {validity}. Never share it with anyone."


def deposit_confirmed(amount: Decimal | str, currency: str) -> str:
    return f"Simlexx: your deposit of {amount} {currency} was confirmed."


def withdrawal_confirmed(amount: Decimal | str, currency: str) -> str:
    return f"Simlexx: your withdrawal of {amount} {currency} was confirmed."
