import jwt as pyjwt

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.auth.phone.token.jwt import JwtManagement


_bearer = HTTPBearer()


async def verify_jwt_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency that validates the JWT issued by PhoneAuthentication.login.

    Returns the decoded token payload which includes:
        - user_id  : str
        - firebase_uid : str | None
        - role     : str

    Raises HTTP 401 if the token is missing, expired, or tampered.
    """
    token = credentials.credentials
    try:
        payload = await JwtManagement().decode_auth(token)
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or malformed token",
            headers={"WWW-Authenticate": "Bearer"},
        )
