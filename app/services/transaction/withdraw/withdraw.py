#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.model.models import (
    UserAccount,
    Transaction as TransactionModel,
    UserGoals,
    MoreAboutUsers,
    )

from app.config.setting import get_settings
from app.model.mod_enum import NotificationChannel, OtpPurpose, TransactionStatus, TransactionType
from app.schema.deposit import SendUserOTP
from app.schema.withdraw import UserWithdraw, WithdrawResponse
from app.services.exchange_rate.exchange_rate_service import get_exchange_rate
from app.services.logs.system_log_service import SystemLogService
from app.services.otp_management.otp_generator import generate_otp_code
from app.services.otp_management.otp_store import save_otp_code, get_otp_code, delete_otp_code, format_ttl_duration
from app.services.transaction.transaction import Transaction
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError, InsufficientBalanceError, InvalidOtpError
from app.services.otp_management.otp_management_service import OtpManagementService


class Withdraw(Transaction):
    def __init__(self, session:AsyncSession):
        super().__init__(session=session)
        self.otp_generator = OtpManagementService(session=session)

    async def send_otp_withdraw(self,
                                user_id:str,
                                ip_address:Optional[str] = None,
                                ) -> SendUserOTP:
        """
        Send the user a one-time code to confirm an upcoming withdrawal - by
        email if their email is verified, otherwise by phone if their phone
        number is verified. Nothing about the withdrawal itself is saved
        here - a separate function saves it once this code has been verified.
        """
        user_data = await self.get_verified_user(user_id)
        channel, destination = self.resolve_notification_channel(user_data)

        otp_code = str(generate_otp_code())
        await save_otp_code(identifier=user_id, purpose=OtpPurpose.WITHDRAW, code=otp_code)
        await self.otp_generator.create_otp(
            user_id=user_id,
            channel=channel,
            code=otp_code,
            purpose=OtpPurpose.WITHDRAW,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=get_settings().OTP_TTL_SECONDS),
        )

        if channel == NotificationChannel.EMAIL:
            message = (
                f"Your one-time code to confirm this withdrawal is {otp_code}. It expires in "
                f"{format_ttl_duration(get_settings().OTP_TTL_SECONDS)}."
            )
        else:
            message = otp_code
        await self.notify_user(channel, destination, "Confirm your withdrawal", message)

        await SystemLogService(self.db_session).create_log(
            action="send_otp_withdraw", entity="OtpVerification", user_id=user_id,
            description=f"Withdrawal OTP sent via {channel.value}",
            ip_address=ip_address, endpoint="",
        )

        return SendUserOTP(
            email_address=destination if channel == NotificationChannel.EMAIL else None,
            phone_number=destination if channel == NotificationChannel.SMS else None,
            otp_code=otp_code,
        )

    async def save_withdraw(
            self,
            user_withdraw:UserWithdraw,
            user_id:str,
            otp_code:str,
            ip_address:Optional[str] = None,
            ) -> WithdrawResponse:
        """
        Verify the OTP sent by send_otp_withdraw, and if it's valid, save the
        withdrawal. A withdrawal may or may not be drawn against a goal -
        goal_id is only checked when the client actually sent one.

        Raises AccountNotFoundError if the account (or goal, if given)
        doesn't belong to user_id, InvalidOtpError if the code is wrong,
        expired, or was never issued, and InsufficientBalanceError if the
        account doesn't hold enough to cover this withdrawal.
        """
        user_data = await self.get_verified_user(user_id)

        account_result = await self.db_session.execute(
            select(UserAccount).where(
                UserAccount.id == user_withdraw.account_id,
                UserAccount.user_id == user_id,
            )
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(detail="Account not found")

        goal = None
        if user_withdraw.goal_id is not None:
            goal_result = await self.db_session.execute(
                select(UserGoals).where(
                    UserGoals.id == user_withdraw.goal_id,
                    UserGoals.user_id == user_id,
                )
            )
            goal = goal_result.scalar_one_or_none()
            if goal is None:
                raise AccountNotFoundError(detail="Goal not found")

        stored_code = await get_otp_code(identifier=user_id, purpose=OtpPurpose.WITHDRAW)
        is_valid = stored_code is not None and stored_code == otp_code

        otp_record = await self.otp_generator.get_latest_unverified_otp(
            user_id=user_id, purpose=OtpPurpose.WITHDRAW
        )
        if otp_record is not None:
            await self.otp_generator.record_attempt(otp_record, verified=is_valid)

        if not is_valid:
            raise InvalidOtpError()

        await delete_otp_code(identifier=user_id, purpose=OtpPurpose.WITHDRAW)

        # The account is always debited in its own currency - convert the
        # client's requested payout into it before checking the balance.
        rate = await get_exchange_rate(user_withdraw.currency, account.currency)
        debit_amount = (user_withdraw.amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        debit_currency = account.currency

        if debit_amount > cast(Decimal, account.balance):
            raise InsufficientBalanceError()

        new_transaction = TransactionModel(
            user_id=user_id,
            account_id=user_withdraw.account_id,
            goal_id=user_withdraw.goal_id,
            type=TransactionType.WITHDRAWAL,
            amount=debit_amount,
            currency=debit_currency,
            requested_amount=user_withdraw.amount,
            requested_currency=user_withdraw.currency,
            fx_rate_to_base=rate,
            status=TransactionStatus.COMPLETED,
            bank_reference=user_withdraw.bank_reference,
        )
        self.apply_to_balance(account, debit_amount, goal=goal, credit=False)
        self.db_session.add(new_transaction)
        await self.db_session.commit()
        await self.db_session.refresh(new_transaction)

        conversion_note = (
            f" (paid out as {user_withdraw.amount} {user_withdraw.currency.value} at rate {rate})"
            if user_withdraw.currency != debit_currency else ""
        )
        await SystemLogService(self.db_session).create_log(
            action="save_withdraw", entity="Transaction", user_id=user_id, entity_id=str(new_transaction.id),
            description=(
                f"Withdrawal of {debit_amount} {debit_currency.value} from account "
                f"{user_withdraw.account_id}{conversion_note}"
                + (f", goal {user_withdraw.goal_id}" if user_withdraw.goal_id else "")
            ),
            ip_address=ip_address, endpoint="",
        )

        channel, destination = self.resolve_notification_channel(user_data)
        await self.notify_user(
            channel, destination, "Withdrawal confirmed",
            f"Your withdrawal of {user_withdraw.amount} {user_withdraw.currency.value} was confirmed.",
        )

        profile_result = await self.db_session.execute(
            select(MoreAboutUsers).where(MoreAboutUsers.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()

        return WithdrawResponse(
            first_name=str(profile.first_name) if profile else "",
            last_name=str(profile.last_name) if profile else "",
            account_id=user_withdraw.account_id,
            amount_withdrawn=str(debit_amount),
            currency=debit_currency,
            requested_amount=user_withdraw.amount,
            requested_currency=user_withdraw.currency,
            bank_reference=user_withdraw.bank_reference,
            withdraw_at=new_transaction.created_at,
        )

    async def get_account_withdrawals(
            self,
            user_id:str,
            account_id:str,
            page:int = 1,
            limit:int = 20,
            ) -> tuple[list[TransactionModel], int]:
        """
        One page of withdrawals made from account_id, newest first. The
        account must belong to user_id - one that exists but belongs to
        someone else looks the same as one that doesn't exist at all.
        """
        account_result = await self.db_session.execute(
            select(UserAccount).where(
                UserAccount.id == account_id,
                UserAccount.user_id == user_id,
            )
        )
        if account_result.scalar_one_or_none() is None:
            raise AccountNotFoundError(detail="Account not found")

        statement = (
            select(TransactionModel)
            .where(TransactionModel.account_id == account_id, TransactionModel.type == TransactionType.WITHDRAWAL)
            .order_by(TransactionModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = (
            select(func.count())
            .select_from(TransactionModel)
            .where(TransactionModel.account_id == account_id, TransactionModel.type == TransactionType.WITHDRAWAL)
        )

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())
        return items, total

    async def get_all_withdrawals(
            self,
            user_id:str,
            page:int = 1,
            limit:int = 20,
            ) -> tuple[list[TransactionModel], int]:
        """
        One page of every withdrawal the caller has made, across all of
        their own savings accounts, newest first.
        """
        statement = (
            select(TransactionModel)
            .where(TransactionModel.user_id == user_id, TransactionModel.type == TransactionType.WITHDRAWAL)
            .order_by(TransactionModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = (
            select(func.count())
            .select_from(TransactionModel)
            .where(TransactionModel.user_id == user_id, TransactionModel.type == TransactionType.WITHDRAWAL)
        )

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())
        return items, total
