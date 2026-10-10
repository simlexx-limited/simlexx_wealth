from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.model.mod_enum import CurrencyType, TransactionStatus

class UserWithdraw(BaseModel):
    """
    Withdraw request. amount/currency is what the client wants to receive -
    the account is always debited in its own currency, converted if needed.
    """
    account_id:str = Field(description="User Account ID")
    goal_id:Optional[str] = Field(default=None, description="User goal Id this withdrawal is drawn against, if any")
    amount:Decimal = Field(gt=0, decimal_places=2, description="Amount the client wants to receive")
    currency:CurrencyType = Field(description="Currency the client wants to receive")
    bank_reference:str = Field(description="Bank reference code for the payout")

class ConfirmWithdraw(UserWithdraw):
    """Withdraw request plus the OTP code sent by /otp, to confirm and save it."""
    otp_code:str = Field(description="OTP code sent to confirm this withdrawal")

class WithdrawResponse(BaseModel):
    """Withdraw response"""
    first_name:str
    last_name:str
    account_id:str
    amount_withdrawn:str
    currency:CurrencyType
    requested_amount:Decimal
    requested_currency:CurrencyType
    bank_reference:str
    withdraw_at:datetime

class WithdrawHistoryItem(BaseModel):
    """One withdrawal, as stored - for the paginated account history."""
    model_config = ConfigDict(from_attributes=True)

    id:str
    account_id:str
    goal_id:Optional[str] = None
    amount:Decimal
    currency:CurrencyType
    requested_amount:Decimal
    requested_currency:CurrencyType
    status:TransactionStatus
    bank_reference:Optional[str] = None
    failure_reason:Optional[str] = None
    created_at:datetime

class WithdrawPage(BaseModel):
    """One page of an account's withdrawal history, newest first."""
    items:list[WithdrawHistoryItem]
    total:int
    page:int
    limit:int
