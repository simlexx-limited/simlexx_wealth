from typing import Optional

from fastapi import Request
from fastapi.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config.setting import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
)

OTP_RATE_LIMIT = f"{settings.OTP_ATTEMPT_LIMIT}/hour"

async def get_client_ip(request:Request) -> Optional[str]:
    return request.client.host if request.client else None

async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RateLimitExceeded)
    return _rate_limit_exceeded_handler(request, exc)
