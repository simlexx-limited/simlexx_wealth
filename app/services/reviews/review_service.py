import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import NotificationChannel, UserAccountStatus
from app.model.models import AgentProfiles, MoreAboutUsers, Users
from app.schema.review_schema import ReviewDecision
from app.services.auth.firebase_auth.auth_errors import AccountNotFoundError, PermissionDeniedError
from app.services.email import review_emails
from app.services.logs.system_log_service import SystemLogService
from app.services.notifications.notification_service import NotificationService

logger = logging.getLogger(__name__)


class ReviewService:
    """
    What a reviewer (an agent) can do with client profiles: load them, in full
    or by status, and approve or reject them with a comment.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_profiles(
        self, status: Optional[UserAccountStatus], page: int, limit: int
    ) -> tuple[list[MoreAboutUsers], int]:
        """One page of profiles (newest activity first), optionally only one status, plus the total."""
        statement = select(MoreAboutUsers)
        count = select(func.count()).select_from(MoreAboutUsers)
        if status is not None:
            statement = statement.where(MoreAboutUsers.profile_verification_complete == status)
            count = count.where(MoreAboutUsers.profile_verification_complete == status)

        statement = statement.order_by(MoreAboutUsers.updated_at.desc()).offset((page - 1) * limit).limit(limit)
        items = list((await self.session.execute(statement)).scalars().all())
        total = int((await self.session.execute(count)).scalar_one())
        return items, total

    async def get_profile(self, user_id: str) -> MoreAboutUsers:
        found = await self.session.execute(select(MoreAboutUsers).where(MoreAboutUsers.user_id == user_id))
        profile = found.scalar_one_or_none()
        if profile is None:
            raise AccountNotFoundError("No profile found for this user.")
        return profile

    async def reviewer_label(self, reviewer_id: str) -> str:
        """Who to show as the reviewer: the agent's name, or their id if they have none."""
        found = await self.session.execute(
            select(AgentProfiles.first_name, AgentProfiles.last_name).where(AgentProfiles.user_id == reviewer_id)
        )
        row = found.first()
        if row is None:
            return reviewer_id
        return f"{row[0]} {row[1]}"

    async def decide(
        self,
        user_id: str,
        reviewer: dict,
        data: ReviewDecision,
        ip_address: Optional[str] = None,
        endpoint: str = "",
    ) -> MoreAboutUsers:
        """
        Approve or reject a client's profile, and tell the client.

        An earlier decision can be changed. Who decided and when come from the
        reviewer's account and the server clock; the
        previous status and this comment are kept in the audit log. The client's
        account status is kept in step, and they get an in-app notification and,
        if they have an email address, an email. If the email can't be queued the
        decision still stands.
        """
        reviewer_id = reviewer["user_id"]
        if reviewer_id == user_id:
            raise PermissionDeniedError("You cannot review your own profile")

        profile = await self.get_profile(user_id)
        previous = profile.profile_verification_complete

        profile.profile_verification_complete = data.decision
        profile.reviewer_id = reviewer_id
        profile.reviewed_by = await self.reviewer_label(reviewer_id)
        profile.reviewed_at = datetime.now(timezone.utc)
        profile.review_comment = data.comment

        client = await self.session.get(Users, user_id)
        if client is not None:
            client.profile_verification_complete = data.decision

        await self.session.commit()
        await self.session.refresh(profile)

        await SystemLogService(self.session).create_log(
            action="review_profile", entity="MoreAboutUsers", user_id=reviewer_id,
            entity_id=str(profile.id),
            description=f"{previous.value} -> {data.decision.value}: {data.comment}",
            ip_address=ip_address, endpoint=endpoint,
        )
        await self.notify_client(user_id, client, profile, data)
        return profile

    async def notify_client(
        self, user_id: str, client: Optional[Users], profile: MoreAboutUsers, data: ReviewDecision
    ) -> None:
        approved = data.decision == UserAccountStatus.APPROVED
        content = (
            f"Your profile was {'approved' if approved else 'not approved'}. "
            f"Comment: {data.comment}"
        )
        try:
            await NotificationService(self.session).create_notification(
                user_id=user_id,
                channel=NotificationChannel.EMAIL,
                notification_type="profile_review",
                content=content,
            )
            if client is not None and client.email_address:
                review_emails.send_profile_review_email(
                    str(client.email_address), str(profile.first_name), approved, data.comment
                )
        except Exception:
            # the decision is already saved; a failed notification must not undo it
            logger.exception("Could not notify user %s about their profile review", user_id)
