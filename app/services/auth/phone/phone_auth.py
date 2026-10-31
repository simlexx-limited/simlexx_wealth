import asyncio
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


from app.config.setting import get_settings
from app.model.models import Users, OtpVerification, Notifications
from app.model.mod_enum import AuthProvider, AccountStatus, UserAccountStatus, UserRole, OtpPurpose, NotificationChannel

from app.services.auth.phone.token.hash_password import generate_hash, compare_hash
from app.services.otp_management.otp_generator import generate_otp_code
from app.services.otp_management.otp_store import save_otp_code, format_ttl_duration, get_otp_code, delete_otp_code
from app.services.otp_management.otp_management_service import OtpManagementService
from app.services.notifications.notification_service import NotificationService
from app.services.logs.system_log_service import SystemLogService
from app.services.sms.sms_service import send_otp_sms
from app.services.auth.firebase_auth.firebase_auth import Auth
from app.services.email.tasks import queue_email
from app.services.auth.phone.token.jwt import JwtManagement
from app.services.auth import login_lockout
from app.services.auth.firebase_auth.auth_errors import (AuthError,
                                                    UnmatchedPassword,
                                                    UnverifiedAccountError,
                                                    DuplicateAccountError,
                                                    AccountDisabledError,
                                                    AccountNotFoundError,
                                                    InvalidCredentialsError,
                                                    PasswordReuseError
                                                    )
from app.schema.email_auth_schema import User_signup_response
from app.schema.phone_auth_schema import (PhoneNumberSignup,
                                          PhoneLogin,
                                          ForgotPasswordEmail,
                                          ForgotPasswordPhone,
                                          ConfirmOTP,
                                          RequestOTP,
                                          ChangePassword
                                          )

@lru_cache
def dummy_hash() -> str:
    """
    A throwaway hash, so a login for an unknown phone number still pays for one
    bcrypt comparison and can't be told apart from a wrong password by timing.
    """
    return generate_hash(password="not-a-real-password", rounds=12)


