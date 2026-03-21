import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import User
from app.routes import v1_api
from app.routes.auth_utils import get_current_user


class _FakeJsonResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.is_success = 200 <= status_code < 300
        self.content = b"{}"
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _QueuedAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: dict | None = None):
        del url, headers
        if not self._responses:
            raise AssertionError("No queued upstream response left")
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class V1ModelsListFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-models-1",
            email="models@example.com",
            username="models-user",
            name="Models User",
            plan="pro",
            is_active=True,
        )

        app = FastAPI()
        app.include_router(v1_api.router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[v1_api.get_db] = lambda: None
        app.dependency_overrides[v1_api.v1_rate_limit] = lambda: None

        self.client = TestClient(app)
        self.cfg = v1_api.ResolvedUpstreamConfig(
            api_key="sk-openai-test",
            base_url="https://api.openai.com/v1",
            source="request",
            api_provider="openai",
        )

    def test_models_route_does_not_reuse_stale_success_payload_after_upstream_503(self) -> None:
        queued_client = _QueuedAsyncClient([
            _FakeJsonResponse({"data": [{"id": "provider-model-a", "owned_by": "openai"}]}, 200),
            _FakeJsonResponse({"error": {"message": "provider unavailable"}}, 503),
            _FakeJsonResponse({"error": {"message": "provider unavailable"}}, 503),
            _FakeJsonResponse({"error": {"message": "provider unavailable"}}, 503),
        ])

        with patch.object(v1_api, "_resolve_upstream_config", return_value=self.cfg), \
             patch.object(v1_api.httpx, "AsyncClient", return_value=queued_client):
            first = self.client.get("/api/v1/models")
            second = self.client.get("/api/v1/models")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"][0]["id"], "provider-model-a")

        self.assertEqual(second.status_code, 503)
        body = second.json()
        self.assertEqual(body["detail"]["error"], "upstream_api_error")
        self.assertEqual(body["detail"]["status_code"], 503)

    def test_models_route_does_not_reuse_stale_success_payload_after_connection_error(self) -> None:
        queued_client = _QueuedAsyncClient([
            _FakeJsonResponse({"data": [{"id": "provider-model-a", "owned_by": "openai"}]}, 200),
            httpx.RequestError("network down"),
        ])

        with patch.object(v1_api, "_resolve_upstream_config", return_value=self.cfg), \
             patch.object(v1_api.httpx, "AsyncClient", return_value=queued_client):
            first = self.client.get("/api/v1/models")
            second = self.client.get("/api/v1/models")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"][0]["id"], "provider-model-a")

        self.assertEqual(second.status_code, 503)
        body = second.json()
        self.assertEqual(body["detail"]["error"], "api_connection_error")
        self.assertIn("Cannot connect to openai API.", body["detail"]["message"])


if __name__ == "__main__":
    unittest.main()
