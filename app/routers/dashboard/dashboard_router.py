#!/usr/bin/env python3

from datetime import date
from typing import Optional

from fastapi import Depends, Query
from fastapi.routing import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import TransactionStatus, TransactionType, UserRole
from app.schema.dashboard import BalanceSummary, DashboardOverview, DashboardTransactionItem, DashboardTransactionPage
from app.schema.user_goal import GoalDashboardResponse
from app.services.auth.authorization import require_roles
from app.services.auth.current_user import get_current_user
from app.services.dashboard.dashboard_service import DashboardService
from app.services.goal_management.goal_management import UserGoalManagement

router = APIRouter(
    prefix="/api/v1/dashboard",
    dependencies=[Depends(get_current_user)],
    tags=["Dashboard"]
    )


@router.get("/overview", response_model=DashboardOverview)
async def get_overview(
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """Everything the app's main dashboard screen needs in one call."""
    return await DashboardService(session).get_overview(user_id=token_payload["user_id"])


@router.get("/balance", response_model=BalanceSummary)
async def get_balance(
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """The caller's own accounts, with a total balance per currency."""
    return await DashboardService(session).get_balance_summary(user_id=token_payload["user_id"])


@router.get("/transactions", response_model=DashboardTransactionPage)
async def get_transactions(
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    type:Optional[TransactionType] = Query(default=None),
    status:Optional[TransactionStatus] = Query(default=None),
    account_id:Optional[str] = Query(default=None),
    goal_id:Optional[str] = Query(default=None),
    date_from:Optional[date] = Query(default=None),
    date_to:Optional[date] = Query(default=None),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """
    One page of the caller's own deposits and withdrawals together, newest
    first - optionally narrowed by type, status, account, goal, or a
    created_at date range.
    """
    items, total = await DashboardService(session).get_transactions(
        user_id=token_payload["user_id"], page=page, limit=limit,
        type=type, status=status, account_id=account_id, goal_id=goal_id,
        date_from=date_from, date_to=date_to,
    )
    return DashboardTransactionPage(
        items=[DashboardTransactionItem.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )


@router.get("/goals", response_model=list[GoalDashboardResponse])
async def get_goals_progress(
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """Progress on every one of the caller's active goals."""
    return await UserGoalManagement(session).list_goal_dashboards(user_id=token_payload["user_id"])


@router.get("/goal/{goal_id}", response_model=GoalDashboardResponse)
async def get_goal_dashboard(
    goal_id:str,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    """The caller's progress on one of their own goals."""
    return await UserGoalManagement(session).get_goal_dashboard(
        user_id=token_payload["user_id"], goal_id=goal_id,
    )
