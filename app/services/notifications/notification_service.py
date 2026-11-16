from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import NotificationChannel, NotificationStatus
from app.model.models import Notifications
from app.services.sms.sms_result import SmsResult


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_notification(
        self,
        user_id: str,
        channel: NotificationChannel,
        notification_type: str,
        content: str,
        sms_result: Optional[SmsResult] = None,
    ) -> Notifications:
        """
        Saves a new notification to the database. Pass sms_result for an SMS
        that was already sent inline, to record it as SENT with the
        provider's message id (which delivery reports are matched on).
        """
        notification = Notifications(
            user_id=user_id,
            channel=channel,
            type=notification_type,
            content=content,
        )
        if sms_result is not None:
            notification.status = NotificationStatus.SENT
            notification.provider = sms_result.provider
            notification.provider_message_id = sms_result.message_id
            notification.cost = sms_result.cost
        self.session.add(notification)
        await self.session.commit()
        await self.session.refresh(notification)
        return notification

    async def mark_latest_as_read(
        self, user_id: str, notification_type: str
        ) -> Optional[Notifications]:
        """
        Marks the most recent unread notification of a given type as read -
        e.g. once the OTP it carried has been successfully verified, that's
        our strongest signal the user actually received and used it.
        """
        statement = (
            select(Notifications)
            .where(
                Notifications.user_id == user_id,
                Notifications.type == notification_type,
                Notifications.is_read.is_(False),
            )
            .order_by(Notifications.sent_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        notification = result.scalar_one_or_none()
        if notification:
            notification.is_read = True
            notification.read_at = datetime.now(timezone.utc)
            self.session.add(notification)
            await self.session.commit()
            await self.session.refresh(notification)
        return notification

    async def get_all_notifications(self, user_id: str) -> list[Notifications]:
        """
        Loads every notification previously sent to a user.
        """
        statement = (
            select(Notifications)
            .where(Notifications.user_id == user_id)
            .order_by(Notifications.sent_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    # ---- SMS delivery tracking ------------------------------------------

    async def mark_sent(self, notification_id: str, result: SmsResult) -> None:
        """Record that the provider accepted the message."""
        notification = await self.session.get(Notifications, notification_id)
        if notification is None:
            return
        notification.status = NotificationStatus.SENT
        notification.provider = result.provider
        notification.provider_message_id = result.message_id
        notification.cost = result.cost
        await self.session.commit()

    async def mark_failed(self, notification_id: str, reason: str) -> None:
        notification = await self.session.get(Notifications, notification_id)
        if notification is None:
            return
        notification.status = NotificationStatus.FAILED
        notification.failure_reason = reason
        await self.session.commit()

    async def apply_delivery_report(
        self, message_id: str, provider_status: str, failure_reason: Optional[str] = None
    ) -> Optional[Notifications]:
        """
        Apply an Africa's Talking delivery report to the notification that
        carried message_id. Final states (DELIVERED, FAILED) are never
        overwritten, so repeated or out-of-order callbacks are harmless.
        Returns None if no notification has that message id.
        """
        result = await self.session.execute(
            select(Notifications).where(Notifications.provider_message_id == message_id).limit(1)
        )
        notification = result.scalar_one_or_none()
        if notification is None:
            return None

        new_status = AT_DELIVERY_STATUS.get(provider_status)
        if new_status is None or notification.status in FINAL_STATUSES:
            return notification

        notification.status = new_status
        if new_status == NotificationStatus.DELIVERED:
            notification.delivered_at = datetime.now(timezone.utc)
        if new_status == NotificationStatus.FAILED:
            notification.failure_reason = failure_reason or provider_status
        await self.session.commit()
        return notification


# Africa's Talking delivery-report statuses -> our status
AT_DELIVERY_STATUS: dict[str, NotificationStatus] = {
    "Sent": NotificationStatus.SENT,
    "Submitted": NotificationStatus.SENT,
    "Buffered": NotificationStatus.SENT,
    "Success": NotificationStatus.DELIVERED,
    "Failed": NotificationStatus.FAILED,
    "Rejected": NotificationStatus.FAILED,
    "AbsentSubscriber": NotificationStatus.FAILED,
}
FINAL_STATUSES = {NotificationStatus.DELIVERED, NotificationStatus.FAILED}
