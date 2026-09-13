import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


from app.config.setting import get_settings
from app.model.models import Users, OtpVerification, Notifications
from app.model.mod_enum import AuthProvider, AccountStatus, UserAccountStatus, OtpPurpose, NotificationChannel

from app.services.phone.token.hash_password import generate_hash
from app.services.otp_management.otp_generator import generate_otp_code
from app.services.otp_management.otp_store import save_otp_code, format_ttl_duration
from app.services.otp_management.otp_management_service import OtpManagementService
from app.services.notifications.notification_service import NotificationService
from app.services.logs.system_log_service import SystemLogService
from app.services.sms.sms_service import send_otp_sms
from app.services.firebase_auth.firebase_auth import Auth
from app.services.email.tasks import send_email_task
from app.services.firebase_auth.auth_errors import (UnmatchedPassword,
                                                    UnverifiedAccountError,
                                                    DuplicateAccountError,
                                                    AccountDisabledError,
                                                    AccountNotFoundError
                                                    )
from app.schema.email_auth_schema import User_signup_response
from app.schema.phone_auth_schema import (PhoneNumberSignup,
                                          PhoneLogin,
                                          ForgotPasswordEmail,
                                          ForgotPasswordPhone
                                          )

class PhoneAuthentication(Auth):
    def __init__(self, security:HTTPBearer, session:AsyncSession):
        super().__init__(security=security, session=session)

    async def check_password_match(self, password:str, confirm_password:str) -> bool:
        """
        check if password match
        """
        return password == confirm_password

    async def check_existance(self, email:str, phone_number:str):
        """
        Run full check for any duplicate account
        """
        phone_exists = await self.check_phone_exists(phone_number=phone_number)
        email_exists = await self.check_email_exists(email=email)
        
        if phone_exists and email_exists:
            raise DuplicateAccountError("Both this phone number and email are already registered")
        elif phone_exists:
            raise DuplicateAccountError("This phone number is already registered")
        elif email_exists:
            raise DuplicateAccountError("This email address is already registered")

    async def send_otp_email(self, message:str, email_address:str, otp_expiry, code:str):
        """
        Function to send email during registration
        """
        send_email_task.delay(
            email_address,
            "Welcome to Simlexx Wealth",
            message
            )

    async def register(self, register_data:PhoneNumberSignup):
        """
        This function create a new user via phone number and password
        """
        register_data.email_address = str(register_data.email_address)

        await self.check_existance(
            email=register_data.email_address,
            phone_number=register_data.phone_number
            )

        if not await self.check_password_match(
            password=register_data.password,
            confirm_password=register_data.confirm_password
            ):
            raise UnmatchedPassword(detail="Password did not match")

        register_data.password = await asyncio.to_thread(
            generate_hash, password=register_data.password, rounds=12
            )
        try:
            new_user = Users(
                phone_number=register_data.phone_number,
                password=register_data.password,
                email_address=str(register_data.email_address),
                auth_provider=AuthProvider.PHONE_NUMBER,
                role=register_data.role,
                status=AccountStatus.ACTIVE,
                profile_verification_complete=UserAccountStatus.PENDING,
                phone_number_verified=False,
                email_verified=False
            )
            self.db_session.add(new_user)
            await self.db_session.commit()
        except sqlalchemy.exc.IntegrityError:
            await self.db_session.rollback()
            raise DuplicateAccountError(
                "Account already exists. Sigin instead"
                )
        await self.db_session.refresh(new_user)

        user_id = str(new_user.id)
        otp_code = str(generate_otp_code())
        otp_expiry_seconds = get_settings().OTP_TTL_SECONDS
        otp_expiry = format_ttl_duration(otp_expiry_seconds)
       
        message = (
            f"Hi, your account was created successfully.\n"
            f"Your OTP code is {otp_code} that expires after {otp_expiry}"
            )
        
        # Send first - Redis/notification/OTP-record persistence only
        # happens once the code has actually been dispatched to every
        # applicable channel (phone always, email only if provided).
        await send_otp_sms(phone_number=register_data.phone_number, code=otp_code)

        if register_data.email_address:
            await self.send_otp_email(
                message=message,
                email_address=str(register_data.email_address),
                otp_expiry=otp_expiry,
                code=otp_code,
                )

        await save_otp_code(
            identifier=register_data.phone_number,
            purpose=OtpPurpose.PHONE_VERIFICATION,
            code=otp_code
            )
        await OtpManagementService(self.db_session).create_otp(
            user_id=user_id,
            channel=NotificationChannel.SMS,
            code=otp_code,
            purpose=OtpPurpose.PHONE_VERIFICATION,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=otp_expiry_seconds),
        )
        await NotificationService(self.db_session).create_notification(
            user_id=user_id,
            channel=NotificationChannel.SMS,
            notification_type="welcome_otp",
            content=message,
        )

        await SystemLogService(self.db_session).create_log(
            user_id=user_id,
            action="register",
            entity="Users",
            entity_id=user_id,
            description="User registered via phone number and password",
        )

        return {
            "status":True,
            "message":"User created successfully",
            "data":User_signup_response.model_validate(new_user)
            }
