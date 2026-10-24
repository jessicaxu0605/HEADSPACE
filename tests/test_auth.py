from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth.api import get_auth_service
from app.auth.models import (
    AuthenticatedSession,
    IssuedSession,
    Session,
    User,
    UserWithPassword,
)
from app.auth.passwords import PasswordManager
from app.auth.service import AuthService, InvalidCredentialsError
from app.auth.tokens import InvalidTokenError, JwtTokenCodec
from app.main import app


def _keys() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


class FakeAuthStore:
    def __init__(self) -> None:
        self.users_by_email: dict[str, UserWithPassword] = {}
        self.sessions: dict[UUID, Session] = {}
        self.touched: list[UUID] = []

    async def create_user(
        self, *, email, name, password_hash, birthday, about
    ):
        now = datetime.now(UTC)
        user = UserWithPassword(
            uuid4(),
            email,
            name,
            now,
            birthday,
            about,
            password_hash=password_hash,
        )
        self.users_by_email[email.casefold()] = user
        return User(
            user.id,
            user.email,
            user.name,
            user.created_at,
            user.birthday,
            user.about,
        )

    async def get_user_by_email(self, *, email):
        return self.users_by_email.get(email.casefold())

    async def get_user(self, *, user_id):
        for user in self.users_by_email.values():
            if user.id == user_id:
                return User(
                    user.id,
                    user.email,
                    user.name,
                    user.created_at,
                    user.birthday,
                    user.about,
                )
        return None

    async def create_session(self, *, user_id, expires_at):
        now = datetime.now(UTC)
        session = Session(uuid4(), user_id, now, expires_at)
        self.sessions[session.id] = session
        return session

    async def get_session(self, *, session_id):
        return self.sessions.get(session_id)

    async def touch_session(self, *, session_id):
        self.touched.append(session_id)

    async def revoke_session(self, *, session_id, user_id):
        session = self.sessions.get(session_id)
        if session is None or session.user_id != user_id:
            return
        self.sessions[session_id] = Session(
            **{
                **session.__dict__,
                "revoked_at": session.revoked_at or datetime.now(UTC),
            }
        )


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        private_key, public_key = _keys()
        self.codec = JwtTokenCodec(
            private_key=private_key,
            public_key=public_key,
            issuer="test-issuer",
            audience="test-audience",
        )
        self.passwords = PasswordManager()
        self.store = FakeAuthStore()
        self.service = AuthService(
            store=self.store, passwords=self.passwords, tokens=self.codec
        )

    async def test_creates_user_with_argon2_hash_and_never_stores_password(self):
        user = await self.service.create_user(
            email="jessica@example.com",
            name="Jessica Xu",
            password="correct horse battery",
            birthday=date(1995, 4, 12),
            about="Builds memory tools.",
        )

        stored = self.store.users_by_email["jessica@example.com"]
        self.assertEqual(user.name, "Jessica Xu")
        self.assertNotEqual(stored.password_hash, "correct horse battery")
        self.assertTrue(stored.password_hash.startswith("$argon2id$"))
        self.assertTrue(
            self.passwords.verify(stored.password_hash, "correct horse battery")
        )
        self.assertEqual(user.birthday, date(1995, 4, 12))
        self.assertEqual(user.about, "Builds memory tools.")

    async def test_login_issues_workspace_agnostic_rs256_token(self):
        user = await self.service.create_user(
            email="jessica@example.com",
            name="Jessica Xu",
            password="correct horse battery",
        )

        issued = await self.service.login(
            email="JESSICA@EXAMPLE.COM", password="correct horse battery"
        )
        claims = self.codec.decode(issued.access_token)

        self.assertEqual(claims.user_id, user.id)
        self.assertEqual(claims.session_id, issued.authenticated.session.id)
        header = jwt.get_unverified_header(issued.access_token)
        self.assertEqual(header["alg"], "RS256")
        payload = jwt.decode(issued.access_token, options={"verify_signature": False})
        self.assertNotIn("workspace_id", payload)

    async def test_authenticate_checks_persisted_session_and_claims(self):
        await self.service.create_user(
            email="jessica@example.com",
            name="Jessica Xu",
            password="correct horse battery",
        )
        issued = await self.service.login(
            email="jessica@example.com", password="correct horse battery"
        )

        authenticated = await self.service.authenticate(issued.access_token)

        self.assertEqual(authenticated.user.email, "jessica@example.com")
        self.assertEqual(self.store.touched, [authenticated.session.id])

        self.store.sessions[authenticated.session.id] = Session(
            **{
                **authenticated.session.__dict__,
                "revoked_at": datetime.now(UTC),
            }
        )
        with self.assertRaises(InvalidTokenError):
            await self.service.authenticate(issued.access_token)

    async def test_rejects_wrong_password_and_tampered_token(self):
        await self.service.create_user(
            email="jessica@example.com",
            name="Jessica Xu",
            password="correct horse battery",
        )
        with self.assertRaises(InvalidCredentialsError):
            await self.service.login(
                email="jessica@example.com", password="wrong password"
            )

        issued = await self.service.login(
            email="jessica@example.com", password="correct horse battery"
        )
        head, body, signature = issued.access_token.split(".")
        replacement = "A" if signature[-1] != "A" else "B"
        tampered = f"{head}.{body}.{signature[:-1]}{replacement}"
        with self.assertRaises(InvalidTokenError):
            self.codec.decode(tampered)

    async def test_logout_revokes_current_session(self):
        await self.service.create_user(
            email="jessica@example.com",
            name="Jessica Xu",
            password="correct horse battery",
        )
        issued = await self.service.login(
            email="jessica@example.com", password="correct horse battery"
        )

        await self.service.logout(issued.authenticated)

        self.assertIsNotNone(
            self.store.sessions[issued.authenticated.session.id].revoked_at
        )
        with self.assertRaises(InvalidTokenError):
            await self.service.authenticate(issued.access_token)


