from datetime import datetime, timezone

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.security import HTTPBearer

from app.model.mod_enum import NotificationChannel, OtpPurpose
from app.services.auth.firebase_auth.firebase_auth import Auth
from app.services.auth.firebase_auth.auth_errors import DuplicateAccountError, UnverifiedAccountError
from app.services.otp_management.otp_management_service import OtpManagementService
from app.services.logs.system_log_service import SystemLogService
from app.schema.email_auth_schema import User_signup_schema, EmailSignINSchema, User_signup_response
from app.model.models import Users

class Google_Auth(Auth):
    def __init__(self, security:HTTPBearer, session:AsyncSession):
        super().__init__(security=security, session=session)
    
    async def register(self,
                       new_user:User_signup_schema,
                       email_sigin:EmailSignINSchema
                       ):
        """
        New User registration via Google sign-in
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
            role=new_user.role ,
            email_verified=True,
            phone_number_verified=False
        )

        try:
            self.db_session.add(user)
            await self.db_session.commit()
        except sqlalchemy.exc.IntegrityError:
            await self.db_session.rollback()
            raise DuplicateAccountError("Account already exists. Sigin instead")

        await self.db_session.refresh(user)
        user_id = str(user.id)

        verified_at = datetime.now(timezone.utc)
        await OtpManagementService(self.db_session).create_otp(
            user_id=user_id,
            channel=NotificationChannel.EMAIL,
            code="FIREBASE_VERIFIED",
            purpose=OtpPurpose.EMAIL_VERIFICATION,
            expires_at=verified_at,
            verified_at=verified_at,
        )
        await SystemLogService(self.db_session).create_log(
            user_id=user_id,
            action="register",
            entity="Users",
            entity_id=user_id,
            description="User registered via Firebase Google sign-in",
        )

        return {
            "status":True,
            "message":"User created successfully",
            "data":User_signup_response.model_validate(user)
            }
