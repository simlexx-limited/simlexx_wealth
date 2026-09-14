from typing import cast

from app.config.setting import get_settings
from app.model.mod_enum import OtpPurpose
from app.queue.redis import redis_client


async def save_otp_code(identifier: str, purpose: OtpPurpose, code: str) -> None:
    """
    Stores an OTP code in Redis, scoped by purpose so codes for different
    flows (email verification, forgot password, etc.) never collide.
    """
    settings = get_settings()
    key = f"otp:{purpose.value}:{identifier}"
    await redis_client.set(key, code, ex=settings.OTP_TTL_SECONDS)

async def get_otp_code(identifier: str, purpose: OtpPurpose) -> str | None:
    """
    Retrieve saved otp
    """
    key = f"otp:{purpose.value}:{identifier}"
    # redis_client is constructed with decode_responses=True, so this is
    # always a str at runtime - the redis-py stubs don't know that flag was
    # set and report a broader bytes|str|None return type.
    return cast("str | None", await redis_client.get(key))

async def delete_otp_code(identifier: str, purpose: OtpPurpose) -> None:
    """
    Deletes a saved OTP code - e.g. once it's been successfully used, so it
    can't be replayed a second time.
    """
    key = f"otp:{purpose.value}:{identifier}"
    await redis_client.delete(key)

def format_ttl_duration(seconds: int) -> str:
    """
    Formats a TTL in seconds as a human-readable duration for user-facing
    messages (e.g. "24 hours", "30 minutes") - kept in sync with
    OTP_TTL_SECONDS so the copy never drifts from the real expiry.
    """
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60

    if hours and minutes:
        return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"
