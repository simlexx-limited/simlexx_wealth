from fastapi import Depends, Request
from fastapi.routing import APIRouter
from fastapi.security import HTTPBearer

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session

from app.services.auth.phone.phone_auth import PhoneAuthentication
from app.services.rate_limiter.limiter import limiter, OTP_RATE_LIMIT, get_client_ip

from app.schema.phone_auth_schema import PhoneNumberSignup, PhoneLogin, ConfirmOTP, RequestOTP, ChangePassword

router = APIRouter(
    prefix="/api/v1/auth/phone_auth",
    tags=["Phone Number Auth"]
    )

@router.post("/register")
async def create_user(
    request:Request,
    user_data:PhoneNumberSignup,
    session:AsyncSession = Depends(get_session)
):
    """
    Phone authentication
    """
    phone_auth = PhoneAuthentication(
        security=HTTPBearer(),
        session=session
        )
    return await phone_auth.register(
        register_data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )

@router.post("/register/confirm")
@limiter.limit(OTP_RATE_LIMIT)
async def confirm_account(
    request:Request,
    user_data:ConfirmOTP,
    session:AsyncSession = Depends(get_session)
    ):
    phone_auth = PhoneAuthentication(
            security=HTTPBearer(),
            session=session
            )
    return await phone_auth.confirm_account(
        user_data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )

@router.post("/login")
@limiter.limit(OTP_RATE_LIMIT)
async def login(
    request:Request,
    user_data:PhoneLogin,
    session:AsyncSession = Depends(get_session)
):
    phone_auth = PhoneAuthentication(
                security=HTTPBearer(),
                session=session
                )
    return await phone_auth.login(
        user_data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )

@router.post("/login/otp")
async def request_otp(
    request:Request,
    user_data:RequestOTP,
    session:AsyncSession = Depends(get_session)
):
    phone_auth = PhoneAuthentication(
                security=HTTPBearer(),
                session=session
                )
    return await phone_auth.request_otp(
        user_data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )

@router.post("/login/change_password")
@limiter.limit(OTP_RATE_LIMIT)
async def change_password(
    request:Request,
    user_data:ChangePassword,
    session:AsyncSession = Depends(get_session)
    ):
    phone_auth = PhoneAuthentication(
                security=HTTPBearer(),
                session=session
                )
    return await phone_auth.change_password(
        user_data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )
