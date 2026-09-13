from fastapi import Depends
from fastapi.routing import APIRouter
from fastapi.security import HTTPBearer

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session

from app.services.phone.phone_auth import PhoneAuthentication

from app.schema.phone_auth_schema import PhoneNumberSignup, PhoneLogin

router = APIRouter(
    prefix="/api/v1/auth/phone_auth",
    tags=["Phone Number Auth"]
    )

@router.post("/register")
async def create_user(
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
    return await phone_auth.register(register_data=user_data)
