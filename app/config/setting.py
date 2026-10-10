from functools import lru_cache

from typing import Annotated, Optional

import pycountry
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.model.mod_enum import UserRole

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
        )

    # Database
    POSTGRES_USER:str
    POSTGRES_PASSWORD:str
    POSTGRES_DB:str
    DATABASE_URL:str

    # Redis
    REDIS_URL:str
    REDIS_MAX_CONNECTIONS:int = 20
    REDIS_SOCKET_TIMEOUT:int = 3
    MAX_WORKERS:int = 5

    # Celery
    CELERY_BROKER_URL:str
    CELERY_RESULT_BACKEND:str

    # Openrouter & ADK
    OPENROUTER_KEY:str
    LLM_MODEL:str
    LLM_ENABLED:bool = True
    ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS:bool = True
    LLM_MAX_RETRIES:int
    LLM_RETRY_DELAY:int
    APP_NAME:str

    # Tavily search
    TAVILY_SEARCH_API:str
    SEARCH_DEPTH:str = "basic"

    # Exchange rates (ExchangeRate-API)
    EXCHANGE_RATE_API_KEY:str

    # Cloudflare S3 Bucket
    R2_ACCOUNT_ID:str
    R2_ACCESS_KEY_ID:str
    R2_SECRET_ACCESS_KEY:str
    R2_BUCKET_NAME:str
    R2_JURISDICTION:Optional[str] = None

    # How long a signed link to an uploaded file stays valid (R2 allows at most 7 days)
    SIGNED_URL_EXPIRE_SECONDS:int = Field(default=900, ge=60, le=604800)

    # Firebase
    PATH_TO_FIREBASE:str
    FIREBASE_WEB_API:str

    # SMTP (email service)
    SMTP_HOST:str
    SMTP_PORT:int = 587
    SMTP_USERNAME:str
    SMTP_PASSWORD:str
    SMTP_USE_TLS:bool = True
    SMTP_FROM_EMAIL:str

    # JWT token
    SECRET_ACCESS_KEY:str
    ALGORITHM:str
    JWT_EXPIRE_MINUTES:int = 60

    # Shorter session for administrators (no MFA yet, so keep the window small)
    ADMIN_JWT_EXPIRE_MINUTES:int = 15

    # Login lockout: failed password attempts allowed per phone number / per IP
    # inside one window; hitting the limit locks that key until the window ends
    LOGIN_MAX_FAILED_ATTEMPTS:int = 5
    LOGIN_IP_MAX_FAILED_ATTEMPTS:int = 20
    LOGIN_LOCKOUT_SECONDS:int = 900

    # OTP
    OTP_TTL_SECONDS:int = 86400

    # Rate limiting
    OTP_ATTEMPT_LIMIT:int = 5
    OTP_ATTEMPT_WINDOW_SECONDS:int = 3600

    # Countries we serve (ISO 3166-1 alpha-2). In .env: SERVED_COUNTRIES=UG,RW
    SERVED_COUNTRIES:Annotated[list[str], NoDecode] = ["UG"]

    @field_validator("SERVED_COUNTRIES", mode="before")
    @classmethod
    def parse_served_countries(cls, value):
        if isinstance(value, str):
            value = value.split(",")
        codes = [c.strip().upper() for c in value if c.strip()]
        for code in codes:
            if pycountry.countries.get(alpha_2=code) is None:
                raise ValueError(f"'{code}' is not a valid ISO country code")
        return list(dict.fromkeys(codes))

    # Account types anyone may create by signing up themselves. Agents and
    # admins are created by a Simlexx admin, so
    # by default only clients can self-register. In .env: SELF_REGISTRATION_ROLES=User
    SELF_REGISTRATION_ROLES:Annotated[list[str], NoDecode] = ["User"]

    @field_validator("SELF_REGISTRATION_ROLES", mode="before")
    @classmethod
    def parse_self_registration_roles(cls, value):
        if isinstance(value, str):
            value = value.split(",")
        roles = [r.strip() for r in value if r.strip()]
        valid = {role.value for role in UserRole}
        for role in roles:
            if role == UserRole.ADMIN.value:
                raise ValueError("Admin accounts can't be created by self-registration")
            if role not in valid:
                raise ValueError(f"'{role}' is not a valid role (expected one of {sorted(valid)})")
        return list(dict.fromkeys(roles))

    @field_validator("R2_JURISDICTION")
    @classmethod
    def blank_to_none(cls, value):
        if value == "":
            return None
        return value

    # Names read by app/services/storage/upload_to_cloudflare.py, mapped onto
    # the R2_* settings above so there is a single source of truth in .env.
    @property
    def r2_access_key_id(self) -> str:
        return self.R2_ACCESS_KEY_ID

    @property
    def r2_secret_access_key(self) -> str:
        return self.R2_SECRET_ACCESS_KEY

    @property
    def r2_bucket_name(self) -> str:
        return self.R2_BUCKET_NAME

    @property
    def r2_endpoint_url(self) -> str:
        jurisdiction = f"{self.R2_JURISDICTION}." if self.R2_JURISDICTION else ""
        return f"https://{self.R2_ACCOUNT_ID}.{jurisdiction}r2.cloudflarestorage.com"


@lru_cache
def get_settings()-> Settings:
    return Settings()  # type: ignore[call-arg]  # fields are loaded from env/.env at runtime
