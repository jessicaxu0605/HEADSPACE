"""Domain records used by the auth service and HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.workspaces.models import CreatedWorkspace


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    name: str
    created_at: datetime
    birthday: date | None = None
    about: str | None = None


@dataclass(frozen=True)
class NewAccount:
    """A just-created user and the default workspace signup made for them."""

    user: User
    workspace: CreatedWorkspace


@dataclass(frozen=True, kw_only=True)
class UserWithPassword(User):
    password_hash: str


@dataclass(frozen=True)
class Session:
    id: UUID
    user_id: UUID
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
