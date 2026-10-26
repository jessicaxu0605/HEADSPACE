"""FastAPI routes and dependencies for authentication."""

from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.models import AuthenticatedSession, IssuedSession, NewAccount, User
from app.auth.passwords import PasswordManager
from app.auth.service import AuthService, InvalidCredentialsError
from app.auth.store import EmailAlreadyExistsError, PostgresAuthStore
from app.auth.tokens import InvalidTokenError, JwtTokenCodec


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=1024)
    birthday: date | None = None
    about: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("email must be a valid email address")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("about")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("email must be a valid email address")
        return value


class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str
    created_at: datetime
    birthday: date | None
    about: str | None

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            created_at=user.created_at,
            birthday=user.birthday,
            about=user.about,
        )


class CreateUserResponse(UserResponse):
    # The workspace signup created for the account, and the user's node in it,
    # so the client can open it as a just-created workspace.
    default_workspace_id: UUID
    self_node_id: UUID | None

    @classmethod
    def from_new_account(cls, account: NewAccount) -> "CreateUserResponse":
        return cls(
            **UserResponse.from_user(account.user).model_dump(),
            default_workspace_id=account.workspace.workspace.id,
            self_node_id=account.workspace.self_node_id,
        )


class SessionResponse(BaseModel):
    id: UUID
    user: UserResponse
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_authenticated(
        cls, authenticated: AuthenticatedSession
    ) -> "SessionResponse":
        return cls(
            id=authenticated.session.id,
            user=UserResponse.from_user(authenticated.user),
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
    "/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED
)
async def create_user(
    request: CreateUserRequest,
    service: AuthService = Depends(get_auth_service),
) -> CreateUserResponse:
    try:
        account = await service.create_user(
            email=request.email,
            name=request.name,
            password=request.password,
            birthday=request.birthday,
            about=request.about,
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CreateUserResponse.from_new_account(account)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    try:
        issued = await service.login(
            email=request.email,
            password=request.password,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return LoginResponse.from_issued(issued)


@router.get("/session", response_model=SessionResponse)
async def read_session(
    authenticated: AuthenticatedSession = Depends(get_current_session),
) -> SessionResponse:
    return SessionResponse.from_authenticated(authenticated)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: AuthService = Depends(get_auth_service),
) -> None:
    await service.logout(authenticated)
