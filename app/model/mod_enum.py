from enum import Enum as PyEnum

class AuthProvider(str, PyEnum):
    FIREBASE_GOOGLE = "firebase_google_signin"
    FIREBASE_SIMPLE = "firebase_email_password"
    PHONE_NUMBER = "phone_number_login"

class MultifactorAuth(str, PyEnum):
    BIOMETRIC_LOGIN = "biometric_enable"
    EMAIL_OTP_VERIFICATION = "email_verification"
    PHONE_NUMBER_OTP_VERIFICATION = "phone_verification"
    WHATSAPP_NUMBER_OTP_VERIFICATION = "whatsapp_verification"


class UserAccountStatus(str, PyEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    APPROVED = "approved"
    DISABLED = "disabled"

class NotificationChannel(str, PyEnum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"

class TransactionType(str, PyEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"

class TransactionStatus(str, PyEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"

class GoalStatus(str, PyEnum):
    ONGOING = "ongoing"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    REOPENED = "reopened"

class ProductType(str, PyEnum):
    UNIT_TRUST = "unit_trust"
    MONEY_MARKET_FUND = "money_market_fund"

class NotificationStatus(str, PyEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
