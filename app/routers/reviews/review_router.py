from typing import Optional

from fastapi import Depends, Query, Request
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import UserAccountStatus
from app.schema.more_about_user_schema import MoreAboutUserResponse
from app.schema.review_schema import ReviewDecision, ReviewPage
from app.services.auth.authorization import REVIEWER_ROLES, require_roles
from app.services.rate_limiter.limiter import get_client_ip
from app.services.reviews.review_service import ReviewService

# Only agents review client profiles.
router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["Profile Reviews"],
    )


@router.get("", response_model=ReviewPage)
async def list_profiles(
    status:Optional[UserAccountStatus] = Query(None, description="pending, approved or rejected; leave out for all"),
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    token_payload:dict = Depends(require_roles(*REVIEWER_ROLES)),
    session:AsyncSession = Depends(get_session),
):
    """Load client profiles, everything or one status at a time, a page at a time."""
    items, total = await ReviewService(session).list_profiles(status, page, limit)
    return ReviewPage(
        items=[MoreAboutUserResponse.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )


@router.get("/{user_id}", response_model=MoreAboutUserResponse)
async def get_profile(
    user_id:str,
    token_payload:dict = Depends(require_roles(*REVIEWER_ROLES)),
    session:AsyncSession = Depends(get_session),
):
    """Load one client's full profile."""
    return await ReviewService(session).get_profile(user_id)


@router.put("/{user_id}", response_model=MoreAboutUserResponse)
async def decide_profile(
    request:Request,
    user_id:str,
    data:ReviewDecision,
    token_payload:dict = Depends(require_roles(*REVIEWER_ROLES)),
    session:AsyncSession = Depends(get_session),
):
    """
    Approve or reject a client's profile, with a comment explaining why. You can
    change an earlier decision. The client is notified by email.
    """
    return await ReviewService(session).decide(
        user_id, reviewer=token_payload, data=data,
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )
