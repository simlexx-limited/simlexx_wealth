from functools import lru_cache

from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Tavily search
    TAVILY_SEARCH_API:str
    SEARCH_DEPTH:str = "basic"

    # Cloudflare S3 Bucket
    R2_ACCOUNT_ID:str
    R2_ACCESS_KEY_ID:str
    R2_SECRET_ACCESS_KEY:str
    R2_BUCKET_NAME:str
    R2_JURISDICTION:Optional[str] = None
    BUCKET_URL:str

    # Firebase
    PATH_TO_FIREBASE:str
    FIREBASE_WEB_API:str


    @field_validator("R2_JURISDICTION")
    @classmethod
    def blank_to_none(cls, value):
        if value == "":
            return None
        return value


@lru_cache
def get_settings()-> Settings:
    return Settings()
