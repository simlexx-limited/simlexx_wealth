import logging
from typing import Optional

import sqlalchemy
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.mod_enum import AccountStatus, AuthProvider, UserAccountStatus, UserRole
from app.model.models import AgentProfiles, Users
from app.schema.agent_schema import AgentCreate, AgentResponse
from app.services.auth.firebase_auth import firebase_accounts
from app.services.auth.firebase_auth.auth_errors import (
    AccountNotFoundError,
    AuthError,
    DuplicateAccountError,
    EmailNotQueuedError,
)
from app.services.auth.temp_password import generate_temporary_password
from app.services.email import agent_emails
from app.services.logs.system_log_service import SystemLogService

logger = logging.getLogger(__name__)


def agent_response(user: Users, profile: AgentProfiles) -> AgentResponse:
    return AgentResponse(
        user_id=str(user.id),
        first_name=profile.first_name,
        last_name=profile.last_name,
        email_address=str(user.email_address) if user.email_address else None,
        phone_number=str(user.phone_number),
        job_title=profile.job_title,
        status=user.status,
        must_change_password=bool(user.must_change_password),
        created_at=profile.created_at,
    )


def mask_email(email: str) -> str:
    """j***@example.com - enough to recognise, not enough to harvest."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}"


class AgentService:
    """
    Simlexx administrators create and manage the agents who review client
    profiles. Agents sign in with Firebase (email and password, or Google).
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logs = SystemLogService(session)

    async def ensure_unique(self, email: str, phone_number: str) -> None:
        found = await self.session.execute(
            select(Users.email_address, Users.phone_number).where(
                or_(Users.email_address == email, Users.phone_number == phone_number)
            )
        )
        rows = found.all()
        email_taken = any(r[0] == email for r in rows)
        phone_taken = any(r[1] == phone_number for r in rows)
        if email_taken and phone_taken:
            raise DuplicateAccountError("Both this phone number and email are already registered")
        if email_taken:
            raise DuplicateAccountError("This email address is already registered")
        if phone_taken:
            raise DuplicateAccountError("This phone number is already registered")

    async def create_agent(
        self,
        data: AgentCreate,
        created_by: str,
        ip_address: Optional[str] = None,
        endpoint: str = "",
    ) -> tuple[Users, AgentProfiles, bool]:
        """
        Create an agent: a Firebase email/password account with a random temporary
        password, our own Users row and the agent profile. The temporary password
        is emailed and must be changed at first sign-in. The agent can also sign
        in with Google once, using the same (verified) email.

        Returns (user, profile, credentials_email_queued).
        """
        email = str(data.email_address).lower()
        await self.ensure_unique(email, data.phone_number)

        temporary_password = generate_temporary_password()
        display_name = f"{data.first_name} {data.last_name}"
        uid = await firebase_accounts.create_email_account(email, temporary_password, display_name)

        try:
            user = Users(
                email_address=email,
                phone_number=data.phone_number,
                auth_provider=AuthProvider.FIREBASE_SIMPLE,
                firebase_uid=uid,
                role=UserRole.AGENT,
                status=AccountStatus.ACTIVE,
                profile_verification_complete=UserAccountStatus.PENDING,
                phone_number_verified=True,   # vouched for by the admin who created the account
                email_verified=False,
                must_change_password=True,
            )
            self.session.add(user)
            await self.session.flush()
            profile = AgentProfiles(
                user_id=user.id,
                first_name=data.first_name,
                last_name=data.last_name,
                job_title=data.job_title,
                created_by=created_by,
            )
            self.session.add(profile)
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            try:
                await firebase_accounts.delete_account(uid)   # don't leave an orphan sign-in behind
            except Exception:
                logger.exception("Could not remove Firebase account %s after a failed creation", uid)
            if isinstance(exc, sqlalchemy.exc.IntegrityError):
                raise DuplicateAccountError("Account already exists")
            raise
        await self.session.refresh(user)
        await self.session.refresh(profile)

        queued = True
        try:
            agent_emails.send_account_created_email(
                to_address=email,
                first_name=data.first_name,
                account_label="agent",
                login_id=email,
                temporary_password=temporary_password,
            )
        except Exception:
            queued = False
            logger.exception("Could not queue the credentials email for %s", user.id)

        await self.logs.create_log(
            action="create_agent", entity="Users", user_id=created_by, entity_id=str(user.id),
            description=f"Agent account created; credentials email {'queued' if queued else 'NOT queued'}",
            ip_address=ip_address, endpoint=endpoint,
        )
        return user, profile, queued

    async def load_agent(self, user_id: str) -> tuple[Users, AgentProfiles]:
        found = await self.session.execute(
            select(Users, AgentProfiles)
            .join(AgentProfiles, AgentProfiles.user_id == Users.id)
            .where(Users.id == user_id, Users.role == UserRole.AGENT)
        )
        row = found.first()
        if row is None:
            raise AccountNotFoundError("Agent not found")
        return row[0], row[1]

    async def list_agents(self) -> list[AgentResponse]:
        found = await self.session.execute(
            select(Users, AgentProfiles)
            .join(AgentProfiles, AgentProfiles.user_id == Users.id)
            .where(Users.role == UserRole.AGENT)
            .order_by(AgentProfiles.created_at.desc())
        )
        return [agent_response(u, p) for u, p in found.all()]

    async def get_agent(self, user_id: str) -> AgentResponse:
        user, profile = await self.load_agent(user_id)
        return agent_response(user, profile)

    async def set_status(
        self,
        user_id: str,
        active: bool,
        reason: Optional[str],
        actor_id: str,
        ip_address: Optional[str] = None,
        endpoint: str = "",
    ) -> AgentResponse:
        """Deactivate or reactivate an agent. Takes effect on their very next request."""
        user, profile = await self.load_agent(user_id)

        user.status = AccountStatus.ACTIVE if active else AccountStatus.DISABLE
        await self.session.commit()

        # The database status is what the gate checks. Disabling the Firebase
        # account too also stops it signing in and refreshing tokens.
        if user.firebase_uid:
            try:
                await firebase_accounts.set_account_disabled(str(user.firebase_uid), not active)
            except Exception:
                logger.exception("Could not update the Firebase account for %s", user.id)

        if user.email_address:
            try:
                agent_emails.send_account_status_email(str(user.email_address), profile.first_name, active, reason)
            except Exception:
                logger.exception("Could not queue the status email for %s", user.id)

        await self.logs.create_log(
            action="reactivate_agent" if active else "deactivate_agent", entity="Users",
            user_id=actor_id, entity_id=str(user.id),
            description=f"Agent account {'reactivated' if active else 'deactivated'}"
                        + (f": {reason}" if reason else ""),
            ip_address=ip_address, endpoint=endpoint,
        )
        await self.session.refresh(user)
        return agent_response(user, profile)

    async def reset_password(
        self,
        user_id: str,
        actor_id: str,
        ip_address: Optional[str] = None,
        endpoint: str = "",
    ) -> dict:
        """
        Last-resort recovery: end the agent's sessions and email them a one-time
        link to choose a new password. The link is only ever emailed to the
        address on file - it is never returned to the administrator.
        """
        user, profile = await self.load_agent(user_id)
        if not (user.firebase_uid and user.email_address):
            raise AuthError("This account has no email sign-in to reset")

        email = str(user.email_address)
        link = await firebase_accounts.make_password_reset_link(email)
        await firebase_accounts.revoke_account_sessions(str(user.firebase_uid))
        try:
            agent_emails.send_password_reset_email(email, profile.first_name, link)
        except Exception:
            logger.exception("Could not queue the reset email for %s", user.id)
            raise EmailNotQueuedError()

        await self.logs.create_log(
            action="reset_agent_password", entity="Users", user_id=actor_id,
            entity_id=str(user.id), description="Password reset link emailed",
            ip_address=ip_address, endpoint=endpoint,
        )
        return {"status": True, "message": f"A one-time password reset link was emailed to {mask_email(email)}"}
