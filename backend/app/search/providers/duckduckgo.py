"""
DuckDuckGo provider for web search fallback.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List

try:
    from ddgs import DDGS
except Exception:  # pragma: no cover - optional dependency at runtime
    DDGS = None

from ..models import SearchResult

logger = logging.getLogger(__name__)


class DuckDuckGoSearchProvider:
    """Fallback search provider built on top of DDGS."""

    _VIETNAMESE_CHAR_RE = re.compile(
        r"[ăâđêôơưĂÂĐÊÔƠƯ"
        r"áàảãạấầẩẫậắằẳẵặ"
        r"éèẻẽẹếềểễệ"
        r"íìỉĩị"
        r"óòỏõọốồổỗộớờởỡợ"
        r"úùủũụứừửữự"
        r"ýỳỷỹỵ]"
    )
    _CJK_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
    _LEGACY_BACKENDS = {"html", "lite"}

    def __init__(
        self,
        enabled: bool = True,
        region: str = "us-en",
        safesearch: str = "moderate",
        backend: str = "html",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.enabled = bool(enabled)
        self.region = (region or "us-en").strip() or "us-en"
        self.safesearch = (safesearch or "moderate").strip() or "moderate"
        self.backend = (backend or "html").strip() or "html"
        self.timeout_seconds = max(1.0, float(timeout_seconds or 5.0))

    @property
    def is_enabled(self) -> bool:
        return self.enabled and DDGS is not None

    def _map_timelimit(self, freshness_days: int | None) -> str | None:
        if freshness_days is None or freshness_days <= 0:
            return None
        if freshness_days <= 1:
            return "d"
        if freshness_days <= 7:
            return "w"
        if freshness_days <= 31:
            return "m"
        return "y"

    def _resolve_region(self, query: str) -> str:
        normalized_query = (query or "").strip()
        if self._VIETNAMESE_CHAR_RE.search(normalized_query):
            return "vn-vi"
        if self._CJK_CHAR_RE.search(normalized_query):
            return "wt-wt"
        return self.region

    def _resolve_backend(self, topic: str) -> str:
        normalized_backend = (self.backend or "auto").strip().lower() or "auto"
        if normalized_backend in self._LEGACY_BACKENDS:
            return "auto"
        if (topic or "").strip().lower() == "news":
            return "auto"
        return normalized_backend

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

        timelimit = self._map_timelimit(freshness_days)
        region = self._resolve_region(normalized_query)
        normalized_topic = (topic or "general").strip().lower() or "general"
        backend = self._resolve_backend(normalized_topic)

        def _run() -> List[SearchResult]:
            outputs: List[SearchResult] = []
            ddgs_timeout_seconds = max(1, int(round(self.timeout_seconds)))
            with DDGS(timeout=ddgs_timeout_seconds) as ddgs:
                search_method = ddgs.news if normalized_topic == "news" else ddgs.text
                raw_results = list(
                    search_method(
                        normalized_query,
                        region=region,
                        safesearch=self.safesearch,
                        timelimit=timelimit,
                        backend=backend,
                        max_results=max(1, min(10, int(max_results))),
                    )
                )

            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("href") or item.get("url") or "").strip()
                title = str(item.get("title") or url or "Untitled").strip()
                snippet = str(item.get("body") or item.get("snippet") or "").strip()
                if not url or not snippet:
                    continue
                outputs.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        relevance_score=0.45,
                        source_provider="duckduckgo",
                        published_at=str(item.get("date") or item.get("published_at") or "").strip() or None,
                    )
                )
            return outputs

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=self.timeout_seconds + 1.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "DuckDuckGo search timed out query=%s timeout_seconds=%s",
                normalized_query,
                self.timeout_seconds,
            )
            return []
        except Exception as e:
            logger.warning("DuckDuckGo search failed query=%s error_type=%s error=%s", normalized_query, type(e).__name__, e)
            return []
