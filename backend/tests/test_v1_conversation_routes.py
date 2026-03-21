import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.local_storage import LocalDatabase
from app.local_storage.local_models import LocalMessage
from app.local_storage.request_scope import (
    LOCAL_DEVICE_SCOPE_HEADER,
    LOCAL_LEGACY_ACCOUNTS_HEADER,
    bind_request_local_scope,
    parse_legacy_account_ids_header,
    reset_request_local_scope,
)
from app.models import User
from app.routes import v1_api
from app.routes.auth_utils import get_current_user


class V1ConversationRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-conversation-1",
            email="conversation@example.com",
            username="conversation-user",
            name="Conversation User",
            plan="pro",
            is_active=True,
        )
        self.other_user = User(
            id="user-conversation-2",
            email="conversation-2@example.com",
            username="conversation-user-2",
            name="Conversation User 2",
            plan="pro",
            is_active=True,
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name)
        self.storage_dir_patcher = patch(
            "app.local_storage.local_db.get_storage_dir",
            side_effect=self._get_storage_dir,
        )
        self.storage_dir_patcher.start()

        app = FastAPI()

        @app.middleware("http")
        async def local_scope_middleware(request, call_next):
            tokens = bind_request_local_scope(
                request.headers.get(LOCAL_DEVICE_SCOPE_HEADER),
                parse_legacy_account_ids_header(request.headers.get(LOCAL_LEGACY_ACCOUNTS_HEADER)),
            )
            try:
                return await call_next(request)
            finally:
                reset_request_local_scope(tokens)

        app.include_router(v1_api.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[v1_api.get_db] = lambda: None
        app.dependency_overrides[v1_api.v1_rate_limit] = lambda: None

        self.app = app
        self.client = TestClient(app)
        self.device_headers = {
            LOCAL_DEVICE_SCOPE_HEADER: "device-test-1",
            LOCAL_LEGACY_ACCOUNTS_HEADER: f"{self.user.id},{self.other_user.id}",
        }

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()
        self.storage_dir_patcher.stop()
        self.temp_dir.cleanup()

    def _get_storage_dir(self, user_id: str) -> Path:
        path = self.storage_root / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_v1_conversation_routes_cover_crud_and_message_metadata(self) -> None:
        create_response = self.client.post(
            "/api/v1/conversations",
            json={"title": "Proxy audit", "workspace_id": None},
        )
        self.assertEqual(create_response.status_code, 200)
        create_body = create_response.json()
        self.assertEqual(create_body["title"], "Proxy audit")
        self.assertIsNone(create_body["summary"])
        conversation_id = create_body["id"]

        add_message_response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "assistant", "content": "Initial answer", "model": "gpt-4o"},
        )
        self.assertEqual(add_message_response.status_code, 200)
        message_body = add_message_response.json()
        message_id = message_body["id"]
        self.assertEqual(message_body["token_count"], 2)

        local_db = LocalDatabase(self.user.id)
        existing = local_db.get_message(message_id)
        self.assertIsNotNone(existing)
        enriched_message = LocalMessage(
            id=existing.id,
            conversation_id=existing.conversation_id,
            role=existing.role,
            content=existing.content,
            model=existing.model,
            token_count=existing.token_count,
            sources_json=json.dumps([
                {
                    "title": "Example Source",
                    "url": "https://example.com/source",
                    "snippet": "Evidence",
                }
            ]),
            embedding=existing.embedding,
            created_at=existing.created_at,
        )
        local_db.update_message(enriched_message)

        update_response = self.client.patch(
            f"/api/v1/conversations/{conversation_id}/messages/{message_id}",
            json={"content": "Updated answer", "model": "gpt-4o-mini"},
        )
        self.assertEqual(update_response.status_code, 200)
        update_body = update_response.json()
        self.assertEqual(update_body["content"], "Updated answer")
        self.assertEqual(update_body["model"], "gpt-4o-mini")
        self.assertEqual(update_body["sources"], ["https://example.com/source"])
        self.assertEqual(update_body["citations"][0]["title"], "Example Source")

        get_conversation_response = self.client.get(f"/api/v1/conversations/{conversation_id}")
        self.assertEqual(get_conversation_response.status_code, 200)
        conversation_body = get_conversation_response.json()
        self.assertEqual(conversation_body["total_messages"], 1)
        self.assertEqual(conversation_body["messages"][0]["sources"], ["https://example.com/source"])
        self.assertEqual(conversation_body["messages"][0]["citations"][0]["url"], "https://example.com/source")

        get_messages_response = self.client.get(f"/api/v1/conversations/{conversation_id}/messages")
        self.assertEqual(get_messages_response.status_code, 200)
        messages_body = get_messages_response.json()
        self.assertEqual(messages_body["total"], 1)
        self.assertEqual(messages_body["messages"][0]["sources"], ["https://example.com/source"])

        log_response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/logs",
            json={"task_name": "provider-audit", "content": "trace line 1"},
        )
        self.assertEqual(log_response.status_code, 200)
        self.assertTrue(log_response.json()["path"].endswith(".txt"))

        list_logs_response = self.client.get(f"/api/v1/conversations/{conversation_id}/logs")
        self.assertEqual(list_logs_response.status_code, 200)
        self.assertEqual(len(list_logs_response.json()["logs"]), 1)

        artifact_response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/artifacts",
            json={"filename": "audit.md", "content": "# Audit"},
        )
        self.assertEqual(artifact_response.status_code, 200)
        self.assertTrue(artifact_response.json()["path"].endswith("audit.md"))

        list_artifacts_response = self.client.get(f"/api/v1/conversations/{conversation_id}/artifacts")
        self.assertEqual(list_artifacts_response.status_code, 200)
        self.assertEqual(list_artifacts_response.json()["artifacts"], ["audit.md"])

        export_response = self.client.get(f"/api/v1/conversations/{conversation_id}/export")
        self.assertEqual(export_response.status_code, 200)
        export_body = export_response.json()
        self.assertEqual(export_body["title"], "Proxy audit")
        self.assertIn("Updated answer", export_body["markdown"])

        delete_response = self.client.delete(f"/api/v1/conversations/{conversation_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json(), {"ok": True})

        list_response = self.client.get("/api/v1/conversations")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["conversations"], [])

    def test_local_conversations_are_shared_across_accounts_on_same_machine(self) -> None:
        create_response = self.client.post(
            "/api/v1/conversations",
            json={"title": "Machine scoped", "workspace_id": None},
            headers=self.device_headers,
        )
        self.assertEqual(create_response.status_code, 200)
        conversation_id = create_response.json()["id"]

        add_message_response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "shared local message"},
            headers=self.device_headers,
        )
        self.assertEqual(add_message_response.status_code, 200)

        self.app.dependency_overrides[get_current_user] = lambda: self.other_user

        list_response = self.client.get("/api/v1/conversations", headers=self.device_headers)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["conversations"]), 1)
        self.assertEqual(list_response.json()["conversations"][0]["id"], conversation_id)

        get_response = self.client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers=self.device_headers,
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["messages"][0]["content"], "shared local message")


if __name__ == "__main__":
    unittest.main()
