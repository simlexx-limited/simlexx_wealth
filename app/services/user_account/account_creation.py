#!/usr/bin/env python3
import logging
from typing import Optional

import sqlalchemy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schema.user_account_schema import AccountDBSchema, AccountUpdate, UserAccountResponse
from app.model.models import Users, UserAccount
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    DuplicateAccountError,
    NothingToUpdateError,
)
from app.services.logs.system_log_service import SystemLogService
from app.services.storage.upload_to_cloudflare import delete_key

ENDPOINT = "/api/v1/account"
logger = logging.getLogger(__name__)


class UserAccountService:
    def __init__(self, session:AsyncSession):
        self.db_session = session

    async def check_user_exist(self, user_id:str) -> str:
        """
        Confirm user_id is a real account, and return it.

        Raises AccountNotFoundError if not - reuse this wherever an account
        operation needs to confirm the owner exists first.
        """
        result = await self.db_session.execute(select(Users.id).where(Users.id == user_id))
        found = result.scalar_one_or_none()   # a Result can only be read once - don't call a second fetch method on it
        if found is None:
            raise AccountNotFoundError(detail="No user found with the provided ID")
        return str(found)

    async def create_account(self, account:AccountDBSchema, user_id:str, user_ip:Optional[str] = None) -> UserAccountResponse:
        """
        Link a new bank account to user_id.

        Raises AccountNotFoundError if the owner doesn't exist, and
        DuplicateAccountError if this bank name and account number are already linked.
        """
        await self.check_user_exist(user_id)

        new_account = UserAccount(**account.model_dump(exclude={"user_id"}), user_id=user_id)

        try:
            self.db_session.add(new_account)
            await self.db_session.commit()
            await self.db_session.refresh(new_account)
        except sqlalchemy.exc.IntegrityError:
            await self.db_session.rollback()
            await SystemLogService(self.db_session).create_log(
                action="create_account_failed", entity="UserAccount", user_id=user_id,
                description="Duplicate account: this bank name and account number are already linked",
                ip_address=user_ip, endpoint=f"{ENDPOINT}/create",
            )
            raise DuplicateAccountError("Account with this bank name and account number already exists")

        await SystemLogService(self.db_session).create_log(
            action="create_account", entity="UserAccount", user_id=user_id, entity_id=str(new_account.id),
            description="Savings account created",
            ip_address=user_ip, endpoint=f"{ENDPOINT}/create",
        )
        return UserAccountResponse.model_validate(new_account)

    async def get_all_accounts(self, user_id:str, user_ip:Optional[str] = None) -> list[UserAccountResponse]:
        """
        Return every account belonging to user_id, newest first.
        """
        await self.check_user_exist(user_id=user_id)
        search = await self.db_session.execute(
            select(UserAccount).where(UserAccount.user_id == user_id).order_by(UserAccount.created_at.desc())
        )
        accounts = search.scalars().all()
        await SystemLogService(self.db_session).create_log(
            action="view_accounts", entity="UserAccount", user_id=user_id,
            description=f"Listed accounts ({len(accounts)})",
            ip_address=user_ip, endpoint=f"{ENDPOINT}/view/all",
        )
        return [UserAccountResponse.model_validate(account) for account in accounts]

    async def get_account(self, user_id:str, account_id:str, user_ip:Optional[str] = None) -> UserAccountResponse:
        """
        Return one of user_id's own accounts. An account that exists but
        belongs to someone else looks the same as one that doesn't exist at
        all (404 either way), so a caller can't use this to probe other
        users' account ids.
        """
        await self.check_user_exist(user_id=user_id)
        search = await self.db_session.execute(
            select(UserAccount).where(UserAccount.id == account_id, UserAccount.user_id == user_id)
        )
        account = search.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(detail="Account not found")
        await SystemLogService(self.db_session).create_log(
            action="view_account", entity="UserAccount", user_id=user_id, entity_id=str(account.id),
            description=f"Viewed account {account.id}",
            ip_address=user_ip, endpoint=f"{ENDPOINT}/view/{account_id}",
        )
        return UserAccountResponse.model_validate(account)

    async def update_account(
        self, user_id:str, account_id:str, data:AccountUpdate, user_ip:Optional[str] = None
    ) -> UserAccountResponse:
        """
        Update one of user_id's own accounts. Only the uploaded proof document
        can change here (see AccountUpdate) - only fields actually sent are
        written.

        Raises AccountNotFoundError if there's no such account for this user,
        and NothingToUpdateError if nothing was sent.
        """
        search = await self.db_session.execute(
            select(UserAccount).where(UserAccount.id == account_id, UserAccount.user_id == user_id)
        )
        account = search.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(detail="Account not found")

        changes = {
            field: value
            for field, value in data.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if not changes:
            raise NothingToUpdateError()

        # unlike KYC, an account's proof document is replaced, not kept alongside
        # old versions - capture the key being replaced so it can be deleted,
        # but only after the swap is safely committed
        previous_document_key = str(account.account_document) if "account_document" in changes else None

        for field, value in changes.items():
            setattr(account, field, value)

        await self.db_session.commit()
        await self.db_session.refresh(account)

        if previous_document_key is not None:
            try:
                await delete_key(previous_document_key)
            except Exception:
                logger.exception(
                    "Account %s was updated, but its old document %s could not be deleted from storage",
                    account.id, previous_document_key,
                )

        await SystemLogService(self.db_session).create_log(
            action="update_account", entity="UserAccount", user_id=user_id, entity_id=str(account.id),
            description=f"Account updated: {', '.join(changes)}",
            ip_address=user_ip, endpoint=f"{ENDPOINT}/update/{account_id}",
        )
        return UserAccountResponse.model_validate(account)
