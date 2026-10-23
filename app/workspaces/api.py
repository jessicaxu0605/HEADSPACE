"""Authenticated workspace HTTP endpoints."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.api import SessionResponse, get_current_session
from app.auth.models import AuthenticatedSession, User
from app.auth.tokens import JwtTokenCodec
from app.workspaces.models import IssuedWorkspace, Workspace
from app.workspaces.service import WorkspaceService
from app.workspaces.store import PostgresWorkspaceStore, StaleSessionError


router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    create_self_node: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_by_user_id: UUID
    role: str = "owner"
    created_at: datetime

    @classmethod
    def from_workspace(cls, workspace: Workspace) -> "WorkspaceResponse":
        return cls(
            id=workspace.id,
            name=workspace.name,
            created_by_user_id=workspace.created_by_user_id,
            created_at=workspace.created_at,
        )


class CreateWorkspaceResponse(BaseModel):
    workspace: WorkspaceResponse
    self_node_id: UUID | None
    access_token: str
    token_type: str = "bearer"
    session: SessionResponse

    @classmethod
    def from_issued(
        cls, issued: IssuedWorkspace, user: User
    ) -> "CreateWorkspaceResponse":
        authenticated = AuthenticatedSession(user, issued.created.session)
        return cls(
            workspace=WorkspaceResponse.from_workspace(issued.created.workspace),
            self_node_id=issued.created.self_node_id,
            access_token=issued.access_token,
            session=SessionResponse.from_authenticated(authenticated),
        )


@lru_cache(maxsize=1)
def get_workspace_service() -> WorkspaceService:
    return WorkspaceService(
        store=PostgresWorkspaceStore(), tokens=JwtTokenCodec.from_environment()
    )


@router.post(
    "", response_model=CreateWorkspaceResponse, status_code=status.HTTP_201_CREATED
)
async def create_workspace(
    request: CreateWorkspaceRequest,
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: WorkspaceService = Depends(get_workspace_service),
) -> CreateWorkspaceResponse:
    try:
        issued = await service.create_workspace(
            authenticated=authenticated,
            name=request.name,
            create_self_node=request.create_self_node,
        )
    except StaleSessionError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return CreateWorkspaceResponse.from_issued(issued, authenticated.user)
