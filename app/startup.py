import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text

from app.database.database import engine
from app.queue.celery_app import celery_app
from app.queue.redis import redis_client
from app.services.auth.firebase_auth.setup import init_firebase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("simlexx")
logger.setLevel(logging.INFO)

ALEMBIC_INI_PATH = Path(__file__).resolve().parent.parent / "alembic.ini"


def run_migrations() -> None:
    alembic_cfg = Config(str(ALEMBIC_INI_PATH))
    command.upgrade(alembic_cfg, "head")


def check_celery_broker() -> None:
    connection = celery_app.connection()
    try:
        connection.ensure_connection(max_retries=1, timeout=2)
    finally:
        connection.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Simlexx Wealth API...")

    logger.info("Applying database migrations...")
    await asyncio.to_thread(run_migrations)
    logger.info("Database migrations applied.")

    logger.info("Connecting to PostgreSQL...")
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Connected to PostgreSQL.")

    logger.info("Connecting to Redis...")
    await redis_client.ping()
    logger.info("Connected to Redis.")

    logger.info("Starting Firebase...")
    try:
        init_firebase()
        logger.info("Firebase started.")
    except Exception as exc:
        logger.error("Firebase failed to start: %s", exc)
        raise

    logger.info("Startup complete.")
    yield

    logger.info("Shutting down Simlexx Wealth API...")
    await redis_client.aclose()
    await engine.dispose()
    logger.info("Shutdown complete.")
