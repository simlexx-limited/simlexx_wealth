from fastapi import Depends, Query, Request
from fastapi.routing import APIRouter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_session
from app.model.mod_enum import UserRole
from app.schema.deposit import SendUserOTP
from app.schema.withdraw import ConfirmWithdraw, WithdrawHistoryItem, WithdrawPage, WithdrawResponse
from app.services.auth.authorization import require_roles
from app.services.auth.current_user import get_current_user
from app.services.rate_limiter.limiter import get_client_ip
from app.services.transaction.withdraw.withdraw import Withdraw

router = APIRouter(
    prefix="/api/v1/withdraw",
    dependencies=[Depends(get_current_user)],
    tags=["Withdraw"]
    )


@router.post("/otp", response_model=SendUserOTP)
async def send_withdraw_otp(
    request:Request,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """
    Send a one-time code to confirm an upcoming withdrawal - by email if the
    caller's email is verified, otherwise by phone. Nothing about the
    withdrawal itself is saved yet; send it to /withdraw with this code.
    """
    return await Withdraw(session).send_otp_withdraw(
        user_id=token_payload["user_id"], ip_address=await get_client_ip(request),
    )


@router.post("", response_model=WithdrawResponse, status_code=201)
async def confirm_withdraw(
    request:Request,
    withdraw_info:ConfirmWithdraw,
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """
    Verify the code sent by /otp and, if valid, save the withdrawal. The
    account (and the goal, if one is given) must belong to the caller, and
    the account must hold enough to cover it.
    """
    return await Withdraw(session).save_withdraw(
        user_withdraw=withdraw_info,
        user_id=token_payload["user_id"],
        otp_code=withdraw_info.otp_code,
        ip_address=await get_client_ip(request),
    )


@router.get("/all", response_model=WithdrawPage)
async def get_all_withdrawals(
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """One page of every withdrawal the caller has made, across all of their own accounts, newest first."""
    items, total = await Withdraw(session).get_all_withdrawals(
        user_id=token_payload["user_id"], page=page, limit=limit,
    )
    return WithdrawPage(
        items=[WithdrawHistoryItem.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )


@router.get("/{account_id}", response_model=WithdrawPage)
async def get_account_withdrawals(
    account_id:str,
    page:int = Query(1, ge=1),
    limit:int = Query(20, ge=1, le=100),
    token_payload:dict = Depends(require_roles(UserRole.CLIENT)),
    session:AsyncSession = Depends(get_session),
    ):
    """One page of withdrawals made from one of the caller's own accounts, newest first."""
    items, total = await Withdraw(session).get_account_withdrawals(
        user_id=token_payload["user_id"], account_id=account_id, page=page, limit=limit,
    )
    return WithdrawPage(
        items=[WithdrawHistoryItem.model_validate(item) for item in items],
        total=total, page=page, limit=limit,
    )
