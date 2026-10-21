from datetime import date, datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.model.base import Base
from app.model.generator import id_generator
from app.model.mod_enum import (
    AuthProvider,
    UserRole,
    UserAccountStatus,
    AccountStatus,
    NotificationChannel,
    TransactionType,
    TransactionStatus,
    GoalStatus,
    ProductType,
    MultifactorAuth,
    NotificationStatus,
    CurrencyType,
    OtpPurpose,
    DocumentType,
    IncomeFrequency,
    SavingFrequency
    )
from sqlalchemy import (
    Float,
    Integer,
    Boolean,
    Enum,
    DateTime,
    ForeignKey,
    func,
    Numeric,
    String,
    Text,
    Date,
    UniqueConstraint
    )
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


# ============================================================
# Shared across every role (client, agent, broker, fund manager)
# ============================================================

class Users(Base):
    __tablename__ = "Users"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    email_address:Mapped[String] = mapped_column(String, unique=True, nullable=True)
    phone_number:Mapped[String] = mapped_column(String, nullable=False, unique=True)
    password:Mapped[Optional[str]] = mapped_column(String, nullable=True)
    auth_provider:Mapped[AuthProvider] = mapped_column(Enum(AuthProvider), nullable=False)
    firebase_uid:Mapped[String] = mapped_column(String, unique=True, nullable=True)
    status:Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE)
    role:Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    profile_verification_complete:Mapped[UserAccountStatus] = mapped_column(Enum(UserAccountStatus), nullable=False, default=UserAccountStatus.PENDING)
    phone_number_verified:Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password:Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")  # set while a temporary password is in use
    token_version:Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # bumped to invalidate every token issued before
    email_verified:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at:Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), server_default=func.now()
        )
    more_about_user:Mapped["MoreAboutUsers"] = relationship("MoreAboutUsers", foreign_keys="[MoreAboutUsers.user_id]", back_populates="user", uselist=False)
    user_accounts:Mapped[list["UserAccount"]] = relationship("UserAccount", back_populates="user")
    transactions:Mapped[list["Transaction"]] = relationship("Transaction", back_populates="user")
    goals:Mapped[list["UserGoals"]] = relationship("UserGoals", back_populates="user")
    goal_recommendations:Mapped[list["GoalRecommendations"]] = relationship("GoalRecommendations", back_populates="user")
    notifications:Mapped[list["Notifications"]] = relationship("Notifications", back_populates="user")
    system_logs:Mapped[list["SystemLogs"]] = relationship("SystemLogs", back_populates="user")
    mfa:Mapped["UserAuthMFA"] = relationship("UserAuthMFA", back_populates="user", uselist=False)
    otp_verifications:Mapped[list["OtpVerification"]] = relationship("OtpVerification", back_populates="user")
    reviewed_profiles:Mapped[list["MoreAboutUsers"]] = relationship("MoreAboutUsers", foreign_keys="[MoreAboutUsers.reviewer_id]", back_populates="reviewer")
    agent_profile:Mapped[Optional["AgentProfiles"]] = relationship("AgentProfiles", foreign_keys="[AgentProfiles.user_id]", back_populates="user", uselist=False)

# ============================================================
# Agents: the people who verify client details. Simlexx creates them.
# ============================================================

class AgentProfiles(Base):
    __tablename__ = "AgentProfiles"
    id:Mapped[str] = mapped_column(String, primary_key=True, default=id_generator)
    user_id:Mapped[str] = mapped_column(ForeignKey("Users.id"), unique=True, nullable=False)
    first_name:Mapped[str] = mapped_column(String, nullable=False)
    last_name:Mapped[str] = mapped_column(String, nullable=False)
    job_title:Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by:Mapped[Optional[str]] = mapped_column(ForeignKey("Users.id"), nullable=True)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user:Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="agent_profile")

class UserAuthMFA(Base):
    __tablename__ = "AuthMFAMethod"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), unique=True, nullable=False)
    is_enable:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=False)
    mfa_methods:Mapped[list[MultifactorAuth]] = mapped_column(ARRAY(Enum(MultifactorAuth)), nullable=False, default=list)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user:Mapped["Users"] = relationship("Users", back_populates="mfa")

