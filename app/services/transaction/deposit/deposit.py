#!/usr/bin/env python3

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

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
from app.schema.deposit import (
    UserDeposit,
    SendUserOTP,
    DepositResponse
    )
from app.schema.user_account_schema import UserAccountResponse
from app.services.exchange_rate.exchange_rate_service import get_exchange_rate
from app.services.logs.system_log_service import SystemLogService
from app.services.otp_management.otp_generator import generate_otp_code
from app.services.otp_management.otp_store import save_otp_code, get_otp_code, delete_otp_code, format_ttl_duration
from app.services.transaction.transaction import Transaction
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError, InvalidOtpError
from app.services.otp_management.otp_management_service import OtpManagementService


class Deposit(Transaction):
    def __init__(self, session:AsyncSession):
        super().__init__(session=session)
        self.otp_generator = OtpManagementService(session=session)

    async def get_user_accounts(self, user_id):
        """
        Get all user bank accounts
        """
        stment = select(UserAccount).where(UserAccount.user_id == user_id)
        search_account = await self.db_session.execute(stment)
        result = search_account.all()
        if not result:
            raise AccountNotFoundError(detail="No saving account for the user")
        return [UserAccountResponse.model_validate(account) for account in result]

    async def send_otp_deposit(self,
                               user_id:str,
                               ip_address:Optional[str] = None,
                               ) -> SendUserOTP:
        """
        Send the user a one-time code to confirm an upcoming deposit - by
        email if their email is verified, otherwise by phone if their phone
        number is verified. Nothing about the deposit itself is saved here -
        a separate function saves it once this code has been verified.
        """
        user_data = await self.get_verified_user(user_id)
        channel, destination = self.resolve_notification_channel(user_data)

        otp_code = str(generate_otp_code())
        await save_otp_code(identifier=user_id, purpose=OtpPurpose.DEPOSIT, code=otp_code)
        await self.otp_generator.create_otp(
            user_id=user_id,
            channel=channel,
            code=otp_code,
            purpose=OtpPurpose.DEPOSIT,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=get_settings().OTP_TTL_SECONDS),
        )

        if channel == NotificationChannel.EMAIL:
            message = (
                f"Your one-time code to confirm this deposit is {otp_code}. It expires in "
                f"{format_ttl_duration(get_settings().OTP_TTL_SECONDS)}."
            )
        else:
            message = otp_code
        await self.notify_user(channel, destination, "Confirm your deposit", message)

        await SystemLogService(self.db_session).create_log(
            action="send_otp_deposit", entity="OtpVerification", user_id=user_id,
            description=f"Deposit OTP sent via {channel.value}",
            ip_address=ip_address, endpoint="",
        )

        return SendUserOTP(
            email_address=destination if channel == NotificationChannel.EMAIL else None,
            phone_number=destination if channel == NotificationChannel.SMS else None,
            otp_code=otp_code,
        )

    async def save_deposit(
            self,
            user_deposit:UserDeposit,
            user_id:str,
            otp_code:str,
            ip_address:Optional[str] = None,
            ) -> DepositResponse:
        """
        Verify the OTP sent by send_otp_deposit, and if it's valid, save the
        deposit. A deposit may or may not be linked to a goal - goal_id is
        only checked when the client actually sent one.

        Raises AccountNotFoundError if the account (or goal, if given)
        doesn't belong to user_id, and InvalidOtpError if the code is wrong,
        expired, or was never issued.
        """
        user_data = await self.get_verified_user(user_id)

        account_result = await self.db_session.execute(
            select(UserAccount).where(
                UserAccount.id == user_deposit.account_id,
                UserAccount.user_id == user_id,
            )
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(detail="Account not found")

        goal = None
        if user_deposit.goal_id is not None:
            goal_result = await self.db_session.execute(
                select(UserGoals).where(
                    UserGoals.id == user_deposit.goal_id,
                    UserGoals.user_id == user_id,
                )
            )
            goal = goal_result.scalar_one_or_none()
            if goal is None:
                raise AccountNotFoundError(detail="Goal not found")

        stored_code = await get_otp_code(identifier=user_id, purpose=OtpPurpose.DEPOSIT)
        is_valid = stored_code is not None and stored_code == otp_code

        otp_record = await self.otp_generator.get_latest_unverified_otp(
            user_id=user_id, purpose=OtpPurpose.DEPOSIT
        )
        if otp_record is not None:
            await self.otp_generator.record_attempt(otp_record, verified=is_valid)

        if not is_valid:
            raise InvalidOtpError()

        await delete_otp_code(identifier=user_id, purpose=OtpPurpose.DEPOSIT)

        # The deposit always settles in the account's own currency - convert
        # it before saving if the client deposited in a different one.
        rate = await get_exchange_rate(user_deposit.currency, account.currency)
        settled_amount = (user_deposit.amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        settled_currency = account.currency

        new_transaction = TransactionModel(
            user_id=user_id,
            account_id=user_deposit.account_id,
            goal_id=user_deposit.goal_id,
            type=TransactionType.DEPOSIT,
            amount=settled_amount,
            currency=settled_currency,
            requested_amount=user_deposit.amount,
            requested_currency=user_deposit.currency,
            fx_rate_to_base=rate,
            status=TransactionStatus.COMPLETED,
            bank_reference=user_deposit.bank_reference,
            unit_master_reference=user_deposit.unit_master_reference,
        )
        self.apply_to_balance(account, settled_amount, goal=goal, credit=True)
        self.db_session.add(new_transaction)
        await self.db_session.commit()
        await self.db_session.refresh(new_transaction)

        conversion_note = (
            f" (converted from {user_deposit.amount} {user_deposit.currency.value} at rate {rate})"
            if user_deposit.currency != settled_currency else ""
        )
        await SystemLogService(self.db_session).create_log(
            action="save_deposit", entity="Transaction", user_id=user_id, entity_id=str(new_transaction.id),
            description=(
                f"Deposit of {settled_amount} {settled_currency.value} to account "
                f"{user_deposit.account_id}{conversion_note}"
                + (f", goal {user_deposit.goal_id}" if user_deposit.goal_id else "")
            ),
            ip_address=ip_address, endpoint="",
        )

        channel, destination = self.resolve_notification_channel(user_data)
        await self.notify_user(
            channel, destination, "Deposit confirmed",
            f"Your deposit of {settled_amount} {settled_currency.value} was confirmed.",
        )

        profile_result = await self.db_session.execute(
            select(MoreAboutUsers).where(MoreAboutUsers.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()

        return DepositResponse(
            first_name=str(profile.first_name) if profile else "",
            last_name=str(profile.last_name) if profile else "",
            account_id=user_deposit.account_id,
            amount_deposited=str(settled_amount),
            currency=settled_currency,
            requested_amount=user_deposit.amount,
            requested_currency=user_deposit.currency,
            unit_trust_reference=user_deposit.unit_master_reference,
            bank_reference=user_deposit.bank_reference,
            deposit_at=new_transaction.created_at,
        )

    async def get_account_deposits(
            self,
            user_id:str,
            account_id:str,
            page:int = 1,
            limit:int = 20,
            ) -> tuple[list[TransactionModel], int]:
        """
        One page of deposits made to account_id, newest first. The account
        must belong to user_id - one that exists but belongs to someone else
        looks the same as one that doesn't exist at all.
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
            .where(TransactionModel.account_id == account_id, TransactionModel.type == TransactionType.DEPOSIT)
            .order_by(TransactionModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = (
            select(func.count())
            .select_from(TransactionModel)
            .where(TransactionModel.account_id == account_id, TransactionModel.type == TransactionType.DEPOSIT)
        )

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())
        return items, total

    async def get_all_deposits(
            self,
            user_id:str,
            page:int = 1,
            limit:int = 20,
            ) -> tuple[list[TransactionModel], int]:
        """
        One page of every deposit the caller has made, across all of their
        own savings accounts, newest first.
        """
        statement = (
            select(TransactionModel)
            .where(TransactionModel.user_id == user_id, TransactionModel.type == TransactionType.DEPOSIT)
            .order_by(TransactionModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = (
            select(func.count())
            .select_from(TransactionModel)
            .where(TransactionModel.user_id == user_id, TransactionModel.type == TransactionType.DEPOSIT)
        )

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())
        return items, total
