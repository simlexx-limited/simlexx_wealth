from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.models import MoreAboutUsers
from app.schema.more_about_user_schema import (
    MoreAboutUserCreate,
    MoreAboutUserReview,
    MoreAboutUserUpdate,
    UploadedURL,
)
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    DuplicateAccountError,
    PermissionDeniedError,
)
from app.services.reference.country_service import ensure_country_served


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

    async def check_can_create(self, user_id: str, data: MoreAboutUserCreate) -> None:
        """
        Everything that can reject a new profile before any file is uploaded.

        Raises UnsupportedCountryError if the country of residence isn't served,
        and DuplicateAccountError if a profile already exists for that user.
        """
        ensure_country_served(data.country_of_residence)

        existing = await self._get_by_user_id(user_id)
        if existing:
            raise DuplicateAccountError(
                "A profile already exists for this user. Use the update endpoint instead."
            )

    async def create(
        self,
        user_id: str,
        data: MoreAboutUserCreate,
        document_link: UploadedURL,
        profile_picture: UploadedURL,
    ) -> MoreAboutUsers:
        """
        Insert a new MoreAboutUsers row for the given user, with the links of
        the already-uploaded identity document and profile picture.

        Raises the same errors as check_can_create.
        """
        await self.check_can_create(user_id, data)

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
            document_link=str(document_link.link),
            document_expiry_date=data.document_expiry_date,
            profile_picture=str(profile_picture.link),
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
        if update_data.get("country_of_residence") is not None:
            ensure_country_served(update_data["country_of_residence"])

        for field, value in update_data.items():
            setattr(profile, field, value)

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    async def review(
        self, user_id: str, reviewer_id: str, data: MoreAboutUserReview
    ) -> MoreAboutUsers:
        """
        Record a reviewer's decision on the profile that belongs to user_id.

        The reviewer and the timestamp are set here, not taken from the caller.
        Raises PermissionDeniedError if a reviewer tries to review their own
        profile, and AccountNotFoundError when there is no such profile.
        """
        if reviewer_id == user_id:
            raise PermissionDeniedError("You cannot review your own profile")

        profile = await self._get_by_user_id(user_id)
        if not profile:
            raise AccountNotFoundError("No profile found for this user.")

        profile.profile_verification_complete = data.profile_verification_complete
        profile.reviewer_id = reviewer_id
        profile.reviewed_by = data.reviewed_by
        profile.reviewed_at = datetime.now(timezone.utc)

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
