from fastapi import Depends, Query, Request
from fastapi.routing import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import UserRole
from app.schema.deposit import ConfirmDeposit, DepositHistoryItem, DepositPage, DepositResponse, SendUserOTP
from app.schema.user_account_schema import UserAccountResponse
from app.services.auth.authorization import require_roles
from app.services.auth.current_user import get_current_user
from app.services.rate_limiter.limiter import get_client_ip
from app.services.transaction.deposit.deposit import Deposit

router = APIRouter(
    prefix="/api/v1/deposit",
    dependencies=[Depends(get_current_user)],
    tags=["Deposit"]
    )


@router.get("/accounts", response_model=list[UserAccountResponse])
async def get_user_accounts(
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """All of the caller's own bank accounts, for choosing which to deposit into."""
    return await Deposit(session).get_user_accounts(user_id=token_payload["user_id"])


@router.post("/otp", response_model=SendUserOTP)
async def send_deposit_otp(
    request:Request,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """
    Send a one-time code to confirm an upcoming deposit - by email if the
    caller's email is verified, otherwise by phone. Nothing about the
    deposit itself is saved yet; send it to /deposit with this code.
    """
    return await Deposit(session).send_otp_deposit(
        user_id=token_payload["user_id"], ip_address=await get_client_ip(request),
    )


@router.post("", response_model=DepositResponse, status_code=201)
async def confirm_deposit(
    request:Request,
    deposit_info:ConfirmDeposit,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """
    Verify the code sent by /otp and, if valid, save the deposit. The
    account (and the goal, if one is given) must belong to the caller.
    """
    return await Deposit(session).save_deposit(
        user_deposit=deposit_info,
        user_id=token_payload["user_id"],
        otp_code=deposit_info.otp_code,
        ip_address=await get_client_ip(request),
    )


@router.get("/all", response_model=DepositPage)
async def get_all_deposits(
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """One page of every deposit the caller has made, across all of their own accounts, newest first."""
    items, total = await Deposit(session).get_all_deposits(
        user_id=token_payload["user_id"], page=page, limit=limit,
    )
    return DepositPage(
        items=[DepositHistoryItem.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )


@router.get("/{account_id}", response_model=DepositPage)
async def get_account_deposits(
    account_id:str,
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """One page of deposits made to one of the caller's own accounts, newest first."""
    items, total = await Deposit(session).get_account_deposits(
        user_id=token_payload["user_id"], account_id=account_id, page=page, limit=limit,
    )
    return DepositPage(
        items=[DepositHistoryItem.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )
