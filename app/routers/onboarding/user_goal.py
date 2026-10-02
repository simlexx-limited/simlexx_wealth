#!/usr/bin/env python3

from fastapi import Depends, Query, Request
from fastapi.routing import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session

from app.model.mod_enum import UserRole, GoalStatus

from app.schema.user_goal import (
    UserGoalCreate,
    UserGoalPage,
    UserGoalResponse,
    UserGoalUpdate,
    SavingRecommendation,
)

from app.services.auth.current_user import get_current_user
from app.services.auth.authorization import require_roles
from app.services.goal_management.goal_management import UserGoalManagement
from app.services.rate_limiter.limiter import get_client_ip

router = APIRouter(
    prefix="/api/v1/user_goal",
    dependencies=[Depends(get_current_user)],
    tags=["Goal creation Router"]
)

@router.get("/goal/all", response_model=UserGoalPage)
async def get_all_goals(
    request:Request,
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=20),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    """
    List the caller's own goals, active ones first, newest first within each
    group, 20 per page.
    """
    items, total = await UserGoalManagement(session).get_all_goals(
        user_id=token_payload["user_id"], page=page, limit=limit,
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )
    return UserGoalPage(
        items=[UserGoalResponse.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )

@router.get("/goal/all/{goal_id}", response_model=UserGoalResponse)
async def get_single_goal(
    goal_id:str,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    """
    Return one of the caller's own goals. A goal that belongs to someone else
    looks the same as one that doesn't exist (404 either way).
    """
    return await UserGoalManagement(session).get_one_goal(
        user_id=token_payload["user_id"], goal_id=goal_id
    )

@router.post("/goal/recommendation", response_model=SavingRecommendation)
async def optain_rec_goal(
    user_goal:UserGoalCreate,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    pass

@router.post("/goal/create", response_model=UserGoalResponse, status_code=201)
async def create_goal(
    request:Request,
    user_goal:UserGoalCreate,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)):
    return await UserGoalManagement(session).create_goal(
        user_goal=user_goal,
        user_id=token_payload["user_id"],
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
    )

@router.put("/goal/update/{goal_id}", response_model=UserGoalResponse)
async def update_goal(
    request:Request,
    goal_id:str,
    user_goal:UserGoalUpdate,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    """
    Partially update one of the caller's own goals. Only fields included in
    the request are changed. Review the docstring on UserGoalUpdate: it
    deliberately can't touch income_regularity or goal_status.
    """
    return await UserGoalManagement(session).update_goal(
        user_id=token_payload["user_id"],
        goal_id=goal_id,
        user_goal_update=user_goal,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
    )

@router.patch("/goal/change_status/{goal_id}", response_model=UserGoalResponse)
async def change_status(
    request:Request,
    goal_id:str,
    status:GoalStatus,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session)
    ):
    """Set the status of one of the caller's own goals."""
    return await UserGoalManagement(session).change_goal_status(
        user_id=token_payload["user_id"], goal_id=goal_id, goal_status=status,
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )
