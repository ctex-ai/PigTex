"""
Tavily provider for web search.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

import httpx

from ..models import SearchResult

logger = logging.getLogger(__name__)


class TavilySearchProvider:
    """Thin async wrapper around Tavily Search API."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.tavily.com/search",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.endpoint = (endpoint or "https://api.tavily.com/search").strip()
        self.timeout_seconds = max(3.0, float(timeout_seconds or 12.0))

    @property
    def is_enabled(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        topic: str = "general",
        max_results: int = 5,
        freshness_days: int | None = None,
    ) -> List[SearchResult]:
        if not self.is_enabled:
            return []

        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        payload = {
            "api_key": self.api_key,
            "query": normalized_query,
            "topic": "news" if topic == "news" else "general",
            "search_depth": "advanced",
            "max_results": max(1, min(10, int(max_results))),
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
        }
        if freshness_days and int(freshness_days) > 0:
            payload["days"] = max(1, min(365, int(freshness_days)))

        timeout = httpx.Timeout(
            connect=min(10.0, self.timeout_seconds),
            read=self.timeout_seconds,
            write=min(10.0, self.timeout_seconds),
            pool=min(10.0, self.timeout_seconds),
        )
        data = None
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        self.endpoint,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    data = response.json()
                    break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Tavily search failed query=%s attempt=%s error_type=%s error=%s",
                    normalized_query,
                    attempt,
                    type(e).__name__,
                    e,
                )
                if attempt < 2:
                    await asyncio.sleep(0.35 * attempt)

        if data is None:
            if last_error:
                logger.debug("Tavily search exhausted retries query=%s last_error=%r", normalized_query, last_error)
            return []

        if not isinstance(data, dict):
            return []

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            return []

        results: List[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or url or "Untitled").strip()
            snippet = str(
                item.get("content")
                or item.get("snippet")
                or item.get("raw_content")
                or ""
            ).strip()
            if not url or not snippet:
                continue
            try:
                relevance = float(item.get("score") or 0.0)
            except Exception:
                relevance = 0.0
            published_at = str(
                item.get("published_date")
                or item.get("published_at")
                or item.get("date")
                or ""
            ).strip() or None
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    relevance_score=relevance,
                    source_provider="tavily",
                    published_at=published_at,
                )
            )

        return results