class PhoneAuthentication(Auth):
    def __init__(self, security:HTTPBearer, session:AsyncSession):
        super().__init__(security=security, session=session)

    async def check_password_match(self, password:str, confirm_password:str) -> bool:
        """
        check if password match
        """
        return password == confirm_password

    async def check_existance(self, email:Optional[str], phone_number:str):
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

    async def find_user_by_phone_or_email(
        self, phone_number:str, email_address:Optional[str] = None
        ) -> Optional[Users]:
        """
        Looks up a user by phone number, optionally also matching by email.
        Only includes the email clause when one is actually provided -
        comparing a SQLAlchemy column to Python None becomes an "IS NULL"
        check, which would otherwise match every user with no email on file.
        """
        conditions = [Users.phone_number == phone_number]
        if email_address:
            conditions.append(Users.email_address == email_address)
        stment = select(Users).where(or_(*conditions))
        search = await self.db_session.execute(stment)
        return search.scalar_one_or_none()

    async def log_attempt(
        self,
        action:str,
        endpoint:str,
        ip_address:Optional[str],
        user_id:Optional[str] = None,
        description:Optional[str] = None,
        ) -> None:
        """
        Records one authentication attempt (success or failure) to the
        system log, with the request's IP and endpoint for audit purposes.
        """
        await SystemLogService(self.db_session).create_log(
            user_id=user_id,
            action=action,
            entity="Users",
            entity_id=user_id,
            description=description,
            ip_address=ip_address,
            endpoint=endpoint,
        )

    async def send_otp_email(self, message:str, email_address:str, otp_expiry, code:str):
        """
        Function to send email during registration
        """
        queue_email(
            email_address,
            "Welcome to Simlexx Wealth",
            message
            )

    async def send_verification_otp(
        self, user_id:str, phone_number:str, email_address:Optional[str], email_only:bool=False
        ) -> None:
        """
        Sends the phone-verification OTP for a new account (SMS always, email if
        the account has one; only email when email_only is set), then stores it
        in Redis and Postgres and records the notification.
        """
        channel = NotificationChannel.EMAIL if email_only else NotificationChannel.SMS
        otp_code = str(generate_otp_code())
        otp_expiry_seconds = get_settings().OTP_TTL_SECONDS
        otp_expiry = format_ttl_duration(otp_expiry_seconds)

        message = (
            f"Hi, your account was created successfully.\n"
            f"Your OTP code is {otp_code} that expires after {otp_expiry}"
            )

        if not email_only:
            await send_otp_sms(phone_number=phone_number, code=otp_code)

        if email_address:
            await self.send_otp_email(
                message=message,
                email_address=str(email_address),
                otp_expiry=otp_expiry,
                code=otp_code,
                )

        await save_otp_code(
            identifier=user_id,
            purpose=OtpPurpose.PHONE_VERIFICATION,
            code=otp_code
            )
        await OtpManagementService(self.db_session).create_otp(
            user_id=user_id,
            channel=channel,
            code=otp_code,
            purpose=OtpPurpose.PHONE_VERIFICATION,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=otp_expiry_seconds),
        )
        await NotificationService(self.db_session).create_notification(
            user_id=user_id,
            channel=channel,
            notification_type="welcome_otp",
            content=message,
        )

    async def register(self, register_data:PhoneNumberSignup, ip_address:Optional[str]=None, endpoint:str=""):
        """
        This function create a new user via phone number and password
        """
        user_id: Optional[str] = None
        try:
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
                    email_address=register_data.email_address,
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
            await self.send_verification_otp(
                user_id=user_id,
                phone_number=register_data.phone_number,
                email_address=register_data.email_address,
                )
        except AuthError as exc:
            await self.log_attempt(
                action="register",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"Registration failed: {exc.detail}",
                )
            raise

        await self.log_attempt(
            action="register",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="User registered via phone number and password",
            )

        return {
            "status":True,
            "message":"User created successfully",
            "verification":"An OTP code has been sent to your phone number",
            "data":User_signup_response.model_validate(new_user)
            }

    async def confirm_account(self, user_data:ConfirmOTP, ip_address:Optional[str]=None, endpoint:str=""):
        """
        This function confirms account (phone or email provided)
        """
        user_id: Optional[str] = None
        try:
            result = await self.find_user_by_phone_or_email(
                phone_number=user_data.phone_number,
                email_address=user_data.email_address,
                )
            if not result:
                raise AccountNotFoundError(detail="This account does not exist! Register your account first")
            user_id = str(result.id)
            stored_code = await get_otp_code(identifier=user_id, purpose=OtpPurpose.PHONE_VERIFICATION)
            if not stored_code:
                raise UnverifiedAccountError(detail="The provided code has already expired. Request a new one")

            is_valid = user_data.otp_code == stored_code
            otp_record = await OtpManagementService(self.db_session).get_latest_unverified_otp(
                user_id=user_id, purpose=OtpPurpose.PHONE_VERIFICATION
                )
            if otp_record:
                await OtpManagementService(self.db_session).record_attempt(otp_record, verified=is_valid)

            if not is_valid:
                raise UnverifiedAccountError(detail="Invalid code. Check your device for the valid code")

            result.phone_number_verified = True

            await self.db_session.commit()
            await self.db_session.refresh(result)

            await delete_otp_code(identifier=user_id, purpose=OtpPurpose.PHONE_VERIFICATION)
            await NotificationService(self.db_session).mark_latest_as_read(
                user_id=user_id, notification_type="welcome_otp"
                )
        except AuthError as exc:
            await self.log_attempt(
                action="confirm_account",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"Phone confirmation failed: {exc.detail}",
                )
            raise

        await self.log_attempt(
            action="confirm_account",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="Phone number verified",
            )

        return {
            "success":True,
            "message":"Phone number verified"
        }

    async def login(self, user_data:PhoneLogin, ip_address:Optional[str]=None, endpoint:str=""):
        """
        login service
        """
        user_id: Optional[str] = None
        try:
            await login_lockout.ensure_not_locked(user_data.phone_number, ip_address)

            stment = select(Users).where(Users.phone_number == user_data.phone_number)
            search = await self.db_session.execute(stment)
            result = search.scalar_one_or_none()
            user_id = str(result.id) if result else None

            # The password is always checked first, and unknown numbers, accounts
            # without a password yet and wrong passwords all give the same answer,
            # so this endpoint can't be used to find out who has an account.
            stored_hash = str(result.password) if result and result.password else dummy_hash()
            verify_password = await asyncio.to_thread(
                compare_hash, password=user_data.password, save_password=stored_hash
                )
            if not (result and result.password and verify_password):
                await login_lockout.record_failure(user_data.phone_number, ip_address)
                raise InvalidCredentialsError(detail="Invalid phone number or password")

            if not result.phone_number_verified:
                raise UnverifiedAccountError(detail="Account phone number not verified")
            if result.status not in (AccountStatus.ACTIVE, AccountStatus.REACTIVE):
                raise AccountDisabledError()
        except AuthError as exc:
            await self.log_attempt(
                action="login",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"Login failed: {exc.detail}",
                )
            raise

        await login_lockout.clear_failures(user_data.phone_number)

        info_to_encrypt = {
            "user_id":result.id,
            "firebase_uid":result.firebase_uid,
            "role":result.role,
            "token_version":result.token_version or 0,
        }
        # Administrators get a shorter session than everyone else
        expires_delta = (
            timedelta(minutes=get_settings().ADMIN_JWT_EXPIRE_MINUTES)
            if result.role == UserRole.ADMIN else None
        )
        jwt_management = JwtManagement()
        token = await jwt_management.encode_auth(data=info_to_encrypt, expires_delta=expires_delta)

        await self.log_attempt(
            action="login",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="Login successful",
            )

        return {
            "status":True,
            "message":"Login successful",
            "token":token,
            "role":UserRole(result.role).value,
            "must_change_password":bool(result.must_change_password)
        }

    async def request_otp(self, user_data:RequestOTP, ip_address:Optional[str]=None, endpoint:str=""):
        """
        Requests a new OTP for password reset - sends it via phone (always)
        and email (if the account has one), then persists it in Postgres.
        """
        user_id: Optional[str] = None
        try:
            result = await self.find_user_by_phone_or_email(
                phone_number=user_data.phone_number,
                email_address=user_data.email_address,
                )
            if not result:
                raise AccountNotFoundError(detail="This account does not exist! Register your account first")

            user_id = str(result.id)
            otp_code = str(generate_otp_code())
            otp_expiry_seconds = get_settings().OTP_TTL_SECONDS
            otp_expiry = format_ttl_duration(otp_expiry_seconds)

            message = (
                f"Hi, you requested to reset your password.\n"
                f"Your OTP code is {otp_code} that expires after {otp_expiry}"
                )

            # Admins get their codes by email only, even though they log in by phone
            email_only = result.role == UserRole.ADMIN and bool(result.email_address)
            if not email_only:
                await send_otp_sms(phone_number=str(result.phone_number), code=otp_code)

            if result.email_address:
                await self.send_otp_email(
                    message=message,
                    email_address=str(result.email_address),
                    otp_expiry=otp_expiry,
                    code=otp_code,
                    )

            await save_otp_code(
                identifier=user_id,
                purpose=OtpPurpose.FORGOT_PASSWORD,
                code=otp_code
                )
            await OtpManagementService(self.db_session).create_otp(
                user_id=user_id,
                channel=NotificationChannel.EMAIL if email_only else NotificationChannel.SMS,
                code=otp_code,
                purpose=OtpPurpose.FORGOT_PASSWORD,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=otp_expiry_seconds),
            )
        except AuthError as exc:
            await self.log_attempt(
                action="request_otp",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"OTP request failed: {exc.detail}",
                )
            raise

        await self.log_attempt(
            action="request_otp",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="OTP sent for password reset",
            )

        return {
            "status":True,
            "message":"OTP sent successfully"
        }

    async def change_password(self, user_data:ChangePassword, ip_address:Optional[str]=None, endpoint:str=""):
        """
        Verifies the OTP then updates the account's password, rejecting
        reuse of the current password.
        """
        user_id: Optional[str] = None
        try:
            result = await self.find_user_by_phone_or_email(
                phone_number=user_data.phone_number,
                email_address=user_data.email_address,
                )
            if not result:
                raise AccountNotFoundError(detail="This account does not exist! Register your account first")

            user_id = str(result.id)
            stored_code = await get_otp_code(identifier=user_id, purpose=OtpPurpose.FORGOT_PASSWORD)
            if not stored_code:
                raise UnverifiedAccountError(detail="The provided code has already expired. Request a new one")

            is_valid = user_data.otp_code == stored_code
            otp_record = await OtpManagementService(self.db_session).get_latest_unverified_otp(
                user_id=user_id, purpose=OtpPurpose.FORGOT_PASSWORD
                )
            if otp_record:
                await OtpManagementService(self.db_session).record_attempt(otp_record, verified=is_valid)

            if not is_valid:
                raise UnverifiedAccountError(detail="Invalid code. Check your device for the valid code")

            if not await self.check_password_match(
                password=user_data.new_password,
                confirm_password=user_data.confirm_password
                ):
                raise UnmatchedPassword(detail="Password did not match")

            same_as_current = await asyncio.to_thread(
                compare_hash, password=user_data.new_password, save_password=str(result.password)
                )
            if same_as_current:
                raise PasswordReuseError()

            new_hash: str = await asyncio.to_thread(
                generate_hash, password=user_data.new_password, rounds=12
                )
            result.password = new_hash
            result.token_version = (result.token_version or 0) + 1  # sign out every existing session
            await self.db_session.commit()
            await self.db_session.refresh(result)

            await delete_otp_code(identifier=user_id, purpose=OtpPurpose.FORGOT_PASSWORD)
        except AuthError as exc:
            await self.log_attempt(
                action="change_password",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"Password change failed: {exc.detail}",
                )
            raise

        await self.log_attempt(
            action="change_password",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="Password updated successfully",
            )

        return {
            "status":True,
            "message":"Password updated successfully"
        }
