from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, EmailStr
from app.model.mod_enum import CurrencyType, TransactionType, TransactionStatus

class UserDeposit(BaseModel):
    """
    Deposit request
    """
    account_id:str = Field(description="User Account ID")
    goal_id:Optional[str] = Field(default=None, description="User goal Id in which transaction must be made")
    type:TransactionType = Field(description="User transaction type")
    amount:Decimal = Field(gt=0, description="Amount of the transaction")
    currency:CurrencyType = Field(description="Currency used for the transaction")
    bank_reference:str = Field(description="Bank reference code")
    unit_master_reference:str = Field(description="Unit master reference")

class ConfirmDeposit(UserDeposit):
    """Deposit request plus the OTP code sent by /otp, to confirm and save it."""
    otp_code:str = Field(description="OTP code sent to confirm this deposit")

class UserDBDeposit(BaseModel):
    """
    Base model for user deposit saved in DB 
    """
    account_id:str = Field(description="User Account ID")
    goal_id:Optional[str] = Field(default=None, description="User goal Id in which transaction must be made")
    type:TransactionType = Field(description="User transaction type")
    amount:Decimal = Field(gt=0, description="Amount of the transaction")
    currency:CurrencyType = Field(description="Currency used for the transaction")
    status:TransactionStatus = Field(description="Transaction status")
    bank_reference:str = Field(description="Bank reference code")
    unit_master_reference:str = Field(description="Unit master reference")
    failure_reason:Optional[str] = Field(default=None, description="Failure reason if request failed")
    fx_rate_to_base:Decimal = Field(description="Fix transaction rate")

class SendUserOTP(BaseModel):
    email_address:Optional[EmailStr] = Field(description="User email address")
    phone_number:Optional[str] = Field(description="User phone number")
    otp_code:str = Field(description="One time OTP sent to the user for verification. expired in 15 min")

class VerifyOTP(BaseModel):
    otp_code:str = Field(description="User typed OTP for verification")

class DepositResponse(BaseModel):
    """
    Deposit response
    """
    first_name:str
    last_name:str
    account_id:str
    amount_deposited:str
    currency:CurrencyType
    requested_amount:Decimal
    requested_currency:CurrencyType
    unit_trust_reference:str
    bank_reference:str
    deposit_at:datetime

class DepositHistoryItem(BaseModel):
    """One deposit, as stored - for the paginated account history."""
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
    unit_master_reference:Optional[str] = None
    failure_reason:Optional[str] = None
    created_at:datetime

class DepositPage(BaseModel):
    """One page of an account's deposit history, newest first."""
    items:list[DepositHistoryItem]
    total:int
    page:int
    limit:int
