from datetime import date, datetime
from typing import Any, Callable

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
    OtpPurpose
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
    Date
    )
from sqlalchemy.dialects.postgresql import ARRAY


# ============================================================
# Shared across every role (client, agent, broker, fund manager)
# ============================================================

class Users(Base):
    __tablename__ = "Users"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    email_address:Mapped[String] = mapped_column(String, unique=True, nullable=True)
    phone_number:Mapped[String] = mapped_column(String, nullable=False, unique=True)
    password:Mapped[String] = mapped_column(String, nullable=True)
    auth_provider:Mapped[AuthProvider] = mapped_column(Enum(AuthProvider), nullable=False)
    firebase_uid:Mapped[String] = mapped_column(String, unique=True, nullable=True)
    status:Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE)
    role:Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    profile_verification_complete:Mapped[UserAccountStatus] = mapped_column(Enum(UserAccountStatus), nullable=False, default=UserAccountStatus.PENDING)
    phone_number_verified:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=False)
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
    user_account:Mapped["UserAccount"] = relationship("UserAccount", back_populates="user", uselist=False)
    transactions:Mapped[list["Transaction"]] = relationship("Transaction", back_populates="user")
    goals:Mapped[list["UserGoals"]] = relationship("UserGoals", back_populates="user")
    notifications:Mapped[list["Notifications"]] = relationship("Notifications", back_populates="user")
    system_logs:Mapped[list["SystemLogs"]] = relationship("SystemLogs", back_populates="user")
    mfa:Mapped["UserAuthMFA"] = relationship("UserAuthMFA", back_populates="user", uselist=False)
    otp_verifications:Mapped[list["OtpVerification"]] = relationship("OtpVerification", back_populates="user")
    reviewed_profiles:Mapped[list["MoreAboutUsers"]] = relationship("MoreAboutUsers", foreign_keys="[MoreAboutUsers.reviewer_id]", back_populates="reviewer")

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
    attempts:Mapped[Integer] = mapped_column(Integer, nullable=False, default=0)
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
    document_type:Mapped[String] = mapped_column(String, nullable=False)
    document_id:Mapped[String] = mapped_column(String, nullable=False)
    document_link:Mapped[String] = mapped_column(String, nullable=False)
    document_expiry_date:Mapped[Date] = mapped_column(Date, nullable=False)
    profile_picture:Mapped[String] = mapped_column(String, nullable=False)
    profile_verification_complete:Mapped[UserAccountStatus] = mapped_column(Enum(UserAccountStatus), nullable=False, default=UserAccountStatus.PENDING)
    reviewer_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=True)
    reviewed_by:Mapped[String] = mapped_column(String, nullable=True)  # name of the reviewer or organization
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
                DateTime(timezone=True), server_default=func.now()
                )
    user:Mapped["Users"] = relationship("Users", foreign_keys=[user_id], back_populates="more_about_user")
    reviewer:Mapped["Users"] = relationship("Users", foreign_keys=[reviewer_id], back_populates="reviewed_profiles")


class UserAccount(Base):
    __tablename__ = "UserAccount"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), unique=True, nullable=False)
    bank_name:Mapped[String] = mapped_column(String, nullable=False)
    account_number:Mapped[String] = mapped_column(String, nullable=False)
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
    user:Mapped["Users"] = relationship("Users", back_populates="user_account")
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
    goal_status:Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), nullable=False, default=GoalStatus.ONGOING)
    onboarding_complete:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=False)
    created_at:Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    updated_at: Mapped[datetime] = mapped_column(
                        DateTime(timezone=True), server_default=func.now()
                        )
    user:Mapped["Users"] = relationship("Users", back_populates="goals")
    product:Mapped["Products"] = relationship("Products", back_populates="goals")

class Transaction(Base):
    __tablename__ = "transactions"
    id:Mapped[String] = mapped_column(String, primary_key=True, unique=True, default=id_generator)
    user_id:Mapped[String] = mapped_column(ForeignKey("Users.id"), nullable=False)
    account_id:Mapped[String] = mapped_column(ForeignKey("UserAccount.id"), nullable=False)
    goal_id:Mapped[String] = mapped_column(ForeignKey("UserGoals.id"), nullable=True)
    type:Mapped[TransactionType] = mapped_column(Enum(TransactionType), nullable=False)
    amount:Mapped[Numeric] = mapped_column(Numeric(30, 8), nullable=False)
    currency:Mapped[CurrencyType] = mapped_column(Enum(CurrencyType), nullable=False)
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
    is_read:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=False)
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
    is_active:Mapped[Boolean] = mapped_column(Boolean, nullable=False, default=True)
    document_id:Mapped[String] = mapped_column(String, nullable=False)
    document_link:Mapped[String] = mapped_column(String, nullable=False)
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
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user:Mapped["Users"] = relationship("Users", back_populates="system_logs")
