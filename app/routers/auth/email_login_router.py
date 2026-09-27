#!/usr/bin/env python3

from functools import lru_cache

from fastapi import Depends, Request
from fastapi.routing import APIRouter
from fastapi.security import HTTPBearer

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import AuthProvider

from app.services.auth.firebase_auth.firebase_auth import authentication
from app.services.auth.firebase_auth.email_Auth import Email_Auth

from app.schema.email_auth_schema import EmailLogin, EmailSignINSchema, User_signup_schema
from app.services.auth.email_login_service import email_login
from app.services.rate_limiter.limiter import get_client_ip


router = APIRouter(
    prefix="/api/v1/auth/email_auth",
    tags=["Email-Password Authentication router"]
)

@router.post("/register")
async def create_account(
    new_user:User_signup_schema,
    session:AsyncSession = Depends(get_session),
    user_signin:dict = Depends(authentication().verify_credentials),
    ):
    """
    Registration router
    """
    email = user_signin.get("email","")
    user_id = user_signin.get("user_id", "")
    email_verified = user_signin.get("email_verified", False)

    signup_schema = EmailSignINSchema(firebase_uid=user_id,
                      email=email,
                      auth_provider=AuthProvider.FIREBASE_SIMPLE,
                      email_verified=email_verified)

    return await Email_Auth(
        security=HTTPBearer(),
        session=session
        ).register(
            new_user=new_user,
            email_sigin=signup_schema
            )


@router.post("/login")
async def login(
    request:Request,
    user_data:EmailLogin,
    session:AsyncSession = Depends(get_session),
    ):
    """
    Log in with email and password. Returns the token to send as the bearer
    token on every other request.
    """
    return await email_login(
        session,
        data=user_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )
