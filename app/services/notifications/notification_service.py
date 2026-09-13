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
