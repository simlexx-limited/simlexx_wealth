from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import Users


async def revoke_sessions(session: AsyncSession, user_id: str) -> None:
    """
    Invalidate every phone/password token already issued to this account by
    bumping its token_version (the gate compares it on each request). Done as
    a single UPDATE so two concurrent revocations can't lose an increment.
    """
    await session.execute(
        update(Users).where(Users.id == user_id).values(token_version=Users.token_version + 1)
    )
    await session.commit()
