from fastapi import Depends, Request
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.schema.password_schema import PasswordChange
from app.services.auth.current_user import get_current_user_any_state
from app.services.auth.password_service import change_password
from app.services.rate_limiter.limiter import OTP_RATE_LIMIT, get_client_ip, limiter

router = APIRouter(
    prefix="/api/v1/auth/password",
    tags=["Password"],
    )

@router.post("/change")
@limiter.limit(OTP_RATE_LIMIT)
async def change_my_password(
    request:Request,
    data:PasswordChange,
    token_payload:dict = Depends(get_current_user_any_state),
    session:AsyncSession = Depends(get_session)
):
    """
    Change the password of the signed-in account. This is also how a temporary
    password is replaced: until it is, every other endpoint refuses the account.
    """
    return await change_password(
        session,
        user_id=token_payload["user_id"],
        data=data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )
