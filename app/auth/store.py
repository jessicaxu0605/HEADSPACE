"""Auth persistence contract and PostgreSQL implementation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from psycopg.errors import UniqueViolation

from app.auth.models import Session, User, UserWithPassword
from app.persistence.postgres.connection import get_async_connection


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

    async def get_user(self, *, user_id: UUID) -> User | None: ...

    async def create_session(
        self,
        *,
        user_id: UUID,
        expires_at: datetime,
    ) -> Session: ...

    async def get_session(self, *, session_id: UUID) -> Session | None: ...

    async def touch_session(self, *, session_id: UUID) -> None: ...

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
        except UniqueViolation as exc:
            raise EmailAlreadyExistsError("Email is already in use.") from exc
        assert row is not None
        return _user(row)

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

    async def get_user(self, *, user_id: UUID) -> User | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, email, name, created_at, birthday, about
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

    async def get_session(self, *, session_id: UUID) -> Session | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, user_id, created_at, expires_at, revoked_at
                    FROM auth_session
                    WHERE id = %s
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
