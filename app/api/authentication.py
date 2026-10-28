"""FastAPI dependencies for bearer-token authentication."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import AuthenticatedSession
from app.auth.passwords import PasswordManager
from app.auth.service import AuthService
from app.auth.store.postgres import PostgresAuthStore
from app.auth.tokens import InvalidTokenError, JwtTokenCodec


bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService(
        store=PostgresAuthStore(),
        passwords=PasswordManager(),
        tokens=JwtTokenCodec.from_environment(),
    )


async def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


async def get_current_session(
    token: str = Depends(require_bearer_token),
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedSession:
    try:
        return await service.authenticate(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