class OtpVerification(Base):
    __tablename__ = "OtpVerifications"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    channel:Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel), nullable=False)
    code:Mapped[String] = mapped_column(String, nullable=False)
    purpose:Mapped[OtpPurpose] = mapped_column(Enum(OtpPurpose), nullable=False)
    expires_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts:Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user:Mapped["Users"] = relationship("Users", back_populates="otp_verifications")

# ============================================================
# Client-only: KYC profile, linked bank account, goals, transactions,
# and notifications. Agent/broker users don't have rows here for
# themselves — they show up as the reviewer on a client's MoreAboutUsers.
# ============================================================

class MoreAboutUsers(Base):
    __tablename__ = "MoreAboutUsers"

    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), unique=True, nullable=False)
    first_name:Mapped[String] = mapped_column(String)
    last_name:Mapped[String] = mapped_column(String)
    middle_name:Mapped[String] = mapped_column(String, nullable=True)
    date_of_birth:Mapped[date] = mapped_column(DateTime, nullable=False)
    nationality:Mapped[String] = mapped_column(String, nullable=False)
    country_of_residence:Mapped[String] = mapped_column(String, nullable=False)
    city_of_residence:Mapped[String] = mapped_column(String, nullable=False)
    full_address:Mapped[String] = mapped_column(String, nullable=False)
    profession:Mapped[String] = mapped_column(String, nullable=False)
    document_type:Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    document_id:Mapped[String] = mapped_column(String, nullable=False)
    document_keys:Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)  # every ID document ever uploaded, oldest first - appended to, never overwritten, so a reviewer can tell a genuine resubmission from the same file sent twice
    document_expiry_date:Mapped[Date] = mapped_column(Date, nullable=False)
    profile_picture_keys:Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)  # same append-only history as document_keys
    profile_verification_complete:Mapped[UserAccountStatus] = mapped_column(Enum(UserAccountStatus), nullable=False, default=UserAccountStatus.PENDING)
    onboarding_complete:Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # has the user finished onboarding (distinct from profile_verification_complete, the reviewer's decision)
    reviewer_id:Mapped[Optional[str]] = mapped_column(ForeignKey("Users.id"), nullable=True)
    reviewed_by:Mapped[Optional[str]] = mapped_column(String, nullable=True)  # name of the reviewing agent
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment:Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # the reviewer's reason for approving or rejecting
    updated_at: Mapped[datetime] = mapped_column(
                DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
                )
    user:Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="more_about_user")
    reviewer:Mapped["Users"] = relationship("Users", foreign_keys=[reviewer_id], back_populates="reviewed_profiles")


class UserAccount(Base):
    __tablename__ = "UserAccount"
    __table_args__ = (
        # account numbers are only guaranteed unique within one bank, not across all banks
        UniqueConstraint('bank_name', 'account_number', name='uq_UserAccount_bank_account_number'),
    )
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)  # not unique: a client may link more than one account
    bank_name:Mapped[String] = mapped_column(String, nullable=False)
    account_number:Mapped[String] = mapped_column(String, nullable=False)  # unique together with bank_name, see __table_args__
    balance:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False, default=0)
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
    account_document_id:Mapped[String] = mapped_column(String, nullable=True)
    account_document:Mapped[String] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    updated_at: Mapped[datetime] = mapped_column(
                    DateTime(timezone=True), server_default=func.now()
                    )
    user:Mapped["Users"] = relationship("Users", back_populates="user_accounts")
    transactions:Mapped[list["Transaction"]] = relationship("Transaction", back_populates="account")

class UserGoals(Base):
    __tablename__ = "UserGoals"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    product_id:Mapped[String] = mapped_column(ForeignKey("products.id"), nullable=True)
    target_amount:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)
    current_amount:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False, default=0)
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
    target_date:Mapped[Date] = mapped_column(Date, nullable=False)
    purpose:Mapped[Text] = mapped_column(Text, nullable=False)
    income_regularity:Mapped[IncomeFrequency] = mapped_column(Enum(IncomeFrequency), nullable=False)  # snapshot of the client's income pattern when this goal was created; never edited afterward
    minimum_income:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)  # snapshot of the least the client receives per pay period when this goal was created; never edited afterward
    goal_status:Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), nullable=False, default=GoalStatus.ONGOING)
    created_at:Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    updated_at: Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    user:Mapped["Users"] = relationship("Users", back_populates="goals")
    product:Mapped["Products"] = relationship("Products", back_populates="goals")

