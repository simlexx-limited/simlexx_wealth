from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import UserAccountStatus
from app.model.models import MoreAboutUsers, Users
from app.schema.more_about_user_schema import (
    MoreAboutUserCreate,
    MoreAboutUserUpdate,
    UploadedURL,
)
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    DuplicateAccountError,
    NothingToUpdateError,
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

    async def check_can_update(
        self, user_id: str, data: MoreAboutUserUpdate, has_files: bool
    ) -> MoreAboutUsers:
        """
        Everything that can reject an update before any file is uploaded, so a
        rejected request leaves nothing behind in the bucket. Returns the profile.

        Raises AccountNotFoundError when there is no profile, NothingToUpdateError
        when nothing was sent, and UnsupportedCountryError for an unserved country.
        """
        profile = await self._get_by_user_id(user_id)
        if not profile:
            raise AccountNotFoundError("No profile found for this user.")

        changes = self._changes(data)
        if not changes and not has_files:
            raise NothingToUpdateError()
        if "country_of_residence" in changes:
            ensure_country_served(changes["country_of_residence"])
        return profile

    async def update(
        self,
        user_id: str,
        data: MoreAboutUserUpdate,
        document_link: Optional[UploadedURL] = None,
        profile_picture: Optional[UploadedURL] = None,
    ) -> MoreAboutUsers:
        """
        Partially update the profile that belongs to user_id: the personal
        details given in data, and/or replacement files (already uploaded).

        Only what was actually sent is written. Editing a profile that has
        already been decided (approved or rejected) puts it back to pending, so a
        reviewer looks at the new details - which is also how a rejected client
        asks for another review. The reviewer's earlier comment stays visible
        until the next decision.

        Raises the same errors as check_can_update.
        """
        profile = await self.check_can_update(
            user_id, data, has_files=document_link is not None or profile_picture is not None
        )

        for field, value in self._changes(data).items():
            setattr(profile, field, value)
        if document_link is not None:
            profile.document_link = str(document_link.link)
        if profile_picture is not None:
            profile.profile_picture = str(profile_picture.link)

        await self._reopen_review(profile)

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_by_user_id(self, user_id: str) -> Optional[MoreAboutUsers]:
        """Internal lookup — returns None instead of raising."""
        statement = select(MoreAboutUsers).where(MoreAboutUsers.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @staticmethod
    def _changes(data: MoreAboutUserUpdate) -> dict:
        """The fields the caller actually sent (None means "leave as it is")."""
        return {
            field: value
            for field, value in data.model_dump(exclude_unset=True).items()
            if value is not None
        }

    async def _reopen_review(self, profile: MoreAboutUsers) -> None:
        """Send a decided profile back to pending, on the profile and on the user."""
        if profile.profile_verification_complete == UserAccountStatus.PENDING:
            return
        profile.profile_verification_complete = UserAccountStatus.PENDING
        user = await self.session.get(Users, profile.user_id)
        if user is not None:
            user.profile_verification_complete = UserAccountStatus.PENDING
