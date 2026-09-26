from fastapi import Depends, File, UploadFile, Form
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRouter

from typing import Optional
from datetime import date

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.services.auth.current_user import get_current_user
from app.services.auth.authorization import ensure_owner, require_roles
from app.services.more_about_user.more_about_user_service import MoreAboutUserService
from app.model.mod_enum import DocumentType, UserRole
from app.schema.more_about_user_schema import (
    MoreAboutUserCreate,
    MoreAboutUserUpdate,
    MoreAboutUserResponse,
    UploadedURL,
)

from app.services.storage.upload_to_cloudflare import upload, ALLOWED_IMAGE_EXTENSIONS
from app.routers.more_about_user.check_image_ext import check_image_extension


router = APIRouter(
    prefix="/api/v1/more_about_user",
    tags=["More About User"],
    dependencies=[Depends(get_current_user)],   # all endpoints require a valid, active account
)

# An identity document may be a photo or a scanned PDF.
ID_DOCUMENT_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS + [".pdf"]

# Only clients use this router, and only for their OWN profile: create, read
# and update it. There is no delete. Reviewing profiles is done by agents
# through /api/v1/reviews.


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


@router.get("/{user_id}", response_model=MoreAboutUserResponse)
async def get_profile_by_user_id(
    user_id: str,
    token_payload: dict = Depends(require_roles(UserRole.CLIENT)),
    session: AsyncSession = Depends(get_session),
):
    """
    Return your own profile, including its review status and the reviewer's comment.
    """
    ensure_owner(token_payload, user_id)
    return await MoreAboutUserService(session).get_by_user_id(user_id=user_id)


@router.put("/{user_id}", response_model=MoreAboutUserResponse)
async def update_profile(
    user_id: str,
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    middle_name: Optional[str] = Form(None),
    date_of_birth: Optional[str] = Form(None, description="YYYY-MM-DD"),
    nationality: Optional[str] = Form(None),
    country_of_residence: Optional[str] = Form(None),
    city_of_residence: Optional[str] = Form(None),
    full_address: Optional[str] = Form(None),
    profession: Optional[str] = Form(None),
    document_type: Optional[str] = Form(None, description="national_id, passport or driving_permit"),
    document_id: Optional[str] = Form(None),
    document_expiry_date: Optional[str] = Form(None, description="YYYY-MM-DD"),
    id_document: Optional[UploadFile] = File(None),
    profile_picture: Optional[UploadFile] = File(None),
    token_payload: dict = Depends(require_roles(UserRole.CLIENT)),
    session: AsyncSession = Depends(get_session),
):
    """
    Update your own profile. Send only what changed: any of the fields, and/or a
    new identity document and profile picture.

    If the profile had already been approved or rejected, it goes back to pending
    so a reviewer looks at the new details. That is also how you ask for another
    review after a rejection. Review status can't be set here.
    """
    ensure_owner(token_payload, user_id)

    # Swagger and some clients send blank values for fields left empty; those
    # mean "not provided", as does an empty file part.
    sent = {
        "first_name": first_name, "last_name": last_name, "middle_name": middle_name,
        "date_of_birth": date_of_birth, "nationality": nationality,
        "country_of_residence": country_of_residence, "city_of_residence": city_of_residence,
        "full_address": full_address, "profession": profession, "document_type": document_type,
        "document_id": document_id, "document_expiry_date": document_expiry_date,
    }
    provided = {name: value for name, value in sent.items() if value not in (None, "")}
    # same normalization as when the profile is created
    if "city_of_residence" in provided:
        provided["city_of_residence"] = provided["city_of_residence"].upper()
    if "full_address" in provided:
        provided["full_address"] = provided["full_address"].lower()
    if "profession" in provided:
        provided["profession"] = provided["profession"].upper()

    id_document = id_document if id_document is not None and id_document.filename else None
    profile_picture = profile_picture if profile_picture is not None and profile_picture.filename else None

    try:
        data = MoreAboutUserUpdate.model_validate(provided)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        )

    service = MoreAboutUserService(session)

    # Reject everything that can be rejected *before* uploading anything.
    await service.check_can_update(
        user_id=user_id, data=data, has_files=id_document is not None or profile_picture is not None
    )
    if id_document is not None:
        await check_image_extension(id_document, allow_pdf=True)
    if profile_picture is not None:
        await check_image_extension(profile_picture)

    document_link = None
    if id_document is not None:
        document_link = UploadedURL.model_validate({"link": await upload(
            id_document,
            folder_name="about",
            username=user_id,
            allowed_extensions=ID_DOCUMENT_EXTENSIONS,
        )})
    picture_link = None
    if profile_picture is not None:
        picture_link = UploadedURL.model_validate({"link": await upload(
            profile_picture,
            folder_name="profile",
            username=user_id,
            allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
        )})

    return await service.update(
        user_id=user_id, data=data, document_link=document_link, profile_picture=picture_link
    )
