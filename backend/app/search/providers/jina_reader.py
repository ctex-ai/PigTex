"""
Jina Reader provider for deep-reading article pages.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class JinaReaderProvider:
    """Fetches markdown/plaintext page content via Jina Reader."""

    def __init__(
        self,
        endpoint: str = "https://r.jina.ai/",
        timeout_seconds: float = 15.0,
    ) -> None:
        normalized_endpoint = (endpoint or "https://r.jina.ai/").strip()
        if not normalized_endpoint.endswith("/"):
            normalized_endpoint = f"{normalized_endpoint}/"
        self.endpoint = normalized_endpoint
        self.timeout_seconds = max(3.0, float(timeout_seconds or 15.0))

    def _build_url(self, target_url: str) -> str:
        # Keep URL delimiters readable while safely escaping spaces/unicode.
        encoded_target = quote(target_url, safe=":/?&=#%+-._~")
        return f"{self.endpoint}{encoded_target}"

    async def read(self, target_url: str) -> str:
        normalized_url = (target_url or "").strip()
        if not normalized_url:
            return ""
        if not normalized_url.lower().startswith(("http://", "https://")):
            return ""

        reader_url = self._build_url(normalized_url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    reader_url,
                    headers={"Accept": "text/plain, text/markdown, application/json"},
                )
                response.raise_for_status()
                return response.text.strip()
        except Exception as e:
            logger.warning("Jina reader failed url=%s error=%s", normalized_url, e)
            return ""

