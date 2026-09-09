import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

from firebase_admin import auth

from functools import lru_cache

from app.services.firebase_auth.firebase_auth import Auth
from app.services.firebase_auth.auth_errors import DuplicateAccountError, UnverifiedAccountError
from app.schema.email_auth_schema import User_signup_schema, EmailSignINSchema, User_signup_response
from app.model.models import Users

class Email_Auth(Auth):
    def __init__(self, security:HTTPBearer, session:AsyncSession):
        super().__init__(security=security, session=session)
    
    async def register(self,
                       new_user:User_signup_schema,
                       email_sigin:EmailSignINSchema
                       ):
        """
        New User registration with email and password
        """
        if not email_sigin.email_verified:
            raise UnverifiedAccountError(
                "Email address not verified! Ensure to verify your email before continue"
                )

        email_sigin.email = str(email_sigin.email)

        phone_exists = await self.check_phone_exists(phone_number=new_user.phone_number)
        email_exists = await self.check_email_exists(email=email_sigin.email)

        if phone_exists and email_exists:
            raise DuplicateAccountError("Both this phone number and email are already registered")
        elif phone_exists:
            raise DuplicateAccountError("This phone number is already registered")
        elif email_exists:
            raise DuplicateAccountError("This email address is already registered")

        user = Users(
            email_address=email_sigin.email,
            phone_number=new_user.phone_number,
            auth_provider = email_sigin.auth_provider,
            firebase_uid = email_sigin.firebase_uid,
            role=new_user.role 
        )
        
        try:
            self.db_session.add(user)
            await self.db_session.commit()
        except sqlalchemy.exc.IntegrityError:
            await self.db_session.rollback()
            raise DuplicateAccountError("Account already exists. Sigin instead")

        await self.db_session.refresh(user)
        return {
            "status":True,
            "message":"User created successfully",
            "data":User_signup_response.model_validate(user)
            }
