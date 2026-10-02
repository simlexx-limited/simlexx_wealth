#!/usr/bin/env python3

from fastapi import Depends, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRouter

from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import CurrencyType, UserRole
from app.schema.user_account_schema import AccountDBSchema, AccountUpdate, UserAccountResponse
from app.services.auth.current_user import get_current_user
from app.services.auth.authorization import require_roles
from app.services.rate_limiter.limiter import get_client_ip
from app.services.user_account.account_creation import UserAccountService

from app.services.storage.upload_to_cloudflare import upload, ALLOWED_IMAGE_EXTENSIONS
from app.routers.more_about_user.check_image_ext import check_image_extension

router = APIRouter(
    prefix="/api/v1/account",
    tags=["User Account"],
    dependencies=[Depends(get_current_user)],   # all endpoints require a valid, active account
)

ACCOUNT_DOCUMENT_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS + [".pdf"]
ACCOUNT_DOCUMENT_MAX_SIZE_MB = 5

@router.post("/create", response_model=UserAccountResponse, status_code=201)
async def create_account(
    request:Request,
    bank_name:str = Form(...),
    account_number:str = Form(...),
    currency:CurrencyType = Form(...),
    account_document_id:str = Form(...),
    account_document:UploadFile = File(...),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
):
    """
    Link a new bank account to the currently authenticated client.

    The user_id is taken from the JWT, never accepted in the body.
    """
    user_id: str = token_payload["user_id"]

    await check_image_extension(account_document, max_size_mb=ACCOUNT_DOCUMENT_MAX_SIZE_MB, allow_pdf=True)
    key = await upload(
        account_document,
        folder_name="account",
        username=user_id,
        allowed_extensions=ACCOUNT_DOCUMENT_EXTENSIONS,
    )

    try:
        data = AccountDBSchema(
            user_id=user_id,
            bank_name=bank_name,
            account_number=account_number,
            currency=currency,
            account_document_id=account_document_id,
            account_document=key,
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        )

    return await UserAccountService(session).create_account(
        account=data, user_id=user_id, user_ip=await get_client_ip(request),
    )


@router.get("/all", response_model=list[UserAccountResponse])
async def get_all_accounts(
    request:Request,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
):
    """List every account linked by the caller, newest first."""
    return await UserAccountService(session).get_all_accounts(
        user_id=token_payload["user_id"], user_ip=await get_client_ip(request),
    )


@router.get("/{account_id}", response_model=UserAccountResponse)
async def get_account(
    request:Request,
    account_id:str,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
):
    """Return one of the caller's own accounts."""
    return await UserAccountService(session).get_account(
        user_id=token_payload["user_id"], user_ip=await get_client_ip(request), account_id=account_id,
    )


@router.put("/{account_id}", response_model=UserAccountResponse)
async def update_account(
    request:Request,
    account_id:str,
    account_document_id:Optional[str] = Form(None),
    account_document:Optional[UploadFile] = File(None),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
):
    """
    Update one of the caller's own accounts. Only the proof document can
    change here - send a new reference number, a new file, or both.
    """
    account_document = account_document if account_document is not None and account_document.filename else None

    key = None
    if account_document is not None:
        await check_image_extension(account_document, max_size_mb=ACCOUNT_DOCUMENT_MAX_SIZE_MB, allow_pdf=True)
        key = await upload(
            account_document,
            folder_name="account",
            username=token_payload["user_id"],
            allowed_extensions=ACCOUNT_DOCUMENT_EXTENSIONS,
        )

    try:
        data = AccountUpdate(account_document_id=account_document_id, account_document=key)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        )

    return await UserAccountService(session).update_account(
        user_id=token_payload["user_id"],
        user_ip=await get_client_ip(request),
        account_id=account_id,
        data=data,
    )
