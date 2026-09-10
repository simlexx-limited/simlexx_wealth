import asyncio
import logging

from fastapi import APIRouter
from sqlalchemy import text

from app.database.database import engine
from app.queue.redis import redis_client
from app.startup import check_celery_broker
from app.services.firebase_auth.setup import check_firebase

logger = logging.getLogger("simlexx")

router = APIRouter()

@router.get("/")
async def welcome() -> dict:
    return {
        "status":True,
        "message":"Welcome to Simlexx Investment Platform"
    }

@router.get("/health")
async def health() -> dict:
    components: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components["database"] = "healthy"
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        components["database"] = "failed"

    try:
        await redis_client.ping()
        components["cache"] = "healthy"
    except Exception as exc:
        logger.error("Cache health check failed: %s", exc)
        components["cache"] = "failed"

    try:
        await asyncio.to_thread(check_celery_broker)
        components["background"] = "healthy"
    except Exception as exc:
        logger.error("Background health check failed: %s", exc)
        components["background"] = "failed"

    try:
        await asyncio.to_thread(check_firebase)
        components["firebase"] = "healthy"
    except Exception as exc:
        logger.error("Firebase health check failed: %s", exc)
        components["firebase"] = "failed"

    overall = "running" if all(value == "healthy" for value in components.values()) else "failed"
    return {"status": overall, "components": components}
