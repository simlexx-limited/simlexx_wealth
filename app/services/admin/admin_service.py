import asyncio
from typing import Optional

import sqlalchemy
from fastapi.security import HTTPBearer
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import AccountStatus, AuthProvider, UserAccountStatus, UserRole
from app.model.models import Users
from app.schema.admin_schema import AdminCreate, AdminSignup
from app.schema.email_auth_schema import User_signup_response
from app.services.auth.firebase_auth.auth_errors import (
    AuthError,
    DuplicateAccountError,
    PermissionDeniedError,
    UnmatchedPassword,
)
from app.services.auth.phone.phone_auth import PhoneAuthentication
from app.services.auth.phone.token.hash_password import generate_hash
from app.services.auth.temp_password import generate_temporary_password
from app.services.agents.agent_service import AgentService
from app.services.email import agent_emails

# Arbitrary constant used as a Postgres advisory-lock key, so two simultaneous
# first-run requests are handled one after the other instead of both succeeding.
ADMIN_SETUP_LOCK_KEY = 7_274_010


class AdminService(PhoneAuthentication):
    def __init__(self, session: AsyncSession):
        super().__init__(security=HTTPBearer(), session=session)

    async def admin_exists(self) -> bool:
        count = await self.db_session.execute(
            select(func.count()).select_from(Users).where(Users.role == UserRole.ADMIN)
        )
        return count.scalar_one() > 0

    async def register_first_admin(
        self, data: AdminSignup, ip_address: Optional[str] = None, endpoint: str = ""
    ):
        """
        First-run setup: creates the Simlexx administrator, and only while no
        administrator exists. After the first one is written this refuses for
        everybody, so it can't be used to add admins later.

        The account is created unverified; the phone number is confirmed with
        the OTP through the normal /register/confirm endpoint before login works.
        """
        user_id: Optional[str] = None
        try:
            # Held until the transaction ends, so the "is there an admin?"
            # check and the insert below can't interleave with another caller's.
            await self.db_session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMIN_SETUP_LOCK_KEY}
            )
            if await self.admin_exists():
                raise PermissionDeniedError("Admin setup is already complete")

            await self.check_existance(email=str(data.email_address), phone_number=data.phone_number)

            if not await self.check_password_match(data.password, data.confirm_password):
                raise UnmatchedPassword(detail="Password did not match")

            password_hash = await asyncio.to_thread(generate_hash, password=data.password, rounds=12)
            try:
                admin = Users(
                    phone_number=data.phone_number,
                    email_address=str(data.email_address),
                    password=password_hash,
                    auth_provider=AuthProvider.PHONE_NUMBER,
                    role=UserRole.ADMIN,
                    status=AccountStatus.ACTIVE,
                    profile_verification_complete=UserAccountStatus.APPROVED,
                    phone_number_verified=True,
                    email_verified=True,
                )
                self.db_session.add(admin)
                await self.db_session.commit()
            except sqlalchemy.exc.IntegrityError:
                await self.db_session.rollback()
                raise DuplicateAccountError("Account already exists")
            await self.db_session.refresh(admin)

            user_id = str(admin.id)
            # Admins get their codes by email only, even though they log in by phone
            await self.send_verification_otp(
                user_id=user_id,
                phone_number=data.phone_number,
                email_address=str(data.email_address),
                email_only=True,
            )
        except AuthError as exc:
            await self.log_attempt(
                action="register_admin",
                endpoint=endpoint,
                ip_address=ip_address,
                user_id=user_id,
                description=f"Admin setup failed: {exc.detail}",
            )
            raise

        await self.log_attempt(
            action="register_admin",
            endpoint=endpoint,
            ip_address=ip_address,
            user_id=user_id,
            description="First administrator account created",
        )

        return {
            "status": True,
            "message": "Admin account created",
            "verification": "An OTP code has been sent to your email address",
            "data": User_signup_response.model_validate(admin),
        }

    async def create_admin(
        self, data: AdminCreate, created_by: str, ip_address: Optional[str] = None, endpoint: str = ""
    ) -> dict:
        """
        An existing administrator creates another one. The new admin signs in
        with their phone number and a temporary password that is emailed to them
        and must be changed at first sign-in.
        """
        email = str(data.email_address).lower()
        await AgentService(self.db_session).ensure_unique(email, data.phone_number)

        temporary_password = generate_temporary_password()
        password_hash = await asyncio.to_thread(generate_hash, password=temporary_password, rounds=12)
        try:
            admin = Users(
                phone_number=data.phone_number,
                email_address=email,
                password=password_hash,
                auth_provider=AuthProvider.PHONE_NUMBER,
                role=UserRole.ADMIN,
                status=AccountStatus.ACTIVE,
                profile_verification_complete=UserAccountStatus.PENDING,
                phone_number_verified=True,   # vouched for by the admin who created it
                email_verified=False,
                must_change_password=True,
            )
            self.db_session.add(admin)
            await self.db_session.commit()
        except sqlalchemy.exc.IntegrityError:
            await self.db_session.rollback()
            raise DuplicateAccountError("Account already exists")
        await self.db_session.refresh(admin)

        queued = True
        try:
            agent_emails.send_account_created_email(
                to_address=email, first_name=data.first_name, account_label="administrator",
                login_id=data.phone_number, temporary_password=temporary_password,
            )
        except Exception:
            queued = False

        await self.log_attempt(
            action="create_admin", endpoint=endpoint, ip_address=ip_address, user_id=created_by,
            description=f"Administrator {admin.id} created; credentials email "
                        f"{'queued' if queued else 'NOT queued'}",
        )
        return {
            "status": True,
            "message": "Administrator created. They sign in with their phone number and the "
                       "temporary password sent to their email, and must change it first",
            "credentials_email_queued": queued,
            "data": {"user_id": str(admin.id), "phone_number": data.phone_number, "email_address": email},
        }
