"""Database connection helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row


DEFAULT_LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.local"


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
    connect_timeout = int(os.getenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "10"))
    with psycopg.connect(
        get_database_url(),
        row_factory=dict_row,
        connect_timeout=connect_timeout,
    ) as conn:
        yield conn


# TEMPORARY PROTOTYPE IDENTITY BOOTSTRAP.
#
# Remove these hardcoded identities as soon as authentication/workspace selection
# exists. They are not derived from a real user, tenant, request, or AWS resource
# and must not be used for a multi-user or production deployment.
TEMPORARY_PROTOTYPE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
TEMPORARY_PROTOTYPE_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000002")


def ensure_temporary_prototype_workspace() -> UUID:
    """TEMPORARY: create the unauthenticated single-user prototype workspace."""

    with get_connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO users (id, email, display_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    TEMPORARY_PROTOTYPE_USER_ID,
                    "prototype@headspace.invalid",
                    "Prototype User",
                ),
            )
            conn.execute(
                """
                INSERT INTO workspaces (id, name, created_by_user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    TEMPORARY_PROTOTYPE_WORKSPACE_ID,
                    "Headspace Prototype",
                    TEMPORARY_PROTOTYPE_USER_ID,
                ),
            )
    return TEMPORARY_PROTOTYPE_WORKSPACE_ID
