import unittest
from unittest.mock import AsyncMock, patch

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


class _CaptureAsyncClient:
    last_url = None
    last_json = None
    last_headers = None

    def __init__(self, response: _FakeJsonResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        _CaptureAsyncClient.last_url = url
        _CaptureAsyncClient.last_json = json
        _CaptureAsyncClient.last_headers = headers
        return self._response


class V1MediaProviderRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-media-1",
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

    def test_normalize_upstream_models_payload_uses_shared_keyword_classifier_for_any_transport(self) -> None:
        payload = {
            "data": [
                {
                    "id": "gemini-3.1-flash-image-preview",
                    "type": "image",
                    "owned_by": "gateway",
                }
            ]
        }

        normalized = v1_api._normalize_upstream_models_payload("openai", payload)
        model = normalized["data"][0]

        self.assertEqual(model["transport"], "openai")
        self.assertIn("image_generation", model["capabilities"])

    def test_normalize_upstream_models_payload_classifies_t2i_asr_r2v_keywords(self) -> None:
        payload = {
            "data": [
                {
                    "id": "acme-t2i-pro",
                    "owned_by": "gateway",
                },
                {
                    "id": "acme-asr-voice",
                    "owned_by": "gateway",
                },
                {
                    "id": "acme-r2v-seedream-sora",
                    "owned_by": "gateway",
                },
            ]
        }

        normalized = v1_api._normalize_upstream_models_payload("openai", payload)
        models_by_id = {item["id"]: item for item in normalized["data"]}

        self.assertEqual(models_by_id["acme-t2i-pro"]["type"], "image")
        self.assertIn("image_generation", models_by_id["acme-t2i-pro"]["capabilities"])
        self.assertIn("image_edit", models_by_id["acme-t2i-pro"]["capabilities"])

        self.assertEqual(models_by_id["acme-asr-voice"]["type"], "audio")
        self.assertNotIn("audio_speech", models_by_id["acme-asr-voice"]["capabilities"])

        self.assertEqual(models_by_id["acme-r2v-seedream-sora"]["type"], "video")
        self.assertIn("video_generation", models_by_id["acme-r2v-seedream-sora"]["capabilities"])

    def test_normalize_upstream_models_payload_preserves_provider_flags(self) -> None:
        payload = {
            "data": [
                {
                    "id": "acme-best-model",
                    "owned_by": "gateway",
                    "recommendation_flag": {
                        "label": "Best",
                        "tone": "accent",
                    },
                    "status_flag": {
                        "label": "Stopped",
                        "tone": "danger",
                        "disabled": True,
                    },
                }
            ]
        }

        normalized = v1_api._normalize_upstream_models_payload("openai", payload)
        model = normalized["data"][0]

        self.assertEqual(model["recommendation_flag"], {
            "label": "Best",
            "tone": "accent",
        })
        self.assertEqual(model["status_flag"], {
            "label": "Stopped",
            "tone": "danger",
            "disabled": True,
        })

    def test_chat_completions_requires_explicit_model(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="sk-openai-test",
            base_url="https://api.openai.com/v1",
            source="request",
            api_provider="openai",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/chat/completions",
                headers={
                    "X-API-Key": "sk-openai-test",
                    "X-API-Base-URL": "https://api.openai.com/v1",
                    "X-API-Provider": "openai",
                },
                json={
                    "messages": [
                        {"role": "user", "content": "Hello from PigTex"}
                    ],
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "model_required")
        self.assertEqual(body["detail"]["provider"], "openai")
        self.assertEqual(body["detail"]["operation"], "chat_completions")

    def test_image_generation_uses_requested_gemini_model_without_fallback(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="AIza-test",
            base_url="https://generativelanguage.googleapis.com",
            source="request",
            api_provider="gemini",
        )
        gemini_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "aGVsbG8=",
                                }
                            },
                            {
                                "text": "Revised prompt",
                            },
                        ]
                    }
                }
            ]
        }

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg), \
             patch.object(v1_api, "_touch_legacy_key_usage"), \
             patch.object(v1_api, "_persist_image_response_data", new=AsyncMock(return_value=[
                 {
                     "serve_url": "/api/images/serve/user-media-1/generated.png",
                     "url": "/api/images/serve/user-media-1/generated.png",
                     "mime_type": "image/png",
                 }
             ])), \
             patch.object(v1_api, "httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = _CaptureAsyncClient(_FakeJsonResponse(gemini_payload))
            response = self.client.post(
                "/api/v1/images/generations",
                headers={
                    "X-API-Key": "AIza-test",
                    "X-API-Base-URL": "https://generativelanguage.googleapis.com",
                    "X-API-Provider": "gemini",
                },
                json={
                    "prompt": "Create a lakeside scene at sunrise",
                    "model": "gemini-provider-exact-image-model",
                    "size": "1024x1024",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"][0]["serve_url"], "/api/images/serve/user-media-1/generated.png")
        self.assertTrue(
            _CaptureAsyncClient.last_url.endswith(
                "/v1beta/models/gemini-provider-exact-image-model:generateContent"
            )
        )
        self.assertEqual(
            _CaptureAsyncClient.last_json["generationConfig"]["responseModalities"],
            ["IMAGE"],
        )
        self.assertEqual(
            _CaptureAsyncClient.last_json["generationConfig"]["imageConfig"]["aspectRatio"],
            "1:1",
        )

    def test_image_generation_requires_explicit_gemini_model(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="AIza-test",
            base_url="https://generativelanguage.googleapis.com",
            source="request",
            api_provider="gemini",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/images/generations",
                headers={
                    "X-API-Key": "AIza-test",
                    "X-API-Base-URL": "https://generativelanguage.googleapis.com",
                    "X-API-Provider": "gemini",
                },
                json={
                    "prompt": "Create a lakeside scene at sunrise",
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "model_required")
        self.assertEqual(body["detail"]["provider"], "gemini")
        self.assertEqual(body["detail"]["operation"], "image_generation")

    def test_audio_speech_is_disabled(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="AIza-test",
            base_url="https://generativelanguage.googleapis.com",
            source="request",
            api_provider="gemini",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/audio/speech",
                headers={
                    "X-API-Key": "AIza-test",
                    "X-API-Base-URL": "https://generativelanguage.googleapis.com",
                    "X-API-Provider": "gemini",
                },
                json={
                    "model": "gemini-provider-exact-tts-model",
                    "input": "Hello from PigTex",
                },
            )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "voice_feature_disabled")
        self.assertEqual(body["detail"]["operation"], "audio_speech")

    def test_audio_speech_returns_disabled_even_when_model_blank(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="sk-openai-test",
            base_url="https://api.openai.com/v1",
            source="request",
            api_provider="openai",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/audio/speech",
                headers={
                    "X-API-Key": "sk-openai-test",
                    "X-API-Base-URL": "https://api.openai.com/v1",
                    "X-API-Provider": "openai",
                },
                json={
                    "model": "   ",
                    "input": "Hello from PigTex",
                },
            )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "voice_feature_disabled")
        self.assertEqual(body["detail"]["operation"], "audio_speech")

    def test_audio_speech_returns_disabled_for_anthropic_transport(self) -> None:
        cfg = v1_api.ResolvedUpstreamConfig(
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            source="request",
            api_provider="anthropic",
        )

        with patch.object(v1_api, "_resolve_upstream_config", return_value=cfg):
            response = self.client.post(
                "/api/v1/audio/speech",
                headers={
                    "X-API-Key": "sk-ant-test",
                    "X-API-Base-URL": "https://api.anthropic.com",
                    "X-API-Provider": "anthropic",
                },
                json={
                    "model": "claude-sonnet-4-5",
                    "input": "Hello from PigTex",
                },
            )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "voice_feature_disabled")
        self.assertEqual(body["detail"]["operation"], "audio_speech")

    def test_audio_transcriptions_are_disabled(self) -> None:
        response = self.client.post(
            "/api/v1/audio/transcriptions",
            files={
                "file": ("sample.wav", b"fake-audio", "audio/wav"),
            },
            data={
                "model": "gpt-4o-mini-transcribe",
            },
        )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "voice_feature_disabled")
        self.assertEqual(body["detail"]["operation"], "audio_transcription")

    def test_audio_translations_are_disabled(self) -> None:
        response = self.client.post(
            "/api/v1/audio/translations",
            files={
                "file": ("sample.wav", b"fake-audio", "audio/wav"),
            },
            data={
                "model": "gpt-4o-mini-transcribe",
            },
        )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["detail"]["error"], "voice_feature_disabled")
        self.assertEqual(body["detail"]["operation"], "audio_translation")


if __name__ == "__main__":
    unittest.main()
