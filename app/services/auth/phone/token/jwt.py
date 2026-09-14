import jwt

from datetime import datetime, timedelta, timezone

from app.config.setting import get_settings

class JwtManagement:
    def __init__(self):
        pass

    async def encode_auth(self, data:dict, expires_delta: timedelta | None = None) -> str:
        """
        Encodes data into a signed JWT,
        adding an expiry claim.
        """
        settings = get_settings()
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
        payload = data.copy()
        payload["exp"] = datetime.now(timezone.utc) + expires_delta
        return jwt.encode(payload, settings.SECRET_ACCESS_KEY, algorithm=settings.ALGORITHM)

    async def decode_auth(self, token: str) -> dict:
        """
        Decodes and verifies a JWT
        """
        settings = get_settings()
        return jwt.decode(token, settings.SECRET_ACCESS_KEY, algorithms=[settings.ALGORITHM])
