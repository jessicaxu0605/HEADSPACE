"""Authenticated workspace HTTP endpoints."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.api import get_current_session
from app.auth.models import AuthenticatedSession
from app.workspaces.models import (
    CreatedWorkspace,
    MemberWorkspace,
    Workspace,
    WorkspaceRole,
)
from app.workspaces.service import WorkspaceService
from app.workspaces.store import PostgresWorkspaceStore


router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class WorkspaceNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CreateWorkspaceRequest(WorkspaceNameRequest):
    create_self_node: bool = False


class RenameWorkspaceRequest(WorkspaceNameRequest):
    pass


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_by_user_id: UUID
    role: WorkspaceRole
    created_at: datetime
    # The newest change to the workspace's memory nodes or edges.
    last_edited_at: datetime

    @classmethod
    def from_workspace(
        cls,
        workspace: Workspace,
        role: WorkspaceRole,
        last_edited_at: datetime | None = None,
    ) -> "WorkspaceResponse":
        return cls(
            id=workspace.id,
            name=workspace.name,
            created_by_user_id=workspace.created_by_user_id,
            role=role,
            created_at=workspace.created_at,
            last_edited_at=last_edited_at or workspace.created_at,
        )

    @classmethod
    def from_member_workspace(
        cls, member_workspace: MemberWorkspace
    ) -> "WorkspaceResponse":
        return cls.from_workspace(
            member_workspace.workspace,
            member_workspace.role,
            member_workspace.last_edited_at,
        )


class CreateWorkspaceResponse(BaseModel):
    workspace: WorkspaceResponse
    self_node_id: UUID | None

    @classmethod
    def from_created(cls, created: CreatedWorkspace) -> "CreateWorkspaceResponse":
        return cls(
            workspace=WorkspaceResponse.from_workspace(
                created.workspace, created.role
            ),
            self_node_id=created.self_node_id,
        )


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceResponse]
    # The listed workspace the caller opened most recently, if any.
    last_used_workspace_id: UUID | None


@lru_cache(maxsize=1)
def get_workspace_service() -> WorkspaceService:
    return WorkspaceService(store=PostgresWorkspaceStore())


@router.post(
    "", response_model=CreateWorkspaceResponse, status_code=status.HTTP_201_CREATED
)
async def create_workspace(
    request: CreateWorkspaceRequest,
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: WorkspaceService = Depends(get_workspace_service),
) -> CreateWorkspaceResponse:
    created = await service.create_workspace(
        authenticated=authenticated,
        name=request.name,
        create_self_node=request.create_self_node,
    )
    return CreateWorkspaceResponse.from_created(created)


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceListResponse:
    """List the workspaces the current user is an active member of."""

    member_workspaces = await service.list_workspaces(authenticated=authenticated)
    used = [item for item in member_workspaces if item.last_used_at is not None]
    last_used = max(used, key=lambda item: item.last_used_at, default=None)
    return WorkspaceListResponse(
        workspaces=[
            WorkspaceResponse.from_member_workspace(member_workspace)
            for member_workspace in member_workspaces
        ],
        last_used_workspace_id=last_used.workspace.id if last_used else None,
    )


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def rename_workspace(
    workspace_id: UUID,
    request: RenameWorkspaceRequest,
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceResponse:
    """Rename a workspace the current user owns."""

    renamed = await service.rename_workspace(
        authenticated=authenticated, workspace_id=workspace_id, name=request.name
    )
    if renamed is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace owner access required.",
        )
    return WorkspaceResponse.from_member_workspace(renamed)


@router.put(
    "/{workspace_id}/last-used", status_code=status.HTTP_204_NO_CONTENT
)
async def mark_workspace_last_used(
    workspace_id: UUID,
    authenticated: AuthenticatedSession = Depends(get_current_session),
    service: WorkspaceService = Depends(get_workspace_service),
) -> Response:
    """Remember this workspace as the one the current user opened last."""

    if not await service.mark_last_used(
        authenticated=authenticated, workspace_id=workspace_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
