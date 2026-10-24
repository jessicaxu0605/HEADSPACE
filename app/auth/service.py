"""Application service for accounts, login, and token authentication."""

from __future__ import annotations

from datetime import UTC, date, datetime

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
        email: str,
        name: str,
        password: str,
        birthday: date | None = None,
        about: str | None = None,
    ) -> User:
        password_hash = self._passwords.hash(password)
        return await self._store.create_user(
            email=email,
            name=name,
            password_hash=password_hash,
            birthday=birthday,
            about=about,
        )

    async def login(
        self,
        *,
        email: str,
        password: str,
    ) -> IssuedSession:
        user = await self._store.get_user_by_email(email=email)
        candidate_hash = (
            user.password_hash
            if user is not None and user.password_hash.startswith("$argon2")
            else self._dummy_password_hash
        )
        password_matches = self._passwords.verify(candidate_hash, password)
        if user is None or not password_matches:
            raise InvalidCredentialsError("Invalid email or password.")
        session = await self._store.create_session(
            user_id=user.id,
            expires_at=session_expiry(),
        )
        public_user = User(
            id=user.id,
            email=user.email,
            name=user.name,
            created_at=user.created_at,
            birthday=user.birthday,
            about=user.about,
        )
        authenticated = AuthenticatedSession(public_user, session)
        return IssuedSession(authenticated, self._tokens.encode(session))

    async def authenticate(self, token: str) -> AuthenticatedSession:
        claims = self._tokens.decode(token)
        authenticated = await self._store.get_authenticated_session(
            session_id=claims.session_id
        )
        now = datetime.now(UTC)
        if (
            authenticated is None
            or authenticated.session.revoked_at is not None
            or authenticated.session.expires_at <= now
            or authenticated.session.user_id != claims.user_id
        ):
            raise InvalidTokenError("Invalid or expired access token.")
        # TODO: If session/device management or idle expiry is added, update
        # last_used_at on a throttled cadence instead of writing on every request.
        return authenticated

    async def logout(self, authenticated: AuthenticatedSession) -> None:
        await self._store.revoke_session(
            session_id=authenticated.session.id,
            user_id=authenticated.user.id,
        )
