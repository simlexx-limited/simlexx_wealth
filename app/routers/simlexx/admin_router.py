from fastapi import Depends, Request
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import UserRole
from app.schema.admin_schema import AdminCreate, AdminSignup
from app.schema.agent_schema import AgentCreate, AgentCreated, AgentResponse, AgentStatusChange
from app.services.admin.admin_service import AdminService
from app.services.agents.agent_service import AgentService, agent_response
from app.services.auth.authorization import require_roles
from app.services.rate_limiter.limiter import OTP_RATE_LIMIT, get_client_ip, limiter

router = APIRouter(
    prefix="/api/v1/simlexx/admin",
    tags=["Simlexx Admin"],
    )

# Everything below (except the first-run setup) is for Simlexx administrators only.
admin_only = Depends(require_roles(UserRole.ADMIN))


@router.post("/register")
@limiter.limit(OTP_RATE_LIMIT)
async def register_first_admin(
    request:Request,
    admin_data:AdminSignup,
    session:AsyncSession = Depends(get_session)
):
    """
    First-run setup of the Simlexx administrator. Open only while no admin
    exists; once one has been created this returns 403 for everyone.

    Confirm the phone number afterwards with POST
    /api/v1/auth/phone_auth/register/confirm, then log in.
    """
    return await AdminService(session).register_first_admin(
        data=admin_data,
        ip_address=await get_client_ip(request),
        endpoint=request.url.path,
        )


@router.post("/admins", status_code=201)
async def create_admin(
    request:Request,
    data:AdminCreate,
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    """
    Create another Simlexx administrator. They sign in with their phone number
    and a temporary password emailed to them, which they must change first.
    """
    return await AdminService(session).create_admin(
        data, created_by=token_payload["user_id"],
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )


@router.post("/agents", response_model=AgentCreated, status_code=201)
async def create_agent(
    request:Request,
    data:AgentCreate,
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    """
    Create an agent, someone who reviews client details. They get a temporary
    password by email and must change it at first sign-in. They can sign in with
    email and password, or with Google using the same email.
    """
    user, profile, queued = await AgentService(session).create_agent(
        data, created_by=token_payload["user_id"],
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )
    return AgentCreated(
        message="Agent created. A temporary password was sent to their email",
        credentials_email_queued=queued,
        data=agent_response(user, profile),
    )


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    """List every agent."""
    return await AgentService(session).list_agents()


@router.get("/agents/{user_id}", response_model=AgentResponse)
async def get_agent(
    user_id:str,
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    return await AgentService(session).get_agent(user_id)


@router.patch("/agents/{user_id}/status", response_model=AgentResponse)
async def set_agent_status(
    request:Request,
    user_id:str,
    data:AgentStatusChange,
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    """Deactivate or reactivate an agent. It applies on their very next request."""
    return await AgentService(session).set_status(
        user_id, data.active, data.reason, actor_id=token_payload["user_id"],
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )


@router.post("/agents/{user_id}/reset-password")
async def reset_agent_password(
    request:Request,
    user_id:str,
    token_payload:dict = admin_only,
    session:AsyncSession = Depends(get_session),
):
    """
    Last-resort password recovery for an agent: emails them a one-time link to
    choose a new password. The link is never shown here, only sent to the
    address on file.
    """
    return await AgentService(session).reset_password(
        user_id, actor_id=token_payload["user_id"],
        ip_address=await get_client_ip(request), endpoint=request.url.path,
    )
