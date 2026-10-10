from decimal import Decimal
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import Users, UserAccount, UserGoals
from app.model.mod_enum import NotificationChannel
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError
from app.services.email.tasks import send_email_task
from app.services.sms.sms_service import send_otp_sms


class Transaction:
    def __init__(self, session:AsyncSession):
        self.db_session = session

    def apply_to_balance(
            self,
            account:UserAccount,
            amount:Decimal,
            goal:Optional[UserGoals] = None,
            credit:bool = True,
            ) -> None:
        """
        Adjust the account's balance by amount, crediting it for a deposit or
        debiting it for a withdrawal - and the goal's current_amount too, if
        one is linked. Caller still has to commit.
        """
        delta = amount if credit else -amount
        account.balance = cast(Decimal, account.balance) + delta   # type: ignore[assignment]
        if goal is not None:
            goal.current_amount = cast(Decimal, goal.current_amount) + delta   # type: ignore[assignment]

    async def get_verified_user(self, user_id:str) -> Users:
        result = await self.db_session.execute(select(Users).where(Users.id == user_id))
        user_data = result.scalar_one_or_none()
        if user_data is None:
            raise AccountNotFoundError(detail="Account not found")
        return user_data

    def resolve_notification_channel(self, user_data:Users) -> tuple[NotificationChannel, str]:
        """
        Pick whichever channel is verified - email first, then phone - and
        return it with the destination to send to. Reused by every step that
        needs to notify the user (sending an OTP, confirming a deposit or a
        withdrawal).
        """
        if user_data.email_verified:
            return NotificationChannel.EMAIL, str(user_data.email_address)
        if user_data.phone_number_verified:
            return NotificationChannel.SMS, str(user_data.phone_number)
        raise HTTPException(status_code=400, detail="No verified email or phone number to notify the user on")

    async def notify_user(self, channel:NotificationChannel, destination:str, subject:str, message:str) -> None:
        """Send message over whichever channel resolve_notification_channel picked."""
        if channel == NotificationChannel.EMAIL:
            send_email_task.delay(destination, subject, message)
        else:
            await send_otp_sms(destination, message)
