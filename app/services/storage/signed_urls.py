from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from app.config.setting import get_settings
from app.services.storage.upload_to_cloudflare import _get_s3_client

# Every object we upload is stored under this prefix (see upload()).
KEY_PREFIX = "users/"


@lru_cache
def signing_client() -> Any:
    """One boto3 client, reused: signing is local, but building a client is not free."""
    return _get_s3_client()


def is_valid_key(key: str) -> bool:
    """A key we uploaded: under users/, relative, and with no path tricks."""
    return key.startswith(KEY_PREFIX) and ".." not in key and "//" not in key


def sign_key(key: str) -> str:
    """
    A temporary link to one file in the (private) bucket, valid for
    SIGNED_URL_EXPIRE_SECONDS. Signing is a local calculation with the R2 secret
    key: no request is made. Anyone holding the link can open that one file until
    it expires, so never log it.

    Only keys produced by upload() are accepted, so a stored public URL or an
    arbitrary path can't be signed by mistake.
    """
    if not is_valid_key(key):
        raise ValueError("Not a valid object key")
    settings = get_settings()
    return str(signing_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": key},
        ExpiresIn=settings.SIGNED_URL_EXPIRE_SECONDS,
    ))


def links_expire_at() -> datetime:
    """When links signed right now stop working."""
    return datetime.now(timezone.utc) + timedelta(seconds=get_settings().SIGNED_URL_EXPIRE_SECONDS)
