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
