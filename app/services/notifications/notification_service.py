from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import NotificationChannel
from app.model.models import Notifications


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_notification(
        self,
        user_id: str,
        channel: NotificationChannel,
        notification_type: str,
        content: str,
    ) -> Notifications:
        """
        Saves a new notification to the database.
        """
        notification = Notifications(
            user_id=user_id,
            channel=channel,
            type=notification_type,
            content=content,
        )
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
