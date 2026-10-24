"""Asymmetrically signed JWT access tokens."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
from dotenv import dotenv_values

from app.auth.models import Session


DEFAULT_LOCAL_ENV_PATH = Path(__file__).resolve().parents[2] / ".env.local"


class InvalidTokenError(ValueError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: UUID
    session_id: UUID
    expires_at: datetime


def _setting(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = dotenv_values(DEFAULT_LOCAL_ENV_PATH).get(name)
    return str(value) if value is not None else default


def _read_key(value_name: str, path_name: str) -> str:
    value = _setting(value_name)
    if value:
        return value.replace("\\n", "\n")
    path = _setting(path_name)
    if path:
        key_path = Path(path)
        if not key_path.is_absolute():
            key_path = DEFAULT_LOCAL_ENV_PATH.parent / key_path
        return key_path.read_text(encoding="utf-8")
    raise RuntimeError(f"Set {value_name} or {path_name}.")


class JwtTokenCodec:
    algorithm = "RS256"

    def __init__(
        self,
        *,
        private_key: str,
        public_key: str,
        issuer: str = "headspace-api",
        audience: str = "headspace-app",
    ) -> None:
        self._private_key = private_key
        self._public_key = public_key
        self._issuer = issuer
        self._audience = audience

    @classmethod
    def from_environment(cls) -> "JwtTokenCodec":
        return cls(
            private_key=_read_key(
                "AUTH_JWT_PRIVATE_KEY", "AUTH_JWT_PRIVATE_KEY_FILE"
            ),
            public_key=_read_key("AUTH_JWT_PUBLIC_KEY", "AUTH_JWT_PUBLIC_KEY_FILE"),
            issuer=_setting("AUTH_JWT_ISSUER", "headspace-api") or "headspace-api",
            audience=_setting("AUTH_JWT_AUDIENCE", "headspace-app")
            or "headspace-app",
        )

    def encode(self, session: Session) -> str:
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(session.user_id),
            "sid": str(session.id),
            "iat": session.created_at,
            "exp": session.expires_at,
        }
        return jwt.encode(payload, self._private_key, algorithm=self.algorithm)

    def decode(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=[self.algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "sid"]},
            )
            expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=UTC)
            return TokenClaims(
                user_id=UUID(payload["sub"]),
                session_id=UUID(payload["sid"]),
                expires_at=expires_at,
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError("Invalid or expired access token.") from exc


def session_expiry(*, now: datetime | None = None) -> datetime:
    hours = int(_setting("AUTH_SESSION_TTL_HOURS", "24") or "24")
    return (now or datetime.now(UTC)) + timedelta(hours=hours)
