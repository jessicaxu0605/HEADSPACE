"""Auth persistence contract and PostgreSQL implementation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from psycopg.errors import UniqueViolation

from app.auth.models import AuthenticatedSession, Session, User, UserWithPassword
from app.persistence.postgres.connection import get_async_connection
from app.workspaces.store import insert_workspace


# Every account starts with one workspace it owns, so workspace-scoped
# endpoints work immediately after signup.
DEFAULT_WORKSPACE_NAME = "Personal"


class EmailAlreadyExistsError(ValueError):
    pass


class AuthStore(Protocol):
    async def create_user(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        birthday: date | None,
        about: str | None,
    ) -> User: ...

    async def get_user_by_email(self, *, email: str) -> UserWithPassword | None: ...

    async def create_session(
        self,
        *,
        user_id: UUID,
        expires_at: datetime,
    ) -> Session: ...

    async def get_authenticated_session(
        self, *, session_id: UUID
    ) -> AuthenticatedSession | None: ...

    async def revoke_session(self, *, session_id: UUID, user_id: UUID) -> None: ...


def _user(row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        created_at=row["created_at"],
        birthday=row["birthday"],
        about=row["about"],
    )


def _session(row) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresAuthStore:
    async def create_user(
        self,
        *,
        email: str,
        name: str,
        password_hash: str,
        birthday: date | None,
        about: str | None,
    ) -> User:
        try:
            async with get_async_connection() as conn:
                async with conn.transaction():
                    row = await (
                        await conn.execute(
                            """
                            INSERT INTO users (
                                email, name, password_hash, display_name,
                                birthday, about
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING id, email, name, created_at,
                                      birthday, about
                            """,
                            (email, name, password_hash, name, birthday, about),
                        )
                    ).fetchone()
                    assert row is not None
                    user = _user(row)
                    await insert_workspace(
                        conn,
                        user=user,
                        name=DEFAULT_WORKSPACE_NAME,
                        create_self_node=True,
                        mark_last_used=True,
                    )
        except UniqueViolation as exc:
            raise EmailAlreadyExistsError("Email is already in use.") from exc
        return user

    async def get_user_by_email(self, *, email: str) -> UserWithPassword | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, email, name, password_hash, created_at,
                           birthday, about
                    FROM users
                    WHERE lower(email) = lower(%s) AND deleted_at IS NULL
                    """,
                    (email,),
                )
            ).fetchone()
        if row is None:
            return None
        return UserWithPassword(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            created_at=row["created_at"],
            birthday=row["birthday"],
            about=row["about"],
            password_hash=row["password_hash"],
        )

    async def create_session(
        self,
        *,
        user_id: UUID,
        expires_at: datetime,
    ) -> Session:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO auth_session (user_id, expires_at)
                    VALUES (%s, %s)
                    RETURNING id, user_id, created_at, expires_at, revoked_at
                    """,
                    (user_id, expires_at),
                )
            ).fetchone()
        assert row is not None
        return _session(row)

    async def get_authenticated_session(
        self, *, session_id: UUID
    ) -> AuthenticatedSession | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT
                        s.id AS session_id,
                        s.user_id AS session_user_id,
                        s.created_at AS session_created_at,
                        s.expires_at,
                        s.revoked_at,
                        u.id AS user_id,
                        u.email,
                        u.name,
                        u.created_at AS user_created_at,
                        u.birthday,
                        u.about
                    FROM auth_session AS s
                    JOIN users AS u
                      ON u.id = s.user_id AND u.deleted_at IS NULL
                    WHERE s.id = %s
                    """,
                    (session_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return AuthenticatedSession(
            user=User(
                id=row["user_id"],
                email=row["email"],
                name=row["name"],
                created_at=row["user_created_at"],
                birthday=row["birthday"],
                about=row["about"],
            ),
            session=Session(
                id=row["session_id"],
                user_id=row["session_user_id"],
                created_at=row["session_created_at"],
                expires_at=row["expires_at"],
                revoked_at=row["revoked_at"],
            ),
        )

    async def revoke_session(self, *, session_id: UUID, user_id: UUID) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                UPDATE auth_session
                SET revoked_at = COALESCE(revoked_at, now())
                WHERE id = %s AND user_id = %s
                """,
                (session_id, user_id),
            )
