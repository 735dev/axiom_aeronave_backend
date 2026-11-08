"""Async Postgres engine, session factory and per-request session lifecycle."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_basekit.aio.sqlalchemy.session import make_session_lifecycle
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _is_test_mode() -> bool:
    return os.getenv("RUNNING_TESTS") == "true" or "pytest" in (sys.argv[0] if sys.argv else "")


engine_kwargs: dict = {"future": True, "pool_pre_ping": True}
if _is_test_mode():
    # Each test runs in its own event loop; pooled connections cannot cross loops.
    engine_kwargs = {"future": True, "poolclass": NullPool}

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionFactory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)


async def _log_rollback(exc: Exception, session: AsyncSession) -> None:
    logger.debug("request session rolled back: %s", type(exc).__name__)


# One commit per request, one rollback on any error. Services never commit.
get_db = make_session_lifecycle(AsyncSessionFactory, on_error=_log_rollback)


async def close_db() -> None:
    await engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.utils.endpoint_registry import assert_routes_registered
    from app.utils.rate_limit import close_rate_limit_store

    assert_routes_registered(app)
    try:
        yield
    finally:
        await close_rate_limit_store()
        await close_db()
