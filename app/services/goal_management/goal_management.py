#!/usr/bin/env python3

from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import GoalRecommendations, Products, Users, UserGoals
from app.model.mod_enum import GoalStatus, NotificationChannel
from app.schema.user_goal import UserGoalCreate, SavingRecommendation, UserGoalUpdate
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,   # 404 - the goal (or account) doesn't exist
    ProductNotFoundError,   # 404 - the given product_id isn't a real product
    NothingToUpdateError,   # 400 - an update call with no fields set
)
from app.services.logs.system_log_service import SystemLogService
from app.services.notifications.notification_service import NotificationService
from app.ai_agents.orchestrator.orchestrator import orchestrator

# A goal is "active" while the user is still working toward it. COMPLETED and
# TERMINATED are its two end states.
ACTIVE_STATUSES = (GoalStatus.ONGOING, GoalStatus.REOPENED)


class UserGoalManagement:
    def __init__(self, session:AsyncSession):
        self.db_session = session

    async def get_goal_recommendation(self,
                                      user_goal:UserGoalCreate,
                                      user_id:str,
                                      ip_address:Optional[str]=None,
                                      endpoint:str = ""
                                      ):
        product = await self.get_chosen_product(user_goal.product_id)
        result = await orchestrator().recommendation(
            user_id=user_id,
            user_goal=user_goal,
            product=product
        )
        self.db_session.add(GoalRecommendations(
            user_id=user_id,
            request=user_goal.model_dump(mode="json"),
            saving_amount_recommended=result.saving_amount_recommended,
            currency=result.currency,
            frequency=result.frequency,
            reason=result.reason,
            alternatives=[alt.model_dump(mode="json") for alt in result.alternatives] if result.alternatives else None,
        ))
        await self.db_session.commit()
        return result
        


    async def get_all_goals(
        self,
        user_id:str,
        page:int = 1,
        limit:int = 20,
        ip_address:Optional[str] = None,
        endpoint:str = "",
    ) -> tuple[list[UserGoals], int]:
        """
        One page of the caller's own goals: active ones first, then completed
        or terminated ones, newest first within each group. Read-only, so it's
        only recorded in the system log - no notification is sent for it.
        """
        active_first = case((UserGoals.goal_status.in_(ACTIVE_STATUSES), 0), else_=1)
        statement = (
            select(UserGoals)
            .where(UserGoals.user_id == user_id)
            .order_by(active_first, UserGoals.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(UserGoals).where(UserGoals.user_id == user_id)

        items = list((await self.db_session.execute(statement)).scalars().all())
        total = int((await self.db_session.execute(count_statement)).scalar_one())

        await SystemLogService(self.db_session).create_log(
            action="list_goals", entity="UserGoals", user_id=user_id,
            description=f"Listed goals (page {page}, {len(items)} of {total})",
            ip_address=ip_address, endpoint=endpoint,
        )
        return items, total

    async def get_one_goal(self, user_id:str, goal_id:str) -> UserGoals:
        """
        Return the goal for the given id, scoped to its owner. A goal that
        exists but belongs to someone else looks the same as one that doesn't
        exist at all - both raise AccountNotFoundError - so a caller can't use
        this to probe which goal ids belong to other users.
        """
        statement = select(UserGoals).where(
            UserGoals.id == goal_id,
            UserGoals.user_id == user_id,
        )
        result = await self.db_session.execute(statement)
        goal = result.scalar_one_or_none()
        if goal is None:
            raise AccountNotFoundError(detail="No goal found for this user.")
        return goal

    async def get_chosen_product(self, product_id:Optional[str]) -> Optional[Products]:
        if product_id is None:
            return None
        result = await self.db_session.execute(select(Products).where(Products.id == product_id))
        product = result.scalar_one_or_none()
        if product is None:
            raise ProductNotFoundError()
        return product

    async def ensure_product_exists(self, product_id:Optional[str]) -> None:
        """No check needed when no product was chosen; otherwise it must be real."""
        if product_id is None:
            return
        result = await self.db_session.execute(select(Products.id).where(Products.id == product_id))
        if result.scalar_one_or_none() is None:
            raise ProductNotFoundError()

    async def create_goal(
        self,
        user_goal:UserGoalCreate,
        user_id:str,
        ip_address:Optional[str] = None,
        endpoint:str = "",
    ) -> UserGoals:
        """
        Save a new goal for user_id.

        Raises AccountNotFoundError if the account doesn't exist, and
        ProductNotFoundError if a product_id was given that isn't real.
        """
        result = await self.db_session.execute(select(Users.id).where(Users.id == user_id))
        if result.scalar_one_or_none() is None:
            raise AccountNotFoundError(detail="Account not found")

        await self.ensure_product_exists(user_goal.product_id)

        new_goal = UserGoals(
            user_id=user_id,
            product_id=user_goal.product_id,
            target_amount=user_goal.target_amount,
            current_amount=user_goal.current_amount,
            currency=user_goal.currency,
            target_date=user_goal.target_date,
            purpose=user_goal.purpose,
            goal_status=GoalStatus.ONGOING,
            income_regularity=user_goal.income_regularity,
            minimum_income=user_goal.minimum_income,
        )
        self.db_session.add(new_goal)
        await self.db_session.commit()
        await self.db_session.refresh(new_goal)

        await SystemLogService(self.db_session).create_log(
            action="create_goal", entity="UserGoals", user_id=user_id, entity_id=str(new_goal.id),
            description=f"Goal created: {new_goal.purpose} ({new_goal.target_amount} {new_goal.currency.value})",
            ip_address=ip_address, endpoint=endpoint,
        )
        await NotificationService(self.db_session).create_notification(
            user_id=user_id,
            channel=NotificationChannel.EMAIL,
            notification_type="goal_created",
            content=f"Your goal '{new_goal.purpose}' was created: "
                    f"{new_goal.target_amount} {new_goal.currency.value} by {new_goal.target_date}.",
        )
        return new_goal

    async def update_goal(
        self,
        user_id:str,
        goal_id:str,
        user_goal_update:UserGoalUpdate,
        ip_address:Optional[str] = None,
        endpoint:str = "",
    ) -> UserGoals:
        """
        Partially update the goal that belongs to user_id. Only fields the
        caller actually sent are written - everything else keeps its current
        value. income_regularity and goal_status aren't on UserGoalUpdate at
        all, so neither can be changed here (see UserGoalUpdate's docstring,
        and change_status for status).

        Raises AccountNotFoundError when there is no such goal for this user,
        NothingToUpdateError when nothing was sent, and ProductNotFoundError
        if product_id was changed to something that isn't a real product.
        """
        goal = await self.get_one_goal(user_id, goal_id)

        changes = {
            field: value
            for field, value in user_goal_update.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if not changes:
            raise NothingToUpdateError()
        if "product_id" in changes:
            await self.ensure_product_exists(changes["product_id"])

        for field, value in changes.items():
            setattr(goal, field, value)

        await self.db_session.commit()
        await self.db_session.refresh(goal)

        await SystemLogService(self.db_session).create_log(
            action="update_goal", entity="UserGoals", user_id=user_id, entity_id=str(goal.id),
            description=f"Goal updated: {', '.join(changes)}",
            ip_address=ip_address, endpoint=endpoint,
        )
        await NotificationService(self.db_session).create_notification(
            user_id=user_id,
            channel=NotificationChannel.EMAIL,
            notification_type="goal_updated",
            content=f"Your goal '{goal.purpose}' was updated.",
        )
        return goal

    async def change_goal_status(
        self,
        user_id:str,
        goal_id:str,
        goal_status:GoalStatus,
        ip_address:Optional[str] = None,
        endpoint:str = "",
    ) -> UserGoals:
        """
        Set the status of the goal that belongs to user_id.

        Raises AccountNotFoundError when there is no such goal for this user.
        """
        goal = await self.get_one_goal(user_id, goal_id)
        previous = goal.goal_status
        goal.goal_status = goal_status

        await self.db_session.commit()
        await self.db_session.refresh(goal)

        await SystemLogService(self.db_session).create_log(
            action="change_goal_status", entity="UserGoals", user_id=user_id, entity_id=str(goal.id),
            description=f"Goal status: {previous.value} -> {goal_status.value}",
            ip_address=ip_address, endpoint=endpoint,
        )
        await NotificationService(self.db_session).create_notification(
            user_id=user_id,
            channel=NotificationChannel.EMAIL,
            notification_type="goal_status_changed",
            content=f"Your goal '{goal.purpose}' is now {goal_status.value}.",
        )
        return goal
