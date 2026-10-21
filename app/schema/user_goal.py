#!/usr/bin/env python3

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model.mod_enum import CurrencyType, GoalStatus
from app.model.mod_enum import SavingFrequency, IncomeFrequency

class UserGoalCreate(BaseModel):
    """
    Base class schema for user goal
    """
    product_id: Optional[str] = Field(default=None, description="The product this goal is invested through, if any")
    purpose:str = Field(description="Name and description of the goal")
    target_amount:Decimal = Field(gt=0, decimal_places=2, description="Target amount the user wishes to attend")
    current_amount:Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2, description="Current amount the user has")
    target_date:date = Field(description="by when the user needs to accomplish the goal. eg.2026-12-25")
    currency:CurrencyType = Field(description="Currency choosen")
    income_regularity:IncomeFrequency = Field(description="How often the user earns")
    minimum_income:Decimal = Field(gt=0, decimal_places=2, description="The least the user receives in one pay period, in the goal's currency")

    @model_validator(mode="after")
    def check_goal_is_still_open(self):
        if self.current_amount >= self.target_amount:
            raise ValueError("current_amount must be less than target_amount")
        if self.target_date <= date.today():
            raise ValueError("target_date must be in the future")
        return self

class UserGoalUpdate(BaseModel):
    """
    Partial update of the details a client can edit on their own goal. Every
    field is optional; only provided fields are written.

    Deliberately excludes income_regularity: it's a snapshot of the client's
    financial situation at the moment the goal (and its recommendation) was
    created, so editing it here would quietly invalidate an already-computed
    recommendation without recomputing it. Goal status has its own endpoint
    (see change_status / GoalStatus) and isn't set here either.
    """
    product_id: Optional[str] = None
    purpose: Optional[str] = None
    target_amount: Optional[Decimal] = Field(default=None, gt=0, decimal_places=2)
    current_amount: Optional[Decimal] = Field(default=None, ge=0, decimal_places=2)
    target_date: Optional[date] = None
    currency: Optional[CurrencyType] = None

class UserGoalResponse(BaseModel):
    """
    Response shape for a UserGoals record.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    product_id: Optional[str] = None
    purpose: str
    target_amount: Decimal = Field(decimal_places=2)
    current_amount: Decimal = Field(decimal_places=2)
    currency: CurrencyType
    target_date: date
    income_regularity: IncomeFrequency
    minimum_income: Decimal = Field(decimal_places=2)
    goal_status: GoalStatus
    created_at: datetime
    updated_at: datetime

class UserGoalPage(BaseModel):
    """One page of the caller's own goals, active ones first, newest first within each group."""
    items: list[UserGoalResponse]
    total: int
    page: int
    limit: int

class AlternativeSavingStrategy(BaseModel):
    saving_amount_recommended: Decimal = Field(gt=0, decimal_places=2, description="...")
    frequency: SavingFrequency
    reason: str

class GoalDashboardResponse(BaseModel):
    """
    A client's progress on one goal - everything View 10 of the app shows.
    """
    id: str
    purpose: str
    currency: CurrencyType
    target_amount: Decimal = Field(decimal_places=2)
    current_amount: Decimal = Field(decimal_places=2)
    amount_remaining: Decimal = Field(decimal_places=2)
    percent_complete: Decimal = Field(decimal_places=2, description="0-100")
    target_date: date
    total_contributed: Decimal = Field(decimal_places=2, description="Sum of completed deposits toward this goal")
    number_of_contributions: int
    growth: Decimal = Field(decimal_places=2, description="Simulated - 0 if no product is linked or it has no return rate set")
    next_recommended_amount: Optional[Decimal] = Field(default=None, decimal_places=2)
    next_recommended_frequency: Optional[SavingFrequency] = None

class SavingRecommendation(BaseModel):
    """
    Saving technique recommendation based on user income/frequency, goal target date
    and product choosen
    """
    saving_amount_recommended:Decimal = Field(gt=0, decimal_places=2, description="Saving amount recommended based on the product, revenue occurence/amount, and target date")
    currency:CurrencyType = Field(description="User choosen currency")
    frequency:SavingFrequency = Field(description="Debit daily, weekly, or  monthly")
    reason:str = Field(description="Why this can be a good saving strategy for the user")
    alternatives:Optional[list[AlternativeSavingStrategy]] = Field(
        default=None, max_length=2, description="Alternative saving strategies, up to 2"
    )
