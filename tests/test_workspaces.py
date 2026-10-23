from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.auth.api import get_current_session
from app.auth.models import AuthenticatedSession, Session, User
from app.main import app
from app.workspaces.api import get_workspace_service
from app.workspaces.models import CreatedWorkspace, Workspace
from app.workspaces.service import WorkspaceService


def _authenticated() -> AuthenticatedSession:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        login="jessica",
        name="Jessica Xu",
        created_at=now,
        birthday=date(1995, 4, 12),
        gender="woman",
        about="Builds memory tools.",
    )
    session = Session(uuid4(), user.id, None, now, now + timedelta(hours=24))
    return AuthenticatedSession(user, session)


class FakeWorkspaceStore:
    def __init__(self) -> None:
        self.call = None

    async def create_workspace(self, **kwargs):
        self.call = kwargs
        workspace = Workspace(
            id=uuid4(),
            name=kwargs["name"],
            created_by_user_id=kwargs["user"].id,
            created_at=datetime.now(UTC),
        )
        updated_session = Session(
            id=kwargs["session_id"],
            user_id=kwargs["user"].id,
            active_workspace_id=workspace.id,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        return CreatedWorkspace(
            workspace,
            uuid4() if kwargs["create_self_node"] else None,
            updated_session,
        )


class FakeTokenCodec:
    def __init__(self) -> None:
        self.session = None

    def encode(self, session):
        self.session = session
        return "new.workspace.token"


class WorkspaceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_workspace_activates_it_and_requests_self_node(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        tokens = FakeTokenCodec()
        service = WorkspaceService(store=store, tokens=tokens)

        issued = await service.create_workspace(
            authenticated=authenticated,
            name="Personal",
            create_self_node=True,
        )

        self.assertEqual(store.call["user"], authenticated.user)
        self.assertEqual(store.call["session_id"], authenticated.session.id)
        self.assertTrue(store.call["create_self_node"])
        self.assertIsNotNone(issued.created.self_node_id)
        self.assertEqual(
            issued.created.session.active_workspace_id, issued.created.workspace.id
        )
        self.assertEqual(tokens.session, issued.created.session)
        self.assertEqual(issued.access_token, "new.workspace.token")


class WorkspaceApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_create_workspace_requires_auth_and_returns_replacement_token(self):
        authenticated = _authenticated()
        store = FakeWorkspaceStore()
        service = WorkspaceService(store=store, tokens=FakeTokenCodec())
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
        self.assertEqual(body["access_token"], "new.workspace.token")
        self.assertEqual(
            body["session"]["active_workspace_id"], body["workspace"]["id"]
        )

    def test_rejects_unauthenticated_workspace_creation(self):
        response = TestClient(app).post(
            "/api/v1/workspaces", json={"name": "Personal"}
        )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
