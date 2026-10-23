"""Application service for accounts, login, and token authentication."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from app.auth.models import AuthenticatedSession, IssuedSession, User
from app.auth.passwords import PasswordManager
from app.auth.store import AuthStore
from app.auth.tokens import InvalidTokenError, JwtTokenCodec, session_expiry


class InvalidCredentialsError(ValueError):
    pass


class AuthService:
    def __init__(
        self,
        *,
        store: AuthStore,
        passwords: PasswordManager,
        tokens: JwtTokenCodec,
    ) -> None:
        self._store = store
        self._passwords = passwords
        self._tokens = tokens
        # Keep unknown and legacy-disabled accounts on the same expensive
        # verification path as valid accounts to reduce login enumeration.
        self._dummy_password_hash = passwords.hash("not-a-real-user-password")

    async def create_user(
        self,
        *,
        login: str,
        name: str,
        password: str,
        birthday: date | None = None,
        about: str | None = None,
    ) -> User:
        password_hash = self._passwords.hash(password)
        return await self._store.create_user(
            login=login,
            name=name,
            password_hash=password_hash,
            birthday=birthday,
            about=about,
        )

    async def login(
        self,
        *,
        login: str,
        password: str,
        workspace_id: UUID | None = None,
    ) -> IssuedSession:
        user = await self._store.get_user_by_login(login=login)
        candidate_hash = (
            user.password_hash
            if user is not None and user.password_hash.startswith("$argon2")
            else self._dummy_password_hash
        )
        password_matches = self._passwords.verify(candidate_hash, password)
        if user is None or not password_matches:
            raise InvalidCredentialsError("Invalid login or password.")
        session = await self._store.create_session(
            user_id=user.id,
            requested_workspace_id=workspace_id,
            expires_at=session_expiry(),
        )
        public_user = User(
            id=user.id,
            login=user.login,
            name=user.name,
            created_at=user.created_at,
            birthday=user.birthday,
            about=user.about,
        )
        authenticated = AuthenticatedSession(public_user, session)
        return IssuedSession(authenticated, self._tokens.encode(session))

    async def authenticate(self, token: str) -> AuthenticatedSession:
        claims = self._tokens.decode(token)
        session = await self._store.get_session(session_id=claims.session_id)
        now = datetime.now(UTC)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= now
            or session.user_id != claims.user_id
            or session.active_workspace_id != claims.active_workspace_id
        ):
            raise InvalidTokenError("Invalid or expired access token.")
        user = await self._store.get_user(user_id=session.user_id)
        if user is None:
            raise InvalidTokenError("Invalid or expired access token.")
        await self._store.touch_session(session_id=session.id)
        return AuthenticatedSession(user, session)
