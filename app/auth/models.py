"""Domain records used by the auth service and HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class User:
    id: UUID
    login: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class UserWithPassword(User):
    password_hash: str


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
    active_workspace_id: UUID | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    session: Session


@dataclass(frozen=True)
class IssuedSession:
    authenticated: AuthenticatedSession
    access_token: str
