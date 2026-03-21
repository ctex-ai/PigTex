"""
Search broker to chain multiple providers with fallback.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from ..models import SearchResult


class SearchBroker:
    """Run providers in order and fallback when the earlier provider is weak or empty."""

    def __init__(self, providers: Sequence[Tuple[str, object]], min_results_to_stop: int = 3) -> None:
        self.providers = list(providers)
        self.min_results_to_stop = max(1, int(min_results_to_stop))

    @property
    def is_enabled(self) -> bool:
        for _, provider in self.providers:
            if bool(getattr(provider, "is_enabled", False)):
                return True
        return False

    async def search(
        self,
        query: str,
        topic: str = "general",
        max_results: int = 5,
        freshness_days: int | None = None,
    ) -> List[SearchResult]:
        aggregated: List[SearchResult] = []
        seen_urls: set[str] = set()

        for _, provider in self.providers:
            if not bool(getattr(provider, "is_enabled", False)):
                continue

            provider_results = await provider.search(
                query=query,
                topic=topic,
                max_results=max_results,
                freshness_days=freshness_days,
            )
            if not provider_results:
                continue

            for result in provider_results:
                url = (getattr(result, "url", "") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                aggregated.append(result)

            if len(aggregated) >= min(max_results, self.min_results_to_stop):
                break

        return aggregated
