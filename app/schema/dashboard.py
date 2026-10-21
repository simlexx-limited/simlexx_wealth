from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.model.mod_enum import CurrencyType, TransactionStatus, TransactionType


class AccountBalance(BaseModel):
    """One of the caller's own accounts, with its current balance."""
    model_config = ConfigDict(from_attributes=True)

    id:str
    bank_name:str
    account_number:str
    currency:CurrencyType
    balance:Decimal


class BalanceSummary(BaseModel):
    """The caller's balance across every one of their own accounts."""
    accounts:list[AccountBalance]
    total_by_currency:dict[CurrencyType, Decimal]


class DashboardTransactionItem(BaseModel):
    """One deposit or withdrawal, as stored - for the dashboard's unified transaction history."""
    model_config = ConfigDict(from_attributes=True)

    id:str
    account_id:str
    goal_id:Optional[str] = None
    type:TransactionType
    amount:Decimal
    currency:CurrencyType
    status:TransactionStatus
    bank_reference:Optional[str] = None
    failure_reason:Optional[str] = None
    created_at:datetime


class DashboardTransactionPage(BaseModel):
    """One page of the caller's deposits and withdrawals together, newest first."""
    items:list[DashboardTransactionItem]
    total:int
    page:int
    limit:int


class DashboardOverview(BaseModel):
    """Everything the app's main dashboard screen needs in one call."""
    total_balance_by_currency:dict[CurrencyType, Decimal]
    total_growth_by_currency:dict[CurrencyType, Decimal]
    active_goal_count:int
    recent_transactions:list[DashboardTransactionItem]
