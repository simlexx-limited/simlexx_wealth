from fastapi import Depends
from fastapi.routing import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.services.auth.phone.token.jwt_dependency import verify_jwt_token
from app.services.more_about_user.more_about_user_service import MoreAboutUserService
from app.schema.more_about_user_schema import (
    MoreAboutUserCreate,
    MoreAboutUserUpdate,
    MoreAboutUserResponse,
)


router = APIRouter(
    prefix="/api/v1/more_about_user",
    tags=["More About User"],
    dependencies=[Depends(verify_jwt_token)],   # all endpoints require a valid JWT
)


@router.post("/create", response_model=MoreAboutUserResponse, status_code=201)
async def create_profile(
    data: MoreAboutUserCreate,
    token_payload: dict = Depends(verify_jwt_token),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a MoreAboutUsers profile for the currently authenticated user.

    The user_id is extracted from the JWT — it is **not** accepted in the body.
    """
    user_id: str = token_payload["user_id"]
    return await MoreAboutUserService(session).create(user_id=user_id, data=data)


@router.get("/all", response_model=list[MoreAboutUserResponse])
async def get_all_profiles(
    _: dict = Depends(verify_jwt_token),
    session: AsyncSession = Depends(get_session),
):
    """
    Return every MoreAboutUsers profile (newest first).
    """
    return await MoreAboutUserService(session).get_all()


@router.get("/{user_id}", response_model=MoreAboutUserResponse)
async def get_profile_by_user_id(
    user_id: str,
    _: dict = Depends(verify_jwt_token),
    session: AsyncSession = Depends(get_session),
):
    """
    Return the MoreAboutUsers profile for the given user_id.
    """
    return await MoreAboutUserService(session).get_by_user_id(user_id=user_id)


@router.put("/{user_id}", response_model=MoreAboutUserResponse)
async def update_profile(
    user_id: str,
    data: MoreAboutUserUpdate,
    _: dict = Depends(verify_jwt_token),
    session: AsyncSession = Depends(get_session),
):
    """
    Partially update the MoreAboutUsers profile for the given user_id.

    Only fields included in the request body are modified; omitted fields
    retain their current values.
    """
    return await MoreAboutUserService(session).update(user_id=user_id, data=data)


@router.delete("/{user_id}")
async def delete_profile(
    user_id: str,
    _: dict = Depends(verify_jwt_token),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete the MoreAboutUsers profile for the given user_id.
    """
    return await MoreAboutUserService(session).delete(user_id=user_id)
