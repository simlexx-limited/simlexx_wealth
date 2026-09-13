from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import SystemLogs


class SystemLogService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_log(
        self,
        action: str,
        entity: str,
        user_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        description: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> SystemLogs:
        """
        Saves a new system log entry to the database.
        """
        log = SystemLogs(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            description=description,
            ip_address=ip_address,
        )
        self.session.add(log)
        await self.session.commit()
        await self.session.refresh(log)
        return log

    async def get_all_logs(self, user_id: str) -> list[SystemLogs]:
        """
        Loads every system log entry recorded for a user.
        """
        statement = (
            select(SystemLogs)
            .where(SystemLogs.user_id == user_id)
            .order_by(SystemLogs.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
