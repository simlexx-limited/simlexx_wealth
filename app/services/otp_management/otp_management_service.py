from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import NotificationChannel, OtpPurpose
from app.model.models import OtpVerification


class OtpManagementService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_otp(
        self,
        user_id: str,
        channel: NotificationChannel,
        code: str,
        purpose: OtpPurpose,
        expires_at: datetime,
        verified_at: Optional[datetime] = None,
    ) -> OtpVerification:
        """
        Saves an OTP record to the database. Pass verified_at for a code
        that's already verified at creation time (e.g. Firebase-verified
        emails, where there's no separate app-side verification step).
        """
        otp = OtpVerification(
            user_id=user_id,
            channel=channel,
            code=code,
            purpose=purpose,
            expires_at=expires_at,
            verified_at=verified_at,
        )
        self.session.add(otp)
        await self.session.commit()
        await self.session.refresh(otp)
        return otp

    async def get_latest_unverified_otp(
        self, user_id: str, purpose: OtpPurpose
        ) -> Optional[OtpVerification]:
        """
        Fetches the most recent not-yet-verified OTP record for a user and
        purpose - the row that corresponds to whatever code is currently
        live in Redis for that identifier/purpose.
        """
        statement = (
            select(OtpVerification)
            .where(
                OtpVerification.user_id == user_id,
                OtpVerification.purpose == purpose,
                OtpVerification.verified_at.is_(None),
            )
            .order_by(OtpVerification.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def record_attempt(self, otp: OtpVerification, verified: bool = False) -> OtpVerification:
        """
        Increments the attempt counter on an OTP record, and marks it
        verified (with a timestamp) if this attempt was the successful one.
        """
        otp.attempts += 1
        if verified:
            otp.verified_at = datetime.now(timezone.utc)
        self.session.add(otp)
        await self.session.commit()
        await self.session.refresh(otp)
        return otp

    async def get_all_otps(self, user_id: str) -> list[OtpVerification]:
        """
        Loads every OTP previously sent to a user.
        """
        statement = (
            select(OtpVerification)
            .where(OtpVerification.user_id == user_id)
            .order_by(OtpVerification.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
