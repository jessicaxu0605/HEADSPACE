"""FastAPI routes and dependencies for authentication."""

from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.models import AuthenticatedSession, IssuedSession, User
from app.auth.passwords import PasswordManager
from app.auth.service import AuthService, InvalidCredentialsError
from app.auth.store import LoginAlreadyExistsError, PostgresAuthStore
from app.auth.tokens import InvalidTokenError, JwtTokenCodec


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=1024)
    birthday: date | None = None
    gender: str | None = Field(default=None, max_length=100)
    about: str | None = Field(default=None, max_length=2000)

    @field_validator("login")
    @classmethod
    def validate_login(cls, value: str) -> str:
        value = value.strip()
        if not LOGIN_PATTERN.fullmatch(value):
            raise ValueError(
                "login may contain letters, numbers, dots, hyphens, and underscores"
            )
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("gender", "about")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    workspace_id: UUID | None = None

    @field_validator("login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("login must not be blank")
        return value


class UserResponse(BaseModel):
    id: UUID
    login: str
    name: str
    created_at: datetime
    birthday: date | None
    gender: str | None
    about: str | None

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            login=user.login,
            name=user.name,
            created_at=user.created_at,
            birthday=user.birthday,
            gender=user.gender,
            about=user.about,
        )


class SessionResponse(BaseModel):
    id: UUID
    user: UserResponse
    active_workspace_id: UUID | None
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_authenticated(
        cls, authenticated: AuthenticatedSession
    ) -> "SessionResponse":
        return cls(
            id=authenticated.session.id,
            user=UserResponse.from_user(authenticated.user),
            active_workspace_id=authenticated.session.active_workspace_id,
            created_at=authenticated.session.created_at,
            expires_at=authenticated.session.expires_at,
        )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    session: SessionResponse

    @classmethod
    def from_issued(cls, issued: IssuedSession) -> "LoginResponse":
        return cls(
            access_token=issued.access_token,
            session=SessionResponse.from_authenticated(issued.authenticated),
        )


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


@router.post(
    "/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def create_user(
    request: CreateUserRequest,
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        user = await service.create_user(
            login=request.login,
            name=request.name,
            password=request.password,
            birthday=request.birthday,
            gender=request.gender,
            about=request.about,
        )
    except LoginAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UserResponse.from_user(user)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    try:
        issued = await service.login(
            login=request.login,
            password=request.password,
            workspace_id=request.workspace_id,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return LoginResponse.from_issued(issued)


@router.get("/session", response_model=SessionResponse)
async def read_session(
    authenticated: AuthenticatedSession = Depends(get_current_session),
) -> SessionResponse:
    return SessionResponse.from_authenticated(authenticated)
