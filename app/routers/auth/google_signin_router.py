from fastapi import Depends
from fastapi.routing import APIRouter
from fastapi.security import HTTPBearer

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import AuthProvider

from app.services.auth.firebase_auth.firebase_auth import authentication
from app.services.auth.firebase_auth.google_auth import Google_Auth

from app.schema.email_auth_schema import EmailSignINSchema, User_signup_schema


router = APIRouter(
    prefix="/api/v1/auth/google_signin",
    tags=["Google Auth Signin"]
    )

@router.post("/register")
async def register(
    user_data:User_signup_schema,
    db_session:AsyncSession = Depends(get_session),
    user_signin:dict = Depends(authentication().verify_credentials)
):
    email = user_signin.get("email","")
    user_id = user_signin.get("user_id", "")
    email_verified = user_signin.get("email_verified", False)
    signin_provider = user_signin.get("firebase", {}).get("sign_in_provider", "")
    user_name = user_signin.get("name", "")

    signin_data = EmailSignINSchema(
        firebase_uid=user_id,
        email=email,
        auth_provider=AuthProvider.FIREBASE_GOOGLE,
        email_verified=email_verified
    )
    auth_service = Google_Auth(
        security=HTTPBearer(),
        session=db_session
    )
    result = await auth_service.register(new_user=user_data, email_sigin=signin_data)
    result["signin_provider"] = signin_provider
    result["user_name"] = user_name
    return result
