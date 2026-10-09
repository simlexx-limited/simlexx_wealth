from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import Products


class ProductService:
    def __init__(self, session: AsyncSession):
        self.db_session = session

    async def list_active(self) -> list[Products]:
        result = await self.db_session.execute(
            select(Products).where(Products.is_active.is_(True)).order_by(Products.product_name)
        )
        return list(result.scalars().all())
