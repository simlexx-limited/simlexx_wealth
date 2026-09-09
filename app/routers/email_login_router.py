#!/usr/bin/env python3

from functools import lru_cache

from fastapi import Depends
from fastapi.routing import APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import AuthProvider

from app.services.firebase_auth.firebase_auth import Auth
from app.services.firebase_auth.email_Auth import Email_Auth

from app.schema.email_auth_schema import EmailSignINSchema, User_signup_schema


router = APIRouter(
    prefix="/api/v1/email_auth",
    tags=["Email-Password Authentication router"]
)

@lru_cache
def authentication():
    return Auth(security=HTTPBearer(), session=None)

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
