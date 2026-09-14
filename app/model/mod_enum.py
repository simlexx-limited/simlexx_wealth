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

class UserRole(str, PyEnum):
    CLIENT = "User"
    AGENT = "Agent"
    BROKER = "Broker"
    FUNDMANAGER = "FundManager"

class UserAccountStatus(str, PyEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    APPROVED = "approved"

class AccountStatus(str, PyEnum):
    ACTIVE = "Active"
    DISABLE = "Disable"
    REACTIVE = "Reactived"

class OtpPurpose(str, PyEnum):
    PHONE_VERIFICATION = "phone_verification"
    EMAIL_VERIFICATION = "email_verification"
    FORGOT_PASSWORD = "forgot_password"
    MFA = "mfa"
    EDIT_SENSITIVE_INFO = "edit_sensitive_info"

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
    READ = "read"
    NOT_READ = "not_read"

class CurrencyType(str, PyEnum):
    DZD = "DZD"
    AOA = "AOA"
    XOF = "XOF"
    BWP = "BWP"
    BIF = "BIF"
    CVE = "CVE"
    XAF = "XAF"
    KMF = "KMF"
    CDF = "CDF"
    DJF = "DJF"
    EGP = "EGP"
    ERN = "ERN"
    SZL = "SZL"
    ETB = "ETB"
    GMD = "GMD"
    GHS = "GHS"
    GNF = "GNF"
    KES = "KES"
    LSL = "LSL"
    LRD = "LRD"
    LYD = "LYD"
    MGA = "MGA"
    MWK = "MWK"
    MRU = "MRU"
    MUR = "MUR"
    MAD = "MAD"
    MZN = "MZN"
    NAD = "NAD"
    NGN = "NGN"
    RWF = "RWF"
    STN = "STN"
    SCR = "SCR"
    SLE = "SLE"
    SOS = "SOS"
    ZAR = "ZAR"
    SSP = "SSP"
    SDG = "SDG"
    TZS = "TZS"
    TND = "TND"
    UGX = "UGX"
    ZMW = "ZMW"
    ZWG = "ZWG"
    EUR = "EUR"
    CAD = "CAD"
    USD = "USD"
