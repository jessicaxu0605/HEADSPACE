"""Auth persistence contract and PostgreSQL implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from psycopg.errors import UniqueViolation

from app.auth.models import Session, User, UserWithPassword
from app.persistence.postgres.connection import get_async_connection


class LoginAlreadyExistsError(ValueError):
    pass


class AuthStore(Protocol):
    async def create_user(
        self, *, login: str, name: str, password_hash: str
    ) -> User: ...

    async def get_user_by_login(self, *, login: str) -> UserWithPassword | None: ...

    async def get_user(self, *, user_id: UUID) -> User | None: ...

    async def create_session(
        self,
        *,
        user_id: UUID,
        requested_workspace_id: UUID | None,
        expires_at: datetime,
    ) -> Session: ...

    async def get_session(self, *, session_id: UUID) -> Session | None: ...

    async def touch_session(self, *, session_id: UUID) -> None: ...


def _user(row) -> User:
    return User(row["id"], row["login"], row["name"], row["created_at"])


def _session(row) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        active_workspace_id=row["active_workspace_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresAuthStore:
    async def create_user(
        self, *, login: str, name: str, password_hash: str
    ) -> User:
        try:
            async with get_async_connection() as conn:
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO users (login, name, password_hash, display_name)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, login, name, created_at
                        """,
                        (login, name, password_hash, name),
                    )
                ).fetchone()
        except UniqueViolation as exc:
            raise LoginAlreadyExistsError("Login is already in use.") from exc
        assert row is not None
        return _user(row)

    async def get_user_by_login(self, *, login: str) -> UserWithPassword | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, login, name, password_hash, created_at
                    FROM users
                    WHERE lower(login) = lower(%s) AND deleted_at IS NULL
                    """,
                    (login,),
                )
            ).fetchone()
        if row is None:
            return None
        return UserWithPassword(
            id=row["id"],
            login=row["login"],
            name=row["name"],
            created_at=row["created_at"],
            password_hash=row["password_hash"],
        )

    async def get_user(self, *, user_id: UUID) -> User | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, login, name, created_at
                    FROM users
                    WHERE id = %s AND deleted_at IS NULL
                    """,
                    (user_id,),
                )
            ).fetchone()
        return _user(row) if row is not None else None

    async def create_session(
        self,
        *,
        user_id: UUID,
        requested_workspace_id: UUID | None,
        expires_at: datetime,
    ) -> Session:
        async with get_async_connection() as conn:
            async with conn.transaction():
                if requested_workspace_id is not None:
                    membership = await (
                        await conn.execute(
                            """
                            SELECT wm.workspace_id
                            FROM workspace_member wm
                            JOIN workspaces w ON w.id = wm.workspace_id
                            WHERE wm.user_id = %s AND wm.workspace_id = %s
                              AND wm.removed_at IS NULL
                              AND wm.joined_at IS NOT NULL
                              AND w.deleted_at IS NULL
                            """,
                            (user_id, requested_workspace_id),
                        )
                    ).fetchone()
                    if membership is None:
                        raise ValueError("User is not an active member of that workspace.")
                    workspace_id = requested_workspace_id
                else:
                    membership = await (
                        await conn.execute(
                            """
                            SELECT wm.workspace_id
                            FROM workspace_member wm
                            JOIN workspaces w ON w.id = wm.workspace_id
                            WHERE wm.user_id = %s AND wm.removed_at IS NULL
                              AND wm.joined_at IS NOT NULL AND w.deleted_at IS NULL
                            ORDER BY wm.joined_at, wm.workspace_id
                            LIMIT 1
                            """,
                            (user_id,),
                        )
                    ).fetchone()
                    workspace_id = membership["workspace_id"] if membership else None

                row = await (
                    await conn.execute(
                        """
                        INSERT INTO auth_session (user_id, active_workspace_id, expires_at)
                        VALUES (%s, %s, %s)
                        RETURNING id, user_id, active_workspace_id, created_at,
                                  expires_at, revoked_at
                        """,
                        (user_id, workspace_id, expires_at),
                    )
                ).fetchone()
        assert row is not None
        return _session(row)

    async def get_session(self, *, session_id: UUID) -> Session | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, user_id, active_workspace_id, created_at,
                           expires_at, revoked_at
                    FROM auth_session
                    WHERE id = %s
                      AND (
                        active_workspace_id IS NULL
                        OR EXISTS (
                          SELECT 1
                          FROM workspace_member wm
                          JOIN workspaces w ON w.id = wm.workspace_id
                          WHERE wm.workspace_id = auth_session.active_workspace_id
                            AND wm.user_id = auth_session.user_id
                            AND wm.joined_at IS NOT NULL
                            AND wm.removed_at IS NULL
                            AND w.deleted_at IS NULL
                        )
                      )
                    """,
                    (session_id,),
                )
            ).fetchone()
        return _session(row) if row is not None else None

    async def touch_session(self, *, session_id: UUID) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                "UPDATE auth_session SET last_used_at = now() WHERE id = %s",
                (session_id,),
            )
