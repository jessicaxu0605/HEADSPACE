from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.authentication import get_current_session
from app.auth.models import AuthenticatedSession, Session, User
from app.api.main import app
from app.api.routes.workspaces import get_workspace_service
from app.workspaces.models import CreatedWorkspace, MemberWorkspace, Workspace
from app.workspaces.service import WorkspaceService
from app.workspaces.sql.postgres import PostgresWorkspaceStore


def _authenticated() -> AuthenticatedSession:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        email="jessica@example.com",
        name="Jessica Xu",
        created_at=now,
        birthday=date(1995, 4, 12),
        about="Builds memory tools.",
    )
    session = Session(uuid4(), user.id, now, now + timedelta(hours=24))
    return AuthenticatedSession(user, session)


def _workspace(name: str, created_by_user_id=None) -> Workspace:
    return Workspace(
        id=uuid4(),
        name=name,
        created_by_user_id=created_by_user_id or uuid4(),
        created_at=datetime.now(UTC),
    )


class FakeWorkspaceStore:
    def __init__(self, member_workspaces: list[MemberWorkspace] | None = None) -> None:
        self.call = None
        self.list_call = None
        self.mark_call = None
        self.rename_call = None
        self.member_workspaces = member_workspaces or []

    async def create_workspace(self, **kwargs):
        self.call = kwargs
        workspace = Workspace(
            id=uuid4(),
            name=kwargs["name"],
            created_by_user_id=kwargs["user"].id,
            created_at=datetime.now(UTC),
        )
        return CreatedWorkspace(
            workspace,
            "owner",
            uuid4() if kwargs["create_self_node"] else None,
        )

    async def list_member_workspaces(self, **kwargs):
        self.list_call = kwargs
        return self.member_workspaces

    async def mark_last_used(self, **kwargs):
        self.mark_call = kwargs
        return any(
            item.workspace.id == kwargs["workspace_id"]
            for item in self.member_workspaces
        )

    async def rename_workspace(self, **kwargs):
        self.rename_call = kwargs
        for item in self.member_workspaces:
            if item.workspace.id == kwargs["workspace_id"] and item.role == "owner":
                return replace(
                    item, workspace=replace(item.workspace, name=kwargs["name"])
                )
        return None


class WorkspaceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_workspace_and_requests_self_node(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        service = WorkspaceService(store=store)

        created = await service.create_workspace(
            authenticated=authenticated,
            name="Personal",
            create_self_node=True,
        )

        self.assertEqual(store.call["user"], authenticated.user)
        self.assertNotIn("session_id", store.call)
        self.assertTrue(store.call["create_self_node"])
        self.assertIsNotNone(created.self_node_id)

    async def test_lists_workspaces_for_the_session_user(self):
        authenticated = _authenticated()
        listed = [MemberWorkspace(_workspace("Personal"), "owner")]
        store = FakeWorkspaceStore(listed)
        service = WorkspaceService(store=store)

        workspaces = await service.list_workspaces(authenticated=authenticated)

        self.assertEqual(store.list_call, {"user_id": authenticated.user.id})
        self.assertEqual(workspaces, listed)


class WorkspaceApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_create_workspace_requires_auth_without_replacing_session(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        service = WorkspaceService(store=store)
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: service
        client = TestClient(app)

        response = client.post(
            "/api/v1/workspaces",
            json={"name": " Personal ", "create_self_node": True},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["workspace"]["name"], "Personal")
        self.assertEqual(body["workspace"]["role"], "owner")
        self.assertIsNotNone(body["self_node_id"])
        self.assertNotIn("access_token", body)
        self.assertNotIn("session", body)

    def test_lists_member_workspaces_with_each_membership_role(self):
        authenticated = _authenticated()
        owned = _workspace("Personal", authenticated.user.id)
        shared = _workspace("Family")
        store = FakeWorkspaceStore(
            [MemberWorkspace(owned, "owner"), MemberWorkspace(shared, "viewer")]
        )
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).get("/api/v1/workspaces")

        self.assertEqual(response.status_code, 200)
        workspaces = response.json()["workspaces"]
        self.assertEqual(
            [(item["id"], item["name"], item["role"]) for item in workspaces],
            [
                (str(owned.id), "Personal", "owner"),
                (str(shared.id), "Family", "viewer"),
            ],
        )
        self.assertEqual(
            workspaces[1]["created_by_user_id"], str(shared.created_by_user_id)
        )
        self.assertIsNone(response.json()["last_used_workspace_id"])
        # Without any memory edits, a workspace was last edited when created.
        self.assertEqual(workspaces[0]["last_edited_at"], workspaces[0]["created_at"])

    def test_lists_when_each_workspace_was_last_edited(self):
        authenticated = _authenticated()
        last_edited_at = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
        store = FakeWorkspaceStore(
            [
                MemberWorkspace(
                    _workspace("Personal"), "owner", last_edited_at=last_edited_at
                )
            ]
        )
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).get("/api/v1/workspaces")

        self.assertEqual(
            response.json()["workspaces"][0]["last_edited_at"], "2026-10-20T09:30:00Z"
        )

    def test_lists_most_recently_used_workspace(self):
        authenticated = _authenticated()
        now = datetime.now(UTC)
        older = MemberWorkspace(_workspace("Personal"), "owner", now - timedelta(days=1))
        newer = MemberWorkspace(_workspace("Family"), "viewer", now)
        unused = MemberWorkspace(_workspace("Work"), "editor")
        store = FakeWorkspaceStore([older, newer, unused])
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).get("/api/v1/workspaces")

        self.assertEqual(
            response.json()["last_used_workspace_id"], str(newer.workspace.id)
        )
        self.assertNotIn("last_used_at", response.json()["workspaces"][0])

    def test_marks_member_workspace_as_last_used(self):
        authenticated = _authenticated()
        workspace = _workspace("Family")
        store = FakeWorkspaceStore([MemberWorkspace(workspace, "viewer")])
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).put(f"/api/v1/workspaces/{workspace.id}/last-used")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            store.mark_call,
            {"workspace_id": workspace.id, "user_id": authenticated.user.id},
        )

    def test_rejects_marking_a_workspace_without_membership(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).put(f"/api/v1/workspaces/{uuid4()}/last-used")

        self.assertEqual(response.status_code, 403)

    def test_renames_owned_workspace(self):
        authenticated = _authenticated()
        workspace = _workspace("Personal", authenticated.user.id)
        store = FakeWorkspaceStore([MemberWorkspace(workspace, "owner")])
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).patch(
            f"/api/v1/workspaces/{workspace.id}", json={"name": " Journal "}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(workspace.id))
        self.assertEqual(body["name"], "Journal")
        self.assertEqual(body["role"], "owner")
        self.assertEqual(
            store.rename_call,
            {
                "workspace_id": workspace.id,
                "user_id": authenticated.user.id,
                "name": "Journal",
            },
        )

    def test_rejects_renaming_without_ownership(self):
        authenticated = _authenticated()
        workspace = _workspace("Family")
        store = FakeWorkspaceStore([MemberWorkspace(workspace, "editor")])
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).patch(
            f"/api/v1/workspaces/{workspace.id}", json={"name": "Mine now"}
        )

        self.assertEqual(response.status_code, 403)

    def test_rejects_blank_workspace_name_on_rename(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_service] = lambda: WorkspaceService(
            store=store
        )

        response = TestClient(app).patch(
            f"/api/v1/workspaces/{uuid4()}", json={"name": "   "}
        )

        self.assertEqual(response.status_code, 422)
        self.assertIsNone(store.rename_call)

    def test_rejects_unauthenticated_workspace_rename(self):
        response = TestClient(app).patch(
            f"/api/v1/workspaces/{uuid4()}", json={"name": "Journal"}
        )

        self.assertEqual(response.status_code, 401)

    def test_rejects_unauthenticated_last_used_update(self):
        response = TestClient(app).put(f"/api/v1/workspaces/{uuid4()}/last-used")

        self.assertEqual(response.status_code, 401)

    def test_rejects_unauthenticated_workspace_listing(self):
        response = TestClient(app).get("/api/v1/workspaces")

        self.assertEqual(response.status_code, 401)

    def test_rejects_unauthenticated_workspace_creation(self):
        response = TestClient(app).post(
            "/api/v1/workspaces", json={"name": "Personal"}
        )

        self.assertEqual(response.status_code, 401)


class PostgresWorkspaceStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_only_active_memberships_in_live_workspaces(self):
        user_id = uuid4()
        workspace = _workspace("Family")
        rows = [
            {
                "id": workspace.id,
                "name": workspace.name,
                "created_by_user_id": workspace.created_by_user_id,
                "created_at": workspace.created_at,
                "role": "editor",
                "last_used_at": None,
                "last_edited_at": workspace.created_at,
            }
        ]

        class Cursor:
            async def fetchall(self):
                return rows

        class Connection:
            def __init__(self):
                self.calls = []

            async def execute(self, query, params):
                self.calls.append((query, params))
                return Cursor()

        class ConnectionContext:
            def __init__(self, connection):
                self.connection = connection

            async def __aenter__(self):
                return self.connection

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        connection = Connection()
        with patch(
            "app.workspaces.sql.postgres.get_async_connection",
            return_value=ConnectionContext(connection),
        ):
            listed = await PostgresWorkspaceStore().list_member_workspaces(
                user_id=user_id
            )

        self.assertEqual(
            listed,
            [MemberWorkspace(workspace, "editor", last_edited_at=workspace.created_at)],
        )
        query, params = connection.calls[0]
        self.assertIn("max(n.updated_at)", query)
        self.assertIn("max(e.updated_at)", query)
        self.assertIn("wm.removed_at IS NULL", query)
        self.assertIn("w.deleted_at IS NULL", query)
        self.assertEqual(params, (user_id,))


if __name__ == "__main__":
    unittest.main()
