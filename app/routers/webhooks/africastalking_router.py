import logging
import secrets
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import get_settings
from app.database.database import get_session
from app.services.notifications.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks/africastalking", tags=["Webhooks"])


def verify_callback_token(token: str) -> None:
    """
    Africa's Talking doesn't sign its callbacks, so the delivery-report URL
    carries a secret (AT_CALLBACK_TOKEN). A wrong token - or no token
    configured - looks like a missing route.
    """
    expected = get_settings().AT_CALLBACK_TOKEN
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=404, detail="Not Found")


@router.post("/sms/delivery/{token}", include_in_schema=False)
async def sms_delivery_report(
    token: str,
    id: Annotated[str, Form()],
    status: Annotated[str, Form()],
    phoneNumber: Annotated[Optional[str], Form()] = None,
    networkCode: Annotated[Optional[str], Form()] = None,
    failureReason: Annotated[Optional[str], Form()] = None,
    retryCount: Annotated[Optional[str], Form()] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Delivery report for an SMS we sent. Always 200 once authorised - even
    for a message id we don't know - so Africa's Talking doesn't keep
    retrying something we can't use.
    """
    verify_callback_token(token)
    notification = await NotificationService(session).apply_delivery_report(id, status, failureReason)
    if notification is None:
        logger.warning("Delivery report for unknown SMS id %s (status %s)", id, status)
    return {"status": True}
