from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth.api import get_current_session
from app.auth.models import AuthenticatedSession, Session, User
from app.api.dependencies import get_node_search_store
from app.api.main import app
from app.workspaces.dependencies import (
    get_workspace_membership_store,
    require_workspace_member,
    require_workspace_writer,
)
from app.workspaces.models import WorkspaceMembership
from app.workspaces.store import PostgresWorkspaceMembershipStore
from tests.helpers import TEST_WORKSPACE_ID


def _authenticated() -> AuthenticatedSession:
    now = datetime.now(UTC)
    user = User(uuid4(), "member@example.com", "Member", now)
    return AuthenticatedSession(
        user,
        Session(uuid4(), user.id, now, now + timedelta(hours=1)),
    )


class FakeMembershipStore:
    def __init__(self, membership: WorkspaceMembership | None) -> None:
        self.membership = membership
        self.call = None

    async def get_active_membership(self, **kwargs):
        self.call = kwargs
        return self.membership


class EmptySearchStore:
    async def search_nodes(self, **kwargs):
        return []


class WorkspaceAuthorizationDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_dependency_returns_active_membership(self) -> None:
        authenticated = _authenticated()
        membership = WorkspaceMembership(
            TEST_WORKSPACE_ID,
            authenticated.user.id,
            "viewer",
        )
        store = FakeMembershipStore(membership)

        result = await require_workspace_member(
            TEST_WORKSPACE_ID,
            authenticated,
            store,
        )

        self.assertEqual(result, membership)
        self.assertEqual(
            store.call,
            {
                "workspace_id": TEST_WORKSPACE_ID,
                "user_id": authenticated.user.id,
            },
        )

    async def test_member_dependency_rejects_missing_membership(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await require_workspace_member(
                TEST_WORKSPACE_ID,
                _authenticated(),
                FakeMembershipStore(None),
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail,
            "Active workspace membership required.",
        )

    async def test_writer_allows_owner_and_editor_but_rejects_viewer(self) -> None:
        user_id = uuid4()
        for role in ("owner", "editor"):
            membership = WorkspaceMembership(
                TEST_WORKSPACE_ID,
                user_id,
                role,
            )
            self.assertEqual(
                await require_workspace_writer(membership),
                membership,
            )

        viewer = WorkspaceMembership(
            TEST_WORKSPACE_ID,
            user_id,
            "viewer",
        )
        with self.assertRaises(HTTPException) as raised:
            await require_workspace_writer(viewer)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail,
            "Workspace write access required.",
        )


class PostgresWorkspaceMembershipStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_only_active_membership_in_active_workspace(self) -> None:
        workspace_id = uuid4()
        user_id = uuid4()

        class Cursor:
            async def fetchone(self):
                return {
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "role": "editor",
                }

        class Connection:
            def __init__(self):
                self.call = None

            async def execute(self, query, params):
                self.call = (query, params)
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
            "app.workspaces.store.get_async_connection",
            return_value=ConnectionContext(connection),
        ):
            membership = await PostgresWorkspaceMembershipStore().get_active_membership(
                workspace_id=workspace_id,
                user_id=user_id,
            )

        self.assertEqual(
            membership,
            WorkspaceMembership(workspace_id, user_id, "editor"),
        )
        query, params = connection.call
        self.assertIn("wm.removed_at IS NULL", query)
        self.assertIn("w.deleted_at IS NULL", query)
        self.assertEqual(params, (workspace_id, user_id))


class WorkspaceAuthorizationApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_scoped_endpoints_require_a_session(self) -> None:
        node_id = uuid4()
        edge_id = uuid4()
        run_id = uuid4()
        client = TestClient(app)
        workspace = {"workspace_id": str(TEST_WORKSPACE_ID)}
        requests = [
            client.post(
                "/api/v1/nodes/query",
                params=workspace,
                json={"query": "Alice"},
            ),
            client.get(f"/api/v1/nodes/{node_id}/graph", params=workspace),
            client.get(f"/api/v1/edges/{edge_id}", params=workspace),
            client.get("/api/v1/landing", params=workspace),
            client.post(
                "/api/v1/ingestions",
                params=workspace,
                json={"content": "Alice knows Bob.", "client_request_id": "auth"},
            ),
            client.get(f"/api/v1/ingestions/{run_id}", params=workspace),
            client.get(f"/api/v1/ingestions/{run_id}/graph", params=workspace),
        ]

        self.assertEqual(
            [response.status_code for response in requests],
            [401] * 7,
        )

    def test_authenticated_non_member_is_forbidden(self) -> None:
        app.dependency_overrides[get_current_session] = _authenticated
        app.dependency_overrides[get_workspace_membership_store] = lambda: (
            FakeMembershipStore(None)
        )
        app.dependency_overrides[get_node_search_store] = EmptySearchStore

        response = TestClient(app).post(
            "/api/v1/nodes/query",
            params={"workspace_id": str(TEST_WORKSPACE_ID)},
            json={"query": "Alice"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Active workspace membership required.",
        )

    def test_viewer_can_read_but_cannot_ingest(self) -> None:
        authenticated = _authenticated()
        membership = WorkspaceMembership(
            TEST_WORKSPACE_ID,
            authenticated.user.id,
            "viewer",
        )
        app.dependency_overrides[get_current_session] = lambda: authenticated
        app.dependency_overrides[get_workspace_membership_store] = lambda: (
            FakeMembershipStore(membership)
        )
        app.dependency_overrides[get_node_search_store] = EmptySearchStore
        client = TestClient(app)

        workspace = {"workspace_id": str(TEST_WORKSPACE_ID)}
        read = client.post(
            "/api/v1/nodes/query",
            params=workspace,
            json={"query": "Alice"},
        )
        write = client.post(
            "/api/v1/ingestions",
            params=workspace,
            json={"content": "Alice knows Bob.", "client_request_id": "viewer"},
        )

        self.assertEqual(read.status_code, 200)
        self.assertEqual(write.status_code, 403)
        self.assertEqual(
            write.json()["detail"],
            "Workspace write access required.",
        )

    def test_authenticated_request_must_provide_workspace_id(self) -> None:
        app.dependency_overrides[get_current_session] = _authenticated
        app.dependency_overrides[get_node_search_store] = EmptySearchStore

        response = TestClient(app).post(
            "/api/v1/nodes/query",
            json={"query": "Alice"},
        )

        self.assertEqual(response.status_code, 422)

    def test_health_remains_public(self) -> None:
        response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
