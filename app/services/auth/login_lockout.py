from typing import Optional, cast

from app.config.setting import get_settings
from app.queue.redis import redis_client
from app.services.auth.firebase_auth.auth_errors import AccountLockedError
from app.services.otp_management.otp_store import format_ttl_duration


def phone_key(phone_number: str) -> str:
    return f"login_fail:phone:{phone_number}"


def ip_key(ip_address: str) -> str:
    return f"login_fail:ip:{ip_address}"


async def ensure_not_locked(phone_number: str, ip_address: Optional[str]) -> None:
    """
    Refuse the attempt outright when this phone number, or this IP address,
    has already used up its failed logins for the current window.
    """
    settings = get_settings()
    checks = [(phone_key(phone_number), settings.LOGIN_MAX_FAILED_ATTEMPTS)]
    if ip_address:
        checks.append((ip_key(ip_address), settings.LOGIN_IP_MAX_FAILED_ATTEMPTS))

    for key, limit in checks:
        count = cast("str | None", await redis_client.get(key))
        if count is not None and int(count) >= limit:
            remaining = int(await redis_client.ttl(key))
            raise AccountLockedError(
                "Too many failed login attempts. "
                f"Try again in {format_ttl_duration(max(remaining, 1))}"
            )


async def record_failure(phone_number: str, ip_address: Optional[str]) -> None:
    """
    Count one failed login against the phone number and the IP address.
    The window starts at the first failure and expires on its own.
    """
    window = get_settings().LOGIN_LOCKOUT_SECONDS
    keys = [phone_key(phone_number)]
    if ip_address:
        keys.append(ip_key(ip_address))

    for key in keys:
        await redis_client.incr(key)
        if int(await redis_client.ttl(key)) < 0:
            await redis_client.expire(key, window)


async def clear_failures(phone_number: str) -> None:
    """A successful login wipes the phone number's failures (the IP's are kept)."""
    await redis_client.delete(phone_key(phone_number))
