from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import MoreAboutUsers
from app.schema.more_about_user_schema import MoreAboutUserCreate, MoreAboutUserUpdate
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError, DuplicateAccountError


class MoreAboutUserService:
    """
    CRUD service for the MoreAboutUsers model.

    Every method receives (or derives) the caller's user_id from the
    decoded JWT payload injected by the router, keeping business logic
    cleanly separated from the HTTP layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(self, user_id: str, data: MoreAboutUserCreate) -> MoreAboutUsers:
        """
        Insert a new MoreAboutUsers row for the given user.

        Raises DuplicateAccountError if a profile already exists for that user.
        """
        existing = await self._get_by_user_id(user_id)
        if existing:
            raise DuplicateAccountError(
                "A profile already exists for this user. Use the update endpoint instead."
            )

        profile = MoreAboutUsers(
            user_id=user_id,
            first_name=data.first_name,
            last_name=data.last_name,
            middle_name=data.middle_name,
            date_of_birth=data.date_of_birth,
            nationality=data.nationality,
            country_of_residence=data.country_of_residence,
            city_of_residence=data.city_of_residence,
            full_address=data.full_address,
            profession=data.profession,
            document_type=data.document_type,
            document_id=data.document_id,
            document_link=data.document_link,
            document_expiry_date=data.document_expiry_date,
            profile_picture=data.profile_picture,
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    # ------------------------------------------------------------------
    # Read – all
    # ------------------------------------------------------------------

    async def get_all(self) -> list[MoreAboutUsers]:
        """Return every MoreAboutUsers row, ordered by update time (newest first)."""
        statement = (
            select(MoreAboutUsers)
            .order_by(MoreAboutUsers.updated_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Read – by user_id
    # ------------------------------------------------------------------

    async def get_by_user_id(self, user_id: str) -> MoreAboutUsers:
        """
        Return the profile for the given user_id.

        Raises AccountNotFoundError when no matching row is found.
        """
        profile = await self._get_by_user_id(user_id)
        if not profile:
            raise AccountNotFoundError("No profile found for this user.")
        return profile

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update(self, user_id: str, data: MoreAboutUserUpdate) -> MoreAboutUsers:
        """
        Partially update the profile that belongs to user_id.

        Only fields explicitly set in the request body are written;
        omitted / None fields are left as-is (model_dump exclude_unset=True).

        Raises AccountNotFoundError when no matching row is found.
        """
        profile = await self._get_by_user_id(user_id)
        if not profile:
            raise AccountNotFoundError("No profile found for this user.")

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(profile, field, value)

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, user_id: str) -> dict:
        """
        Delete the profile for the given user_id.

        Raises AccountNotFoundError when no matching row is found.
        """
        profile = await self._get_by_user_id(user_id)
        if not profile:
            raise AccountNotFoundError("No profile found for this user.")

        await self.session.delete(profile)
        await self.session.commit()
        return {"success": True, "message": "Profile deleted successfully."}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_by_user_id(self, user_id: str) -> Optional[MoreAboutUsers]:
        """Internal lookup — returns None instead of raising."""
        statement = select(MoreAboutUsers).where(MoreAboutUsers.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
