import unittest
from unittest.mock import AsyncMock, patch
import asyncio

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


class _FakeBinaryResponse:
    def __init__(self, content: bytes, status_code: int = 200, content_type: str = "video/mp4") -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.is_success = 200 <= status_code < 300
        self.content = content
        self.text = ""

    def json(self) -> dict:
        raise AssertionError("Binary response does not expose JSON payload")


class _FakeAsyncClient:
    last_post_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        del url, headers
        _FakeAsyncClient.last_post_json = json
        return _FakeJsonResponse({"data": []})


class _SequenceAsyncClient:
    post_payloads: list[dict | None] = []

    def __init__(self, responses: list[_FakeJsonResponse]) -> None:
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        del url, headers
        _SequenceAsyncClient.post_payloads.append(json)
        return self._responses.pop(0)


class _SequenceTaskClient:
    def __init__(self, responses: list[_FakeJsonResponse]) -> None:
        self._responses = list(responses)

    async def get(self, url: str, headers: dict | None = None, timeout: float | None = None):
        del url, headers, timeout
        return self._responses.pop(0)


class _MediaFetchAsyncClient:
    last_get_url: str | None = None
    last_get_headers: dict | None = None

    def __init__(self, response: _FakeBinaryResponse, *args, **kwargs) -> None:
        del args, kwargs
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: dict | None = None):
        _MediaFetchAsyncClient.last_get_url = url
        _MediaFetchAsyncClient.last_get_headers = headers or {}
        return self._response


