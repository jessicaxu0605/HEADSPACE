"""Database connection helpers."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import UUID

import psycopg
from dotenv import dotenv_values
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row


DEFAULT_LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.local"
_async_pool: AsyncConnectionPool | None = None


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not database_url:
        local_values = dotenv_values(DEFAULT_LOCAL_ENV_PATH)
        database_url = local_values.get("DATABASE_URL") or local_values.get(
            "NEON_DATABASE_URL"
        )
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL or NEON_DATABASE_URL.")
    return str(database_url)


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """Open an isolated synchronous connection for migrations and legacy writes."""

    connect_timeout = int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10"))
    with psycopg.connect(
        get_database_url(),
        row_factory=dict_row,
        connect_timeout=connect_timeout,
    ) as conn:
        yield conn


def get_async_pool() -> AsyncConnectionPool:
    """Return the process-wide pool used by asynchronous request handlers."""

    global _async_pool
    if _async_pool is None:
        connect_timeout = int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10"))
        min_size = int(os.getenv("DATABASE_POOL_MIN_SIZE", "1"))
        max_size = int(os.getenv("DATABASE_POOL_MAX_SIZE", "5"))
        _async_pool = AsyncConnectionPool(
            conninfo=get_database_url(),
            min_size=min_size,
            max_size=max_size,
            # Neon closes idle connections; validate on checkout and recycle
            # before the server drops them.
            check=AsyncConnectionPool.check_connection,
            max_idle=float(os.getenv("DATABASE_POOL_MAX_IDLE_SECONDS", "60")),
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": connect_timeout,
            },
            open=False,
        )
    return _async_pool


async def open_async_pool() -> None:
    """Open the request pool and verify its minimum connections are ready."""

    pool = get_async_pool()
    await pool.open()
    await pool.wait()


async def close_async_pool() -> None:
    """Close all pooled request connections."""

    global _async_pool
    if _async_pool is None:
        return
    pool = _async_pool
    _async_pool = None
    await pool.close()


@asynccontextmanager
async def get_async_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """Check out one connection from the application-lifetime async pool."""

    async with get_async_pool().connection() as conn:
        yield conn


# Requests without authenticated workspace context use this development workspace.
# It is provisioned once by the database migrations rather than on every request.
DEFAULT_DEVELOPMENT_WORKSPACE_ID = UUID(
    "00000000-0000-0000-0000-000000000002"
)
