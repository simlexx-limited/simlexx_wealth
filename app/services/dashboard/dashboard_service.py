#!/usr/bin/env python3

from datetime import date
from decimal import Decimal
from typing import Optional, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import Transaction as TransactionModel, UserAccount
from app.model.mod_enum import CurrencyType, TransactionStatus, TransactionType
from app.schema.dashboard import AccountBalance, BalanceSummary, DashboardOverview, DashboardTransactionItem
from app.services.goal_management.goal_management import UserGoalManagement


class DashboardService:
    def __init__(self, session:AsyncSession):
        self.db_session = session

    async def get_balance_summary(self, user_id:str) -> BalanceSummary:
        """The caller's own accounts, with a total balance per currency."""
        result = await self.db_session.execute(
            select(UserAccount).where(UserAccount.user_id == user_id).order_by(UserAccount.created_at.desc())
        )
        accounts = list(result.scalars().all())

        totals:dict[CurrencyType, Decimal] = {}
        for account in accounts:
            currency = cast(CurrencyType, account.currency)
            totals[currency] = totals.get(currency, Decimal("0")) + cast(Decimal, account.balance)

        return BalanceSummary(
            accounts=[AccountBalance.model_validate(account) for account in accounts],
            total_by_currency=totals,
        )

    async def get_transactions(
            self,
            user_id:str,
            page:int = 1,
            limit:int = 20,
            type:Optional[TransactionType] = None,
            status:Optional[TransactionStatus] = None,
            account_id:Optional[str] = None,
            goal_id:Optional[str] = None,
            date_from:Optional[date] = None,
            date_to:Optional[date] = None,
            ) -> tuple[list[TransactionModel], int]:
        """
        One page of the caller's own deposits and withdrawals together,
        newest first - optionally narrowed by type, status, account, goal,
        or a created_at date range.
        """
        filters = [TransactionModel.user_id == user_id]
        if type is not None:
            filters.append(TransactionModel.type == type)
        if status is not None:
            filters.append(TransactionModel.status == status)
        if account_id is not None:
            filters.append(TransactionModel.account_id == account_id)
        if goal_id is not None:
            filters.append(TransactionModel.goal_id == goal_id)
        if date_from is not None:
            filters.append(func.date(TransactionModel.created_at) >= date_from)
        if date_to is not None:
            filters.append(func.date(TransactionModel.created_at) <= date_to)

        statement = (
            select(TransactionModel)
            .where(*filters)
            .order_by(TransactionModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(TransactionModel).where(*filters)

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())
        return items, total

    async def get_overview(self, user_id:str) -> DashboardOverview:
        """
        Everything the app's main dashboard screen needs in one call: total
        balance and simulated growth across every currency in play, how many
        goals are still active, and the caller's most recent transactions.
        """
        balance = await self.get_balance_summary(user_id)

        goal_dashboards = await UserGoalManagement(self.db_session).list_goal_dashboards(user_id)
        growth_totals:dict[CurrencyType, Decimal] = {}
        for goal in goal_dashboards:
            growth_totals[goal.currency] = growth_totals.get(goal.currency, Decimal("0")) + goal.growth

        recent, _ = await self.get_transactions(user_id, page=1, limit=5)

        return DashboardOverview(
            total_balance_by_currency=balance.total_by_currency,
            total_growth_by_currency=growth_totals,
            active_goal_count=len(goal_dashboards),
            recent_transactions=[DashboardTransactionItem.model_validate(item) for item in recent],
        )
