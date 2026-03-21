import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.models import User
from app.routes import memory, v1_api
from app.routes.auth_utils import get_current_user


class LegacyChatRouteMountTests(unittest.TestCase):
    def test_legacy_chat_routes_are_not_mounted(self) -> None:
        paths = {route.path for route in main_app.routes}
        self.assertNotIn("/api/chat/completions", paths)
        self.assertNotIn("/api/chat/completions/rag", paths)
        self.assertNotIn("/api/chat/smart", paths)
        self.assertNotIn("/api/chat/conversations", paths)


class MemoryConversationDeprecationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-memory-legacy-1",
            email="memory-legacy@example.com",
            username="memory-legacy-user",
            name="Memory Legacy User",
            plan="pro",
            is_active=True,
        )

        app = FastAPI()
        app.include_router(memory.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[memory.get_db] = lambda: None
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_memory_conversation_create_returns_410_with_replacement(self) -> None:
        response = self.client.post(
            "/api/memory/conversations",
            json={"title": "Legacy", "workspace_id": None},
        )
        self.assertEqual(response.status_code, 410)
        body = response.json()
        self.assertEqual(body["detail"]["code"], "endpoint_removed")
        self.assertEqual(body["detail"]["replacement"], "/api/v1/conversations")

    def test_memory_conversation_export_returns_410_with_replacement(self) -> None:
        response = self.client.get("/api/memory/conversations/conv-1/export")
        self.assertEqual(response.status_code, 410)
        body = response.json()
        self.assertEqual(body["detail"]["replacement"], "/api/v1/conversations/conv-1/export")


class V1KeyStorageDeprecationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-v1-keys-legacy-1",
            email="v1-keys-legacy@example.com",
            username="v1-keys-legacy-user",
            name="V1 Keys Legacy User",
            plan="pro",
            is_active=True,
        )

        app = FastAPI()
        app.include_router(v1_api.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[v1_api.get_db] = lambda: None
        app.dependency_overrides[v1_api.v1_rate_limit] = lambda: None
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_v1_keys_routes_return_410(self) -> None:
        create_response = self.client.post("/api/v1/keys", json={"key": "sk-test"})
        self.assertEqual(create_response.status_code, 410)
        self.assertEqual(create_response.json()["detail"]["replacement"], "/api/v1/keys/validate")

        get_response = self.client.get("/api/v1/keys")
        self.assertEqual(get_response.status_code, 410)

        delete_response = self.client.delete("/api/v1/keys/key-1")
        self.assertEqual(delete_response.status_code, 410)


if __name__ == "__main__":
    unittest.main()
