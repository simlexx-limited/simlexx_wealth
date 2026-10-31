from decimal import Decimal
from typing import Optional, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import Users, UserAccount, UserGoals, Transaction as TransactionModel
from app.model.mod_enum import CurrencyType, NotificationChannel, TransactionStatus, TransactionType
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError
from app.services.email.tasks import queue_email
from app.services.logs.system_log_service import SystemLogService
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

    async def record_failed_transaction(
            self,
            user_id:str,
            account_id:str,
            goal_id:Optional[str],
            transaction_type:TransactionType,
            amount:Decimal,
            currency:CurrencyType,
            requested_amount:Decimal,
            requested_currency:CurrencyType,
            failure_reason:str,
            bank_reference:Optional[str] = None,
            unit_master_reference:Optional[str] = None,
            fx_rate_to_base:Optional[Decimal] = None,
            ) -> TransactionModel:
        """
        Records a transaction that was attempted - the OTP had already been
        verified - but could not complete, so there's an audit trail of what
        was tried. The account's balance and any linked goal are left
        untouched; only a successful transaction ever calls apply_to_balance.
        """
        failed = TransactionModel(
            user_id=user_id, account_id=account_id, goal_id=goal_id, type=transaction_type,
            amount=amount, currency=currency, requested_amount=requested_amount,
            requested_currency=requested_currency, fx_rate_to_base=fx_rate_to_base,
            status=TransactionStatus.FAILED, failure_reason=failure_reason,
            bank_reference=bank_reference, unit_master_reference=unit_master_reference,
        )
        self.db_session.add(failed)
        await self.db_session.commit()
        await self.db_session.refresh(failed)

        await SystemLogService(self.db_session).create_log(
            action=f"{transaction_type.value}_failed", entity="Transaction", user_id=user_id,
            entity_id=str(failed.id), description=failure_reason,
        )
        return failed

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
            queue_email(destination, subject, message)
        else:
            await send_otp_sms(destination, message)
