import asyncio
import unittest
from unittest.mock import patch

from app.memory.fact_extractor import FactExtractor
from app.upstream_request import UpstreamRequestConfig


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    last_post_url: str | None = None
    last_post_headers: dict | None = None
    last_post_json: dict | None = None

    def __init__(self, response: _FakeResponse, *args, **kwargs) -> None:
        del args, kwargs
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        _FakeAsyncClient.last_post_url = url
        _FakeAsyncClient.last_post_headers = headers
        _FakeAsyncClient.last_post_json = json
        return self._response


class FactExtractorTests(unittest.TestCase):
    def test_extract_with_ai_returns_empty_without_request_upstream_config(self) -> None:
        extractor = FactExtractor(user_id="user-facts-1")

        result = asyncio.run(
            extractor.extract_with_ai(
                messages=["My name is PigTex"],
                existing_facts=[],
            )
        )

        self.assertEqual(result, [])

    def test_extract_with_ai_uses_request_scoped_openai_credentials(self) -> None:
        extractor = FactExtractor(user_id="user-facts-1")
        upstream_config = UpstreamRequestConfig(
            api_key="sk-request-123",
            base_url="https://api.openai.com/v1",
            api_provider="openai",
        )
        response = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"subject":"User","predicate":"name","object":"PigTex",'
                                '"category":"personal","confidence":0.91,"scope":"system"}]'
                            )
                        }
                    }
                ]
            }
        )

        with patch("app.memory.fact_extractor.httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
            facts = asyncio.run(
                extractor.extract_with_ai(
                    messages=["My name is PigTex"],
                    existing_facts=[],
                    upstream_config=upstream_config,
                    model_hint="gpt-4o-mini",
                )
            )

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].predicate, "name")
        self.assertEqual(facts[0].object, "PigTex")
        self.assertEqual(_FakeAsyncClient.last_post_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(
            _FakeAsyncClient.last_post_headers,
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-request-123",
            },
        )
        self.assertEqual(_FakeAsyncClient.last_post_json["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