class GoalRecommendations(Base):
    """
    One row per saving-technique recommendation the financial agent generates,
    for internal review. Append-only: written once, at generation time, never
    edited. Stores the goal request it was generated from alongside the result,
    since no goal exists yet to hold it.
    """
    __tablename__ = "GoalRecommendations"
    id:Mapped[String] = mapped_column(String, primary_key=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    request:Mapped[dict] = mapped_column(JSONB, nullable=False)  # the UserGoalCreate the recommendation was generated from
    saving_amount_recommended:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
    frequency:Mapped[SavingFrequency] = mapped_column(Enum(SavingFrequency), nullable=False)
    reason:Mapped[Text] = mapped_column(Text, nullable=False)
    alternatives:Mapped[Optional[list[dict]]] = mapped_column(JSONB, nullable=True)  # up to 2 AlternativeSavingStrategy, already validated before being written
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user:Mapped["Users"] = relationship("Users", back_populates="goal_recommendations")

class Transaction(Base):
    __tablename__ = "transactions"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    account_id:Mapped[String] = mapped_column(ForeignKey("UserAccount.id"), nullable=False)
    goal_id:Mapped[String] = mapped_column(ForeignKey("UserGoals.id"), nullable=True)
    type:Mapped[TransactionType] = mapped_column(Enum(TransactionType), nullable=False)
    amount:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)  # settled: what actually moved, in the account's own currency
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)  # settled: always the account's own currency
    requested_amount:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)  # what the client asked for - sent (deposit) or to receive (withdrawal) - before conversion
    requested_currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
    fx_rate_to_base:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=True)  # rate from `currency` to the platform's base currency, captured at transaction time
    status:Mapped[TransactionStatus] = mapped_column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING)
    bank_reference:Mapped[String] = mapped_column(String, nullable=True)  # reference from the transaction made directly via the bank api
    unit_master_reference:Mapped[String] = mapped_column(String, nullable=True)
    failure_reason:Mapped[String] = mapped_column(String, nullable=True)
    created_at:Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    updated_at:Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    user:Mapped["Users"] = relationship("Users", back_populates="transactions")
    account:Mapped["UserAccount"] = relationship("UserAccount", back_populates="transactions")

class Notifications(Base):
    __tablename__ = "NotificationsLogs"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    channel:Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel), nullable=False)
    type:Mapped[String] = mapped_column(String, nullable=False)
    content:Mapped[Text] = mapped_column(String, nullable=False)
    status:Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING)
    is_read:Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at:Mapped[datetime] = mapped_column(
                    DateTime(timezone=True), server_default=func.now()
                    )
    user:Mapped["Users"] = relationship("Users", back_populates="notifications")

# ============================================================
# Cross-cutting (not tied to a single role)
# ============================================================

class Products(Base):
    __tablename__ = "products"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    company_name:Mapped[String] = mapped_column(String, nullable=False)
    product_name:Mapped[String] = mapped_column(String, nullable=False)
    product_type:Mapped[ProductType] = mapped_column(Enum(ProductType), nullable=False)
    description:Mapped[Text] = mapped_column(Text, nullable=False)
    minimum_investment:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=True)
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
    annual_return_rate:Mapped[Numeric] = mapped_column(Numeric(5, 4), nullable=True)  # e.g. 0.11 for 11%/year - simulated, no partner-supplied figure exists yet
    is_active:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=True)
    document_id:Mapped[String] = mapped_column(String, nullable=False)
    document_link:Mapped[str] = mapped_column(String, nullable=False)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    goals:Mapped[list["UserGoals"]] = relationship("UserGoals", back_populates="product")

class SystemLogs(Base):
    __tablename__ = "SystemLogs"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=True)
    action:Mapped[String] = mapped_column(String, nullable=False)
    entity:Mapped[String] = mapped_column(String, nullable=False)
    entity_id:Mapped[String] = mapped_column(String, nullable=True)
    description:Mapped[Text] = mapped_column(Text, nullable=True)
    ip_address:Mapped[String] = mapped_column(String, nullable=True)
    endpoint:Mapped[String] = mapped_column(String, nullable=True)
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user:Mapped["Users"] = relationship("Users", back_populates="system_logs")
