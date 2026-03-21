import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import oauth_state as oauth_state_module
from app.routes import auth as auth_module


class _FakeDbSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.refreshed: list[object] = []

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, value: object) -> None:
        self.refreshed.append(value)


class OAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _FakeDbSession()
        self.original_google_client_id = auth_module.settings.google_client_id
        self.original_google_client_secret = auth_module.settings.google_client_secret
        self.original_store = oauth_state_module._store

        auth_module.settings.google_client_id = "google-client"
        auth_module.settings.google_client_secret = "google-secret"
        oauth_state_module._store = oauth_state_module.InMemoryOAuthStateStore()

        app = FastAPI()
        app.include_router(auth_module.router, prefix="/api")

        def _get_db_override():
            yield self.db

        app.dependency_overrides[auth_module.get_db] = _get_db_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        auth_module.settings.google_client_id = self.original_google_client_id
        auth_module.settings.google_client_secret = self.original_google_client_secret
        oauth_state_module._store = self.original_store

    def test_oauth_state_survives_callback_and_status_polling(self) -> None:
        start_response = self.client.post("/api/auth/oauth/google/start")
        self.assertEqual(start_response.status_code, 200)

        start_payload = start_response.json()
        state = start_payload["state"]
        self.assertEqual(
            oauth_state_module.get_oauth_state(state)["status"],
            "pending",
        )

        with patch.object(
            auth_module,
            "_fetch_oauth_profile",
            new=AsyncMock(
                return_value={
                    "provider_account_id": "provider-user-1",
                    "email": "owner@example.com",
                    "name": "Owner",
                    "avatar_url": "https://example.com/avatar.png",
                }
            ),
        ), patch.object(
            auth_module,
            "_resolve_user_from_oauth",
            return_value=SimpleNamespace(id="user-1"),
        ), patch.object(
            auth_module,
            "_issue_user_access_token",
            return_value="token-from-oauth",
        ):
            callback_response = self.client.get(
                f"/api/auth/oauth/google/callback?state={state}&code=test-code"
            )

        self.assertEqual(callback_response.status_code, 200)
        self.assertIn("Đăng nhập thành công", callback_response.text)
        self.assertTrue(self.db.committed)

        status_response = self.client.get(f"/api/auth/oauth/google/status?state={state}")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(
            status_response.json(),
            {
                "status": "success",
                "access_token": "token-from-oauth",
                "token_type": "bearer",
            },
        )

        missing_response = self.client.get(f"/api/auth/oauth/google/status?state={state}")
        self.assertEqual(missing_response.status_code, 404)

    def test_oauth_start_accepts_get_for_legacy_clients(self) -> None:
        start_response = self.client.get("/api/auth/oauth/google/start")
        self.assertEqual(start_response.status_code, 200)
        payload = start_response.json()
        self.assertEqual(payload.get("provider"), "google")
        self.assertTrue(str(payload.get("auth_url") or "").startswith("https://accounts.google.com/"))


if __name__ == "__main__":
    unittest.main()
