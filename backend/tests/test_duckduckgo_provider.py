import asyncio
import unittest
from unittest.mock import patch

from app.search.providers import duckduckgo as duckduckgo_module
from app.search.providers.duckduckgo import DuckDuckGoSearchProvider


class _FakeDDGS:
    last_instance = None

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")
        self.calls = []
        type(self).last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def text(self, query, **kwargs):
        self.calls.append(("text", query, kwargs))
        return [
            {
                "href": "https://example.com/text",
                "title": "Text result",
                "body": "Text snippet",
            }
        ]

    def news(self, query, **kwargs):
        self.calls.append(("news", query, kwargs))
        return [
            {
                "url": "https://example.com/news",
                "title": "News result",
                "body": "News snippet",
                "date": "2026-03-23",
            }
        ]


class DuckDuckGoSearchProviderTests(unittest.TestCase):
    def test_news_topic_uses_news_endpoint_and_normalizes_legacy_backend(self) -> None:
        with patch.object(duckduckgo_module, "DDGS", _FakeDDGS):
            provider = DuckDuckGoSearchProvider(enabled=True, backend="html", timeout_seconds=7.2)
            results = asyncio.run(
                provider.search(
                    "giá xăng hôm nay",
                    topic="news",
                    max_results=3,
                    freshness_days=1,
                )
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].published_at, "2026-03-23")
        self.assertIsNotNone(_FakeDDGS.last_instance)
        self.assertEqual(_FakeDDGS.last_instance.timeout, 7)
        method, query, kwargs = _FakeDDGS.last_instance.calls[0]
        self.assertEqual(method, "news")
        self.assertEqual(query, "giá xăng hôm nay")
        self.assertEqual(kwargs.get("backend"), "auto")

    def test_general_topic_keeps_supported_backend_and_uses_text_endpoint(self) -> None:
        with patch.object(duckduckgo_module, "DDGS", _FakeDDGS):
            provider = DuckDuckGoSearchProvider(enabled=True, backend="duckduckgo", timeout_seconds=5)
            results = asyncio.run(provider.search("cursor bug", topic="general", max_results=3))

        self.assertEqual(len(results), 1)
        self.assertIsNotNone(_FakeDDGS.last_instance)
        method, query, kwargs = _FakeDDGS.last_instance.calls[0]
        self.assertEqual(method, "text")
        self.assertEqual(query, "cursor bug")
        self.assertEqual(kwargs.get("backend"), "duckduckgo")


if __name__ == "__main__":
    unittest.main()
