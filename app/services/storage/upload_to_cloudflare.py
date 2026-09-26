#!/usr/bin/env python3

import asyncio
import os
import uuid
from typing import Any

import boto3
from botocore.client import Config
from fastapi import HTTPException, UploadFile

from app.config.setting import get_settings

ALLOWED_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"]
ALLOWED_DOCUMENT_EXTENSIONS = [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"]


def _get_s3_client() -> Any:
    _cfg = get_settings()
    return boto3.client(
        "s3",
        aws_access_key_id=_cfg.r2_access_key_id,
        aws_secret_access_key=_cfg.r2_secret_access_key,
        endpoint_url=_cfg.r2_endpoint_url,
        config=Config(signature_version="s3v4"),
    )


async def upload(
    file: UploadFile,
    folder_name: str,
    username: str,
    allowed_extensions: list[str] | None = None,
) -> str:
    """
    Upload any file to Cloudflare R2 and return its object key (its path inside
    the bucket). The bucket is private: to open the file, sign the key with
    app.services.storage.signed_urls.sign_key.

    allowed_extensions restricts the accepted file types (e.g. ALLOWED_IMAGE_EXTENSIONS
    for avatars, ALLOWED_DOCUMENT_EXTENSIONS for documents). Pass None to accept any type.
    """
    extension = os.path.splitext(file.filename or "")[1].lower()
    if allowed_extensions is not None and extension not in allowed_extensions:
        raise HTTPException(
            status_code=400, detail=f"File type {extension} not supported."
        )

    key = f"users/{username}/{folder_name}/{username}_{uuid.uuid4().hex}{extension}"
    contents = await file.read()

    try:
        await asyncio.to_thread(
            _get_s3_client().put_object,
            Bucket=get_settings().r2_bucket_name,
            Key=key,
            Body=contents,
            ContentType=file.content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"File upload failed: {exc}")

    return key
