from fastapi import Depends, File, UploadFile, Form
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRouter

from typing import Optional
from datetime import date

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.services.auth.phone.token.jwt_dependency import verify_jwt_token
from app.services.auth.authorization import (
    REVIEWER_ROLES,
    ensure_owner_or_reviewer,
    require_roles,
)
from app.services.more_about_user.more_about_user_service import MoreAboutUserService
from app.model.mod_enum import DocumentType, UserRole
from app.schema.more_about_user_schema import (
    MoreAboutUserCreate,
    MoreAboutUserUpdate,
    MoreAboutUserReview,
    MoreAboutUserResponse,
    UploadedURL,
)

from app.services.storage.upload_to_cloudflare import upload, ALLOWED_IMAGE_EXTENSIONS
from app.routers.more_about_user.check_image_ext import check_image_extension


router = APIRouter(
    prefix="/api/v1/more_about_user",
    tags=["More About User"],
    dependencies=[Depends(verify_jwt_token)],   # all endpoints require a valid JWT
)

# An identity document may be a photo or a scanned PDF.
ID_DOCUMENT_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS + [".pdf"]

# Who may do what (role checks; ownership is checked per endpoint):
#   client   - create, read, update and delete their OWN profile
#   reviewer - (agent, broker) list and read any profile, and review it
#   others   - no access to profiles


@router.post("/create", response_model=MoreAboutUserResponse, status_code=201)
async def create_profile(
    first_name:str = Form(...),
    last_name:str = Form(...),
    middle_name:Optional[str] = Form(None),
    date_of_birth:date = Form(...),
    nationality:str = Form(...),
    country_of_residence:str = Form(...),
    city_of_residence:str = Form(...),
    full_address:str = Form(...),
    profession:str = Form(...),
    document_type:DocumentType = Form(...),
    document_id:str = Form(...),
    document_expiry_date:date = Form(...),
    id_document:UploadFile = File(...),
    profile_picture:UploadFile = File(...),
    token_payload: dict = Depends(require_roles(UserRole.CLIENT)),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a MoreAboutUsers profile for the currently authenticated client.

    The user_id is extracted from the JWT — it is **not** accepted in the body.
    """
    user_id: str = token_payload["user_id"]

    try:
        data = MoreAboutUserCreate(
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            date_of_birth=date_of_birth,
            nationality=nationality,
            country_of_residence=country_of_residence,
            city_of_residence=city_of_residence.upper(),
            full_address=full_address.lower(),
            profession=profession.upper(),
            document_type=document_type,
            document_id=document_id,
            document_expiry_date=document_expiry_date
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        )

    service = MoreAboutUserService(session)

    await service.check_can_create(user_id=user_id, data=data)
    await check_image_extension(id_document, allow_pdf=True)
    await check_image_extension(profile_picture)

    document_link = UploadedURL.model_validate({"link": await upload(
        id_document,
        folder_name="about",
        username=user_id,
        allowed_extensions=ID_DOCUMENT_EXTENSIONS,
    )})
    picture_link = UploadedURL.model_validate({"link": await upload(
        profile_picture,
        folder_name="profile",
        username=user_id,
        allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
    )})

    return await service.create(
        user_id=user_id,
        data=data,
        document_link=document_link,
        profile_picture=picture_link,
    )


@router.get("/all", response_model=list[MoreAboutUserResponse])
async def get_all_profiles(
    _: dict = Depends(require_roles(*REVIEWER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    """
    Return every MoreAboutUsers profile (newest first). Reviewers only.
    """
    return await MoreAboutUserService(session).get_all()


@router.get("/{user_id}", response_model=MoreAboutUserResponse)
async def get_profile_by_user_id(
    user_id: str,
    token_payload: dict = Depends(require_roles(UserRole.CLIENT, *REVIEWER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    """
    Return the MoreAboutUsers profile for the given user_id.
    A client can only read their own; reviewers can read any.
    """
    ensure_owner_or_reviewer(token_payload, user_id)
    return await MoreAboutUserService(session).get_by_user_id(user_id=user_id)


@router.put("/{user_id}", response_model=MoreAboutUserResponse)
async def update_profile(
    user_id: str,
    data: MoreAboutUserUpdate,
    token_payload: dict = Depends(require_roles(UserRole.CLIENT)),
    session: AsyncSession = Depends(get_session),
):
    """
    Partially update the caller's own profile (clients only).

    Only fields included in the request body are modified; omitted fields
    retain their current values. Review status can't be set here.
    """
    ensure_owner_or_reviewer(token_payload, user_id)
    return await MoreAboutUserService(session).update(user_id=user_id, data=data)


@router.put("/{user_id}/review", response_model=MoreAboutUserResponse)
async def review_profile(
    user_id: str,
    data: MoreAboutUserReview,
    token_payload: dict = Depends(require_roles(*REVIEWER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    """
    Approve or reject a client's profile. Reviewers only; the reviewer id and
    time are recorded from the token and the server clock.
    """
    return await MoreAboutUserService(session).review(
        user_id=user_id,
        reviewer_id=token_payload["user_id"],
        data=data,
    )


@router.delete("/{user_id}")
async def delete_profile(
    user_id: str,
    token_payload: dict = Depends(require_roles(UserRole.CLIENT)),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete the caller's own profile (clients only).
    """
    ensure_owner_or_reviewer(token_payload, user_id)
    return await MoreAboutUserService(session).delete(user_id=user_id)
