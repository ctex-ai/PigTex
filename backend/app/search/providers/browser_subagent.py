"""
Headless browser fallback for dynamic pages that Jina/plain readers handle poorly.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from ..models import SearchResult

logger = logging.getLogger(__name__)


class BrowserSubagentProvider:
    """Render a page in headless Chromium and extract readable text."""

    def __init__(
        self,
        enabled: bool = True,
        timeout_seconds: float = 20.0,
        max_content_chars: int = 20_000,
    ) -> None:
        self.enabled = bool(enabled)
        self.timeout_seconds = max(5.0, float(timeout_seconds or 20.0))
        self.max_content_chars = max(2_000, int(max_content_chars or 20_000))

    async def read(self, target_url: str) -> SearchResult | None:
        normalized_url = (target_url or "").strip()
        if not self.enabled or not normalized_url:
            return None
        if not normalized_url.lower().startswith(("http://", "https://")):
            return None

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            logger.info("Browser subagent unavailable because Playwright is not installed: %s", exc)
            return None

        timeout_ms = int(self.timeout_seconds * 1000)
        parsed = urlparse(normalized_url)

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    viewport={"width": 1440, "height": 1200},
                )
                page = await context.new_page()
                await page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)

                try:
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 6000))
                except Exception:
                    pass

                await page.evaluate(
                    """() => {
                        window.scrollTo(0, Math.min(document.body.scrollHeight, 900));
                    }"""
                )
                await asyncio.sleep(0.35)

                payload = await page.evaluate(
                    """() => {
                        const candidates = [
                            document.querySelector('main'),
                            document.querySelector('article'),
                            document.querySelector('[role="main"]'),
                            document.body,
                        ].filter(Boolean);
                        const best = candidates[0] || document.body;
                        const text = best && typeof best.innerText === 'string' ? best.innerText : '';
                        return {
                            title: document.title || '',
                            text: text || '',
                        };
                    }"""
                )
                await context.close()
                await browser.close()
        except Exception as exc:
            logger.warning("Browser subagent failed url=%s error=%s", normalized_url, exc)
            return None

        if not isinstance(payload, dict):
            return None

        title = str(payload.get("title") or parsed.netloc or normalized_url).strip()
        content = str(payload.get("text") or "").strip()
        if not content:
            return None

        clipped_content = content[: self.max_content_chars].strip()
        snippet = clipped_content[:400].strip()
        return SearchResult(
            title=title[:140] or (parsed.netloc or normalized_url),
            url=normalized_url,
            snippet=snippet,
            full_content=clipped_content,
            source_provider="browser_subagent",
            domain=(parsed.netloc or "").replace("www.", ""),
        )