class V1VideoRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-1",
            email="owner@example.com",
            username="owner",
            name="Owner",
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
            api_key="test-key",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            source="request",
            api_provider="alibaba",
        )

    def test_get_video_generation_task_normalizes_provider_payload(self) -> None:
        payload = {
            "output": {
                "task_id": "task-123",
                "task_status": "SUCCEEDED",
                "videos": [
                    {
                        "url": "https://cdn.example.com/final.mp4",
                    }
                ],
            }
        }

        with patch.object(v1_api, "_resolve_upstream_config", return_value=self.cfg), \
             patch.object(v1_api, "_build_upstream_auth_headers", return_value={"Authorization": "Bearer test-key"}), \
             patch.object(v1_api, "_touch_legacy_key_usage"), \
             patch.object(
                 v1_api,
                 "_fetch_video_generation_task_payload",
                 new=AsyncMock(return_value=(payload, False)),
             ) as mock_fetch:
            response = self.client.get("/api/v1/videos/generations/task-123")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["task_id"], "task-123")
        self.assertEqual(body["data"][0]["url"], "https://cdn.example.com/final.mp4")
        self.assertNotIn("task_status", body)
        mock_fetch.assert_awaited_once()

    def test_video_generation_retries_duration_variants_and_falls_back_to_omitting_duration(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://video.example.com",
            source="request",
            api_provider="openai",
        )
        _SequenceAsyncClient.post_payloads = []
        first_response = _FakeJsonResponse(
            {
                "code": "invalid_json",
                "message": "json: cannot unmarshal string into Go struct field TaskSubmitReq.duration of type int",
                "data": None,
            },
            status_code=400,
        )
        second_response = _FakeJsonResponse(
            {
                "code": "fail_to_fetch_task",
                "message": "{\"message\":\"json: cannot unmarshal number into Go struct field Sora2GenerationRequest.duration of type string\",\"type\":\"task_error\",\"code\":\"parse_request_failed\"}",
                "data": None,
            },
            status_code=400,
        )
        third_response = _FakeJsonResponse({"data": []})

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg), \
             patch.object(v1_api, "_build_upstream_auth_headers", return_value={"Authorization": "Bearer upstream-key"}), \
             patch.object(v1_api, "_touch_legacy_key_usage"), \
             patch.object(v1_api, "httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = _SequenceAsyncClient([first_response, second_response, third_response])
            response = self.client.post(
                "/api/v1/videos/generations",
                headers={
                    "X-API-Key": "upstream-key",
                    "X-API-Base-URL": "https://video.example.com",
                    "X-API-Provider": "openai",
                },
                json={
                    "prompt": "A glossy product spin shot",
                    "model": "sora-2",
                    "duration": "5",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(_SequenceAsyncClient.post_payloads), 3)
        self.assertEqual(_SequenceAsyncClient.post_payloads[0]["duration"], "5")
        self.assertEqual(_SequenceAsyncClient.post_payloads[1]["duration"], 5)
        self.assertNotIn("duration", _SequenceAsyncClient.post_payloads[2])

    def test_resolve_alibaba_tts_voice_falls_back_from_openai_presets(self) -> None:
        request = v1_api.V1AudioSpeechRequest(
            model="qwen3-tts-flash",
            input="Hello from PigTex",
            voice="alloy",
        )

        resolved_voice = v1_api._resolve_alibaba_tts_voice(request)

        self.assertEqual(resolved_voice, "Cherry")

    def test_resolve_alibaba_tts_model_keeps_requested_model(self) -> None:
        request = v1_api.V1AudioSpeechRequest(
            model="gpt-4o-mini-tts",
            input="Hello from PigTex",
        )

        resolved_model = v1_api._resolve_alibaba_tts_model(request)

        self.assertEqual(resolved_model, "gpt-4o-mini-tts")

    def test_video_generation_requires_explicit_model(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://video.example.com",
            source="request",
            api_provider="openai",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/videos/generations",
                headers={
                    "X-API-Key": "upstream-key",
                    "X-API-Base-URL": "https://video.example.com",
                    "X-API-Provider": "openai",
                },
                json={
                    "prompt": "A glossy product spin shot",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "model_required")
        self.assertEqual(body["detail"]["provider"], "openai")
        self.assertEqual(body["detail"]["operation"], "video_generation")

    def test_normalize_video_generation_response_reads_task_from_data_object(self) -> None:
        payload = {
            "data": {
                "taskId": "task-data-123",
                "status": "processing",
            }
        }

        normalized = v1_api._normalize_video_generation_response(payload)

        self.assertEqual(normalized["task_id"], "task-data-123")
        self.assertEqual(normalized["task_status"], "processing")
        self.assertEqual(normalized["data"], [])

    def test_normalize_video_generation_response_reads_urls_from_data_object(self) -> None:
        payload = {
            "data": {
                "video": {
                    "url": "https://cdn.example.com/ready.mp4"
                }
            }
        }

        normalized = v1_api._normalize_video_generation_response(payload)

        self.assertEqual(normalized["data"][0]["url"], "https://cdn.example.com/ready.mp4")

    def test_normalize_video_generation_response_for_client_absolutizes_relative_url_and_hides_completed_status(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://video.example.com",
            source="request",
            api_provider="openai",
        )
        payload = {
            "task_status": "completed",
            "data": {
                "video": {
                    "url": "/v1/videos/task_abc/content"
                }
            }
        }

        normalized = v1_api._normalize_video_generation_response_for_client(payload, cfg)

        self.assertEqual(normalized["data"][0]["url"], "https://video.example.com/v1/videos/task_abc/content")
        self.assertNotIn("task_status", normalized)

    def test_normalize_video_generation_response_for_client_exposes_failed_task_message(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://video.example.com",
            source="request",
            api_provider="openai",
        )
        payload = {
            "task_id": "task-failed-123",
            "task_status": "FAILED",
            "message": '{"message":"Prompt violates the provider safety policy.","type":"task_error","code":"safety_rejected"}',
            "data": [],
        }

        normalized = v1_api._normalize_video_generation_response_for_client(payload, cfg)

        self.assertEqual(normalized["task_status"], "FAILED")
        self.assertEqual(normalized["error_message"], "Prompt violates the provider safety policy.")

    def test_media_fetch_proxies_request_scoped_video_with_upstream_auth(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://api.texapi.dev",
            source="request",
            api_provider="openai",
        )
        _MediaFetchAsyncClient.last_get_url = None
        _MediaFetchAsyncClient.last_get_headers = None

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg), \
             patch.object(v1_api, "_build_upstream_auth_headers", return_value={"Authorization": "Bearer upstream-key"}), \
             patch.object(v1_api, "_touch_legacy_key_usage"), \
             patch.object(v1_api, "httpx") as mock_httpx:
            mock_httpx.AsyncClient.side_effect = lambda *args, **kwargs: _MediaFetchAsyncClient(
                _FakeBinaryResponse(b"video-bytes"),
                *args,
                **kwargs,
            )
            response = self.client.get(
                "/api/v1/media/fetch",
                headers={
                    "X-API-Key": "upstream-key",
                    "X-API-Base-URL": "https://api.texapi.dev",
                    "X-API-Provider": "openai",
                },
                params={"url": "https://api.texapi.dev/v1/videos/task_abc/content"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"video-bytes")
        self.assertEqual(_MediaFetchAsyncClient.last_get_url, "https://api.texapi.dev/v1/videos/task_abc/content")
        self.assertEqual(_MediaFetchAsyncClient.last_get_headers, {"Authorization": "Bearer upstream-key"})

    def test_media_fetch_rejects_private_network_urls(self) -> None:
        response = self.client.get(
            "/api/v1/media/fetch",
            params={"url": "http://127.0.0.1/private.mp4"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["error"], "invalid_media_url")

    def test_fetch_video_generation_task_payload_treats_transient_502_as_pending(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="upstream-key",
            base_url="https://video.example.com/v1",
            source="request",
            api_provider="openai",
        )
        client = _SequenceTaskClient([
            _FakeJsonResponse({}, status_code=502),
        ])

        with patch.object(
            v1_api,
            "_build_video_task_candidate_urls",
            return_value=["https://video.example.com/v1/videos/task-502"],
        ):
            result = asyncio.run(
                v1_api._fetch_video_generation_task_payload(
                    client,
                    cfg,
                    "task-502",
                    {"Authorization": "Bearer upstream-key"},
                )
            )

        self.assertEqual(
            result.payload,
            {
                "task_id": "task-502",
                "task_status": "PENDING",
                "data": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