class FakeApiAuthService:
    def __init__(self) -> None:
        self.created = None
        self.login_request = None
        self.authenticated_token = None
        self.logged_out = None
        self.user = User(
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "jessica@example.com",
            "Jessica Xu",
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.session = Session(
            UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            self.user.id,
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        )

    async def create_user(self, **kwargs):
        self.created = kwargs
        return User(
            self.user.id,
            kwargs["email"],
            kwargs["name"],
            self.user.created_at,
            kwargs["birthday"],
            kwargs["about"],
        )

    async def login(self, **kwargs):
        self.login_request = kwargs
        return IssuedSession(
            AuthenticatedSession(self.user, self.session), "signed.jwt.token"
        )

    async def authenticate(self, token):
        self.authenticated_token = token
        return AuthenticatedSession(self.user, self.session)

    async def logout(self, authenticated):
        self.logged_out = authenticated


class AuthApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_create_user_endpoint_validates_and_normalizes_input(self):
        service = FakeApiAuthService()
        app.dependency_overrides[get_auth_service] = lambda: service
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/users",
            json={
                "email": " Jessica@Example.com ",
                "name": " Jessica Xu ",
                "password": "correct horse battery",
                "birthday": "1995-04-12",
                "about": " Builds memory tools. ",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["email"], "jessica@example.com")
        self.assertEqual(
            service.created,
            {
                "email": "jessica@example.com",
                "name": "Jessica Xu",
                "password": "correct horse battery",
                "birthday": date(1995, 4, 12),
                "about": "Builds memory tools.",
            },
        )
        self.assertEqual(response.json()["birthday"], "1995-04-12")
        self.assertNotIn("login", response.json())
        self.assertNotIn("gender", response.json())

        invalid_email = client.post(
            "/api/v1/auth/users",
            json={
                "email": "not-an-email",
                "name": "Name",
                "password": "correct horse battery",
            },
        )
        self.assertEqual(invalid_email.status_code, 422)

        legacy_login = client.post(
            "/api/v1/auth/users",
            json={
                "login": "person@example.com",
                "name": "Name",
                "password": "correct horse battery",
            },
        )
        self.assertEqual(legacy_login.status_code, 422)

        weak = client.post(
            "/api/v1/auth/users",
            json={
                "email": "valid@example.com",
                "name": "Name",
                "password": "too-short",
            },
        )
        self.assertEqual(weak.status_code, 422)

        gender = client.post(
            "/api/v1/auth/users",
            json={
                "email": "another@example.com",
                "name": "Another User",
                "password": "correct horse battery",
                "gender": "woman",
            },
        )
        self.assertEqual(gender.status_code, 422)

    def test_login_and_current_session_are_workspace_agnostic(self):
        service = FakeApiAuthService()
        app.dependency_overrides[get_auth_service] = lambda: service
        client = TestClient(app)

        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "Jessica@Example.com",
                "password": "correct horse battery",
            },
        )

        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["access_token"], "signed.jwt.token")
        self.assertNotIn("active_workspace_id", login.json()["session"])
        self.assertNotIn("workspace_id", service.login_request)
        self.assertEqual(service.login_request["email"], "jessica@example.com")

        current = client.get(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer signed.jwt.token"},
        )

        self.assertEqual(current.status_code, 200)
        self.assertEqual(
            current.json()["user"]["email"], "jessica@example.com"
        )
        self.assertNotIn("active_workspace_id", current.json())
        self.assertEqual(service.authenticated_token, "signed.jwt.token")

        workspace_login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "jessica@example.com",
                "password": "correct horse battery",
                "workspace_id": str(uuid4()),
            },
        )
        self.assertEqual(workspace_login.status_code, 422)

    def test_logout_revokes_authenticated_session(self):
        service = FakeApiAuthService()
        app.dependency_overrides[get_auth_service] = lambda: service
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer signed.jwt.token"},
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(service.logged_out.session.id, service.session.id)
        self.assertEqual(service.authenticated_token, "signed.jwt.token")

    def test_logout_requires_authentication(self):
        response = TestClient(app).post("/api/v1/auth/logout")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
