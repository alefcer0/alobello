import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from app.config import settings
from app.constants.db import (
    DB_COMMAND_TIMEOUT_SECONDS,
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    ERROR_DB_POOL_NOT_INITIALIZED,
    LOG_DB_POOL_CLOSED,
    LOG_DB_POOL_INITIALIZED,
)

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
        command_timeout=DB_COMMAND_TIMEOUT_SECONDS,
    )
    logger.info(LOG_DB_POOL_INITIALIZED)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info(LOG_DB_POOL_CLOSED)


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(ERROR_DB_POOL_NOT_INITIALIZED)
    return _pool


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
