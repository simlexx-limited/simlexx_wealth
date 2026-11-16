#!/usr/bin/env python3

from contextlib import asynccontextmanager
from typing import AsyncGenerator, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.setting import get_settings

engine = create_async_engine(get_settings().DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def background_session() -> AsyncIterator[AsyncSession]:
    """
    A session for code that runs under its own asyncio.run() - Celery tasks
    and plain background threads. The shared engine's pooled connections
    belong to the API's event loop and can't be used from another loop, so
    this uses a throwaway NullPool engine instead.
    """
    background_engine = create_async_engine(get_settings().DATABASE_URL, echo=False, poolclass=NullPool)
    try:
        async with AsyncSession(background_engine, expire_on_commit=False) as session:
            yield session
    finally:
        await background_engine.dispose()
