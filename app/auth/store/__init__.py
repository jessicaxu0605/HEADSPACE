"""Persistence contract for authentication and account sessions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.auth.models import (
    AuthenticatedSession,
    NewAccount,
    Session,
    UserWithPassword,
)


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
    ) -> NewAccount: ...

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


__all__ = ["AuthStore", "EmailAlreadyExistsError"]
