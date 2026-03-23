"""
Coordinator for PigTex web search pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timezone
from time import perf_counter
from typing import Deque, Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urlparse

from ..config import Settings
from .extractor import (
    build_claim_verification,
    compute_result_score,
    extract_facts_and_citations,
    enrich_and_rank_results,
)
from .models import (
    SearchContext,
    SearchIntent,
    SearchMode,
    SearchQuery,
    SearchResult,
)
from .providers.broker import SearchBroker
from .providers.browser_subagent import BrowserSubagentProvider
from .providers.duckduckgo import DuckDuckGoSearchProvider
from .providers.github_reader import GitHubReaderProvider
from .providers.jina_reader import JinaReaderProvider
from .providers.tavily import TavilySearchProvider

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as redis_async
    REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency runtime guard
    redis_async = None
    REDIS_AVAILABLE = False


class SearchQueryTimeoutError(TimeoutError):
    """Raised when a single search query exceeds its timeout budget."""


class SearchCoordinator:
    """
    End-to-end web-search flow:
    intent routing -> query planning -> Tavily search -> optional deep read ->
    extraction into prompt-ready facts + citations -> optional claim verification.
    """

    URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
    ISO_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
    SLASH_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b")

    REALTIME_HINTS = (
        "latest",
        "today",
        "news",
        "current",
        "recent",
        "update",
        "price",
        "weather",
        "score",
        "moi nhat",
        "hôm nay",
        "tin tuc",
        "giá",
        "gia",
        "bao nhiêu",
        "bao nhieu",
        "how much",
        "cost",
        "pricing",
        "phi",
        "phí",
        "tỷ giá",
        "ty gia",
    )
    FACTUAL_HINTS = (
        "fact check",
        "verify",
        "is it true",
        "official",
        "source",
        "citation",
        "đúng không",
        "xác minh",
        "kiem tra",
        "kiểm tra",
    )
    RESEARCH_HINTS = (
        "compare",
        "comparison",
        "vs",
        "benchmark",
        "pros and cons",
        "alternatives",
        "review",
        "research",
        "so sánh",
        "đánh giá",
        "deep",
    )
    GITHUB_HINTS = (
        "github",
        "repo",
        "repository",
        "issue",
        "issues",
        "pull request",
        "discussion",
        "readme",
        "changelog",
        "release notes",
        "commit",
    )
    REDDIT_HINTS = (
        "reddit",
        "subreddit",
        "thread",
    )
    COMMUNITY_HINTS = (
        "community",
        "user feedback",
        "user opinion",
        "what people say",
        "what users say",
        "real world",
        "real-world",
        "experience",
        "experiences",
        "feedback",
        "cộng đồng",
        "người dùng",
        "kinh nghiệm",
        "phản hồi",
        "review thực tế",
        "ý kiến",
    )
    TROUBLESHOOT_HINTS = (
        "error",
        "exception",
        "traceback",
        "stack trace",
        "bug",
        "fix",
        "workaround",
        "crash",
        "failing",
        "broken",
        "lỗi",
        "sự cố",
        "cách sửa",
        "khắc phục",
    )
    DOCS_HINTS = (
        "docs",
        "documentation",
        "api reference",
        "reference",
        "sdk",
        "guide",
        "manual",
        "spec",
        "rfc",
        "migration",
        "release note",
        "release notes",
        "changelog",
        "tài liệu",
        "hướng dẫn",
        "đặc tả",
    )
    BROWSER_FALLBACK_HINTS = (
        "enable javascript",
        "javascript is required",
        "please turn javascript on",
        "loading...",
        "just a moment",
        "access denied",
        "verify you are human",
        "captcha",
    )
    OFFICIAL_DOMAIN_HINTS = {
        "openai": ("openai.com",),
        "anthropic": ("anthropic.com",),
        "nvidia": ("nvidia.com", "blogs.nvidia.com", "developer.nvidia.com"),
        "google": ("google.com", "blog.google", "deepmind.google", "ai.google.dev", "cloud.google.com"),
        "microsoft": ("microsoft.com", "blogs.microsoft.com", "learn.microsoft.com"),
        "github": ("github.com", "github.blog"),
        "meta": ("meta.com", "about.fb.com", "ai.meta.com"),
        "apple": ("apple.com", "developer.apple.com"),
        "amazon": ("amazon.com", "aboutamazon.com", "aws.amazon.com"),
        "tesla": ("tesla.com",),
    }
    CONTEXT_DOMAIN_HINTS = {
        "github": ("github.com", "github.blog"),
        "reddit": ("reddit.com",),
        "community": ("reddit.com", "news.ycombinator.com"),
        "troubleshooting": ("stackoverflow.com", "github.com", "reddit.com"),
    }
    PRICE_QUERY_RE = re.compile(
        r"(?i)(?:\b(?:price|pricing|cost|quote|quoted|rate|fee|fees|how much|bao nhieu|gia|phi|ty gia)\b|giá|phí|tỷ giá)"
    )
    VIETNAMESE_QUERY_RE = re.compile(
        r"[ăâđêôơưĂÂĐÊÔƠƯ"
        r"áàảãạấầẩẫậắằẳẵặ"
        r"éèẻẽẹếềểễệ"
        r"íìỉĩị"
        r"óòỏõọốồổỗộớờởỡợ"
        r"úùủũụứừửữự"
        r"ýỳỷỹỵ]"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache_ttl_seconds = max(30, int(getattr(settings, "web_search_cache_ttl_seconds", 600) or 600))
        self._rate_limit_per_minute = max(1, int(getattr(settings, "web_search_rate_limit_per_minute", 30) or 30))
        self._max_deep_reads = max(1, int(getattr(settings, "web_search_max_deep_reads", 2) or 2))
        self._default_max_results = max(1, min(10, int(getattr(settings, "web_search_max_results", 5) or 5)))
        self._verify_max_claims = max(1, min(10, int(getattr(settings, "web_search_verify_max_claims", 4) or 4)))
        self._verify_min_sources_per_claim = max(
            1, min(5, int(getattr(settings, "web_search_verify_min_sources_per_claim", 2) or 2))
        )
        self._verify_max_queries = max(3, min(12, int(getattr(settings, "web_search_verify_max_queries", 8) or 8)))
        self._url_read_max_snippet_chars = max(
            800,
            min(12_000, int(getattr(settings, "web_search_url_read_max_snippet_chars", 4200) or 4200)),
        )
        self._browser_min_content_chars = max(
            300,
            min(4_000, int(getattr(settings, "web_search_browser_min_content_chars", 1200) or 1200)),
        )

        self._redis_cache_prefix = str(
            getattr(settings, "web_search_cache_key_prefix", "pigtex:websearch:cache")
            or "pigtex:websearch:cache"
        )
        self._redis_rate_limit_prefix = str(
            getattr(settings, "web_search_rate_limit_key_prefix", "pigtex:websearch:rate")
            or "pigtex:websearch:rate"
        )

        timeout_seconds = max(3.0, float(getattr(settings, "web_search_timeout_seconds", 12.0) or 12.0))
        self._search_query_timeout_seconds = timeout_seconds + 1.0
        tavily_endpoint = getattr(settings, "web_search_tavily_endpoint", "https://api.tavily.com/search")
        tavily_api_key = getattr(settings, "web_search_tavily_api_key", "")
        provider_order = str(getattr(settings, "web_search_provider_order", "tavily,duckduckgo") or "tavily,duckduckgo")
        jina_endpoint = getattr(settings, "web_search_jina_endpoint", "https://r.jina.ai/")

        self.tavily = TavilySearchProvider(
            api_key=tavily_api_key,
            endpoint=tavily_endpoint,
            timeout_seconds=timeout_seconds,
        )
        self.duckduckgo = DuckDuckGoSearchProvider(
            enabled=bool(getattr(settings, "web_search_duckduckgo_enabled", True)),
            region=str(getattr(settings, "web_search_duckduckgo_region", "us-en") or "us-en"),
            safesearch=str(getattr(settings, "web_search_duckduckgo_safesearch", "moderate") or "moderate"),
            backend=str(getattr(settings, "web_search_duckduckgo_backend", "auto") or "auto"),
            timeout_seconds=timeout_seconds,
        )
        self.github_reader = GitHubReaderProvider(
            enabled=bool(getattr(settings, "web_search_github_enabled", True)),
            api_endpoint=str(getattr(settings, "web_search_github_api_endpoint", "https://api.github.com") or "https://api.github.com"),
            token=str(getattr(settings, "web_search_github_token", "") or ""),
            timeout_seconds=timeout_seconds + 3.0,
            max_selected_files=int(getattr(settings, "web_search_github_max_selected_files", 4) or 4),
            max_file_chars=int(getattr(settings, "web_search_github_max_file_chars", 1800) or 1800),
            max_render_chars=int(getattr(settings, "web_search_github_max_render_chars", 7200) or 7200),
        )
        self.jina_reader = JinaReaderProvider(
            endpoint=jina_endpoint,
            timeout_seconds=timeout_seconds + 4.0,
        )
        self.browser_subagent_provider = BrowserSubagentProvider(
            enabled=bool(getattr(settings, "web_search_browser_enabled", True)),
            timeout_seconds=float(getattr(settings, "web_search_browser_timeout_seconds", timeout_seconds + 8.0) or (timeout_seconds + 8.0)),
            max_content_chars=int(getattr(settings, "web_search_browser_max_content_chars", 20_000) or 20_000),
        )
        self.search_broker = SearchBroker(
            providers=self._build_provider_chain(provider_order),
            min_results_to_stop=4,
        )

        self._cache: Dict[str, Tuple[float, List[SearchResult]]] = {}
        self._cache_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._recent_search_calls: Deque[float] = deque()
        self._redis = self._build_redis_client()

    def _build_provider_chain(self, provider_order: str) -> List[Tuple[str, object]]:
        available = {
            "tavily": self.tavily,
            "duckduckgo": self.duckduckgo,
            "ddg": self.duckduckgo,
        }
        ordered_names = [
            part.strip().lower()
            for part in str(provider_order or "").split(",")
            if part.strip()
        ]
        if not ordered_names:
            ordered_names = ["tavily", "duckduckgo"]

        providers: List[Tuple[str, object]] = []
        seen: set[str] = set()
        for name in ordered_names:
            normalized = "duckduckgo" if name == "ddg" else name
            if normalized in seen:
                continue
            provider = available.get(normalized)
            if provider is None:
                continue
            seen.add(normalized)
            providers.append((normalized, provider))
        return providers

    def _build_redis_client(self):
        redis_url = (getattr(self.settings, "redis_url", "") or "").strip()
        if not redis_url or not REDIS_AVAILABLE:
            return None
        try:
            socket_timeout = float(
                getattr(self.settings, "rate_limit_redis_socket_timeout_seconds", 1.0) or 1.0
            )
            return redis_async.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=max(0.1, socket_timeout),
            )
        except Exception as e:
            logger.warning("Redis web-search client init failed: %s", e)
            return None

    def _redis_cache_key(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return f"{self._redis_cache_prefix}:{digest}"

    def _serialize_results(self, results: Iterable[SearchResult]) -> str:
        payload = [
            {
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet,
                "full_content": result.full_content,
                "relevance_score": result.relevance_score,
                "source_provider": result.source_provider,
                "published_at": result.published_at,
                "domain": result.domain,
                "recency_score": result.recency_score,
                "credibility_score": result.credibility_score,
            }
            for result in results
        ]
        return json.dumps(payload, ensure_ascii=False)

    def _deserialize_results(self, raw_json: str) -> List[SearchResult]:
        try:
            payload = json.loads(raw_json or "[]")
        except Exception:
            return []
        if not isinstance(payload, list):
            return []

        results: List[SearchResult] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                results.append(
                    SearchResult(
                        title=str(item.get("title") or "").strip() or "Untitled source",
                        url=str(item.get("url") or "").strip(),
                        snippet=str(item.get("snippet") or "").strip(),
                        full_content=str(item.get("full_content") or "").strip() or None,
                        relevance_score=float(item.get("relevance_score") or 0.0),
                        source_provider=str(item.get("source_provider") or "tavily"),
                        published_at=str(item.get("published_at") or "").strip() or None,
                        domain=str(item.get("domain") or "").strip(),
                        recency_score=float(item.get("recency_score") or 0.0),
                        credibility_score=float(item.get("credibility_score") or 0.0),
                    )
                )
            except Exception:
                continue
        return results

    async def run(
        self,
        user_message: str,
        force: bool = False,
        max_results: int | None = None,
        deep_read: bool = False,
        mode: str = "auto",
        deep_verify: bool = False,
        max_claims: int | None = None,
    ) -> SearchContext:
        started_at = perf_counter()
        text = (user_message or "").strip()
        intent = self._detect_intent(text)
        resolved_mode = self._resolve_mode(mode, intent, deep_read=deep_read, deep_verify=deep_verify)

        context = SearchContext(
            search_intent=intent,
            mode=resolved_mode,
            checked_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )

        if not text:
            context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
            return context

        future_date_warning = self._detect_future_date_warning(text)
        if future_date_warning:
            context.warnings.append(future_date_warning)
        official_domains = self._infer_official_domains(text)
        needs_official_sources = self._should_prefer_official_sources(text, intent, resolved_mode)
        preferred_domains = self._infer_preferred_domains(
            text,
            intent=intent,
            needs_official_sources=needs_official_sources,
            official_domains=official_domains,
        )

        price_query = self._is_price_query(text)
        if price_query:
            context.query_focus = "price"
            context.answer_guidance = [
                "If the user asks for price, fee, cost, or rate, answer with the exact number when the evidence supports it.",
                "If sources disagree, return the tightest defensible range and name the source/date for each number.",
                "Always include currency plus the observed date, time, market, or region when the source provides it.",
                "Do not use vague wording such as 'khá cao' or 'dao động' without numbers.",
            ]

        should_search = force or intent != SearchIntent.NO_SEARCH or resolved_mode != SearchMode.AUTO
        if not should_search:
            context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
            return context

        urls = self._extract_urls(text)
        if urls:
            context.mode = SearchMode.URL_READ
            context.search_queries = urls
            url_results = await self._run_url_reads(urls)
            ranked_url_results = enrich_and_rank_results(
                url_results,
                mode=SearchMode.URL_READ,
                preferred_domains=preferred_domains,
            )
            facts, citations = extract_facts_and_citations(
                ranked_url_results,
                mode=SearchMode.URL_READ,
                max_facts=max(1, len(ranked_url_results)),
                max_snippet_chars=self._url_read_chars_per_result(ranked_url_results),
                preferred_domains=preferred_domains,
                preserve_formatting=True,
                focus="price" if price_query else None,
            )
            context.facts = facts
            context.citations = citations
            context.raw_results_count = len(ranked_url_results)
            if any(self.github_reader.supports_url(url) for url in urls):
                if not any(result.source_provider == "github_api" for result in ranked_url_results):
                    context.warnings.append(
                        "Structured GitHub read was unavailable, so PigTex fell back to generic page reading. "
                        "Set WEB_SEARCH_GITHUB_TOKEN to improve GitHub rate-limit headroom."
                    )
            context.confidence_score = self._compute_context_confidence(citations, mode=SearchMode.URL_READ)
            context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
            return context

        if not self.search_broker.is_enabled:
            logger.info("Web search skipped: no search provider is configured")
            context.warnings.append("Web search provider is not configured on server.")
            context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
            return context

        limit = max(1, min(10, int(max_results or self._default_max_results)))
        verify_claim_limit = max(1, min(10, int(max_claims or self._verify_max_claims)))

        claims: List[str] = []
        if resolved_mode == SearchMode.DEEP_VERIFY:
            claims = self._extract_claim_candidates(text, max_claims=verify_claim_limit)
            planned_queries = self._plan_verify_queries(
                text,
                claims,
                preferred_domains=preferred_domains,
                needs_official_sources=needs_official_sources,
            )
        else:
            planned_queries = self._plan_queries(
                text,
                intent,
                mode=resolved_mode,
                preferred_domains=preferred_domains,
                needs_official_sources=needs_official_sources,
            )

        if not planned_queries:
            context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
            return context

        context.search_queries = [query.query for query in planned_queries]

        query_tasks = [
            asyncio.create_task(self._search_query(query, max_results=limit))
            for query in planned_queries
        ]
        task_outputs = await asyncio.gather(*query_tasks, return_exceptions=True)

        all_results: List[SearchResult] = []
        timeout_warning_added = False
        for query, output in zip(planned_queries, task_outputs):
            if isinstance(output, Exception):
                if isinstance(output, SearchQueryTimeoutError) and not timeout_warning_added:
                    context.warnings.append(
                        "Web search timed out before all live sources were fetched. PigTex continued with partial or empty results."
                    )
                    timeout_warning_added = True
                logger.warning("Search query failed query=%s error=%s", query.query, output)
                continue
            all_results.extend(output)

        deduped_results = self._dedupe_results(all_results)
        if planned_queries and not deduped_results:
            context.warnings.append("Web search did not return any usable source results.")

        should_deep_read = (
            deep_read
            or price_query
            or resolved_mode in (SearchMode.DEEP_VERIFY, SearchMode.URL_READ)
            or intent == SearchIntent.DEEP_RESEARCH
        )
        if should_deep_read and deduped_results:
            await self._deep_read_top_results(deduped_results)

        ranked_results = enrich_and_rank_results(
            deduped_results,
            mode=resolved_mode,
            preferred_domains=preferred_domains,
        )
        facts, citations = extract_facts_and_citations(
            ranked_results,
            max_facts=max(limit + 1, 6),
            mode=resolved_mode,
            preferred_domains=preferred_domains,
            focus="price" if price_query else None,
        )

        context.facts = facts
        context.citations = citations
        context.raw_results_count = len(ranked_results)

        if resolved_mode == SearchMode.DEEP_VERIFY and claims:
            verification, confidence, conflicts = build_claim_verification(
                claims=claims,
                citations=citations,
                min_sources_per_claim=self._verify_min_sources_per_claim,
            )
            context.claim_verification = verification
            context.claims_verified_count = len(verification)
            context.confidence_score = confidence
            context.conflicts_count = conflicts
            if conflicts > 0:
                context.warnings.append(
                    "Some claims have conflicting source evidence. The final answer should mention this uncertainty."
                )
        else:
            context.confidence_score = self._compute_context_confidence(citations, mode=resolved_mode)

        if official_domains and needs_official_sources and resolved_mode == SearchMode.DEEP_VERIFY:
            if not any(self._matches_preferred_domain(str(cite.get("domain") or ""), official_domains) for cite in citations):
                context.warnings.append(
                    "No official-domain sources were found for the main entity. Verification may rely on secondary reporting."
                )

        context.total_search_time_ms = int((perf_counter() - started_at) * 1000)
        return context

    def _resolve_mode(
        self,
        mode: str,
        intent: SearchIntent,
        deep_read: bool,
        deep_verify: bool,
    ) -> SearchMode:
        normalized = (mode or "auto").strip().lower()
        if normalized in {"deep", "verify", "deep_verify", "research"}:
            return SearchMode.DEEP_VERIFY
        if normalized in {"realtime", "fast", "live", "latest"}:
            return SearchMode.REALTIME
        if normalized in {"url_read", "url"}:
            return SearchMode.URL_READ

        if deep_verify:
            return SearchMode.DEEP_VERIFY
        if intent == SearchIntent.URL_READ:
            return SearchMode.URL_READ
        if intent == SearchIntent.DEEP_RESEARCH:
            return SearchMode.DEEP_VERIFY if deep_verify else SearchMode.AUTO
        if intent == SearchIntent.FACTUAL_CHECK:
            return SearchMode.DEEP_VERIFY if (deep_verify or deep_read) else SearchMode.REALTIME
        if intent == SearchIntent.REALTIME_INFO:
            return SearchMode.REALTIME
        return SearchMode.AUTO

    def _extract_urls(self, text: str) -> List[str]:
        urls = [match.group(0).strip(".,)") for match in self.URL_RE.finditer(text or "")]
        deduped: List[str] = []
        seen: set[str] = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped[: self._max_deep_reads]

    def _strip_urls(self, text: str) -> str:
        return self.URL_RE.sub("", text or "").strip()

    def _detect_intent(self, text: str) -> SearchIntent:
        normalized = (text or "").lower().strip()
        if not normalized:
            return SearchIntent.NO_SEARCH
        if self.URL_RE.search(normalized):
            return SearchIntent.URL_READ
        if any(hint in normalized for hint in self.REALTIME_HINTS):
            return SearchIntent.REALTIME_INFO
        if any(hint in normalized for hint in self.RESEARCH_HINTS):
            return SearchIntent.DEEP_RESEARCH
        if any(hint in normalized for hint in self.FACTUAL_HINTS):
            return SearchIntent.FACTUAL_CHECK
        return SearchIntent.NO_SEARCH

    def _detect_future_date_warning(self, text: str) -> str | None:
        today = datetime.now(timezone.utc).date()
        detected_dates: List[date] = []

        for match in self.ISO_DATE_RE.finditer(text or ""):
            try:
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                detected_dates.append(date(y, m, d))
            except Exception:
                continue

        for match in self.SLASH_DATE_RE.finditer(text or ""):
            try:
                first = int(match.group(1))
                second = int(match.group(2))
                year = int(match.group(3))

                # Heuristic:
                # - If first > 12 -> DD/MM/YYYY
                # - If second > 12 -> MM/DD/YYYY
                # - Otherwise default to DD/MM/YYYY for Vietnamese-first UX.
                if first > 12:
                    day, month = first, second
                elif second > 12:
                    month, day = first, second
                else:
                    day, month = first, second
                detected_dates.append(date(year, month, day))
            except Exception:
                continue

        future_dates = sorted({candidate for candidate in detected_dates if candidate > today})
        if not future_dates:
            return None

        first_future = future_dates[0].isoformat()
        return (
            f"The query references future date {first_future}. "
            "No observed real-world data exists yet for that date."
        )

    def _query_topic_for_intent(self, text: str, intent: SearchIntent, mode: SearchMode) -> str:
        if self._is_price_query(text):
            return "general"
        if mode == SearchMode.REALTIME:
            return "news"
        return "news" if intent == SearchIntent.REALTIME_INFO else "general"

    def _is_price_query(self, text: str) -> bool:
        return bool(self.PRICE_QUERY_RE.search((text or "").strip()))

    def _looks_vietnamese_query(self, text: str) -> bool:
        normalized = (text or "").strip().lower()
        if self.VIETNAMESE_QUERY_RE.search(normalized):
            return True
        return any(
            hint in normalized
            for hint in (" hôm nay", " hom nay", " bao nhiêu", " bao nhieu", " giá", " phí", " tỷ giá", " tỉ giá")
        )

    def _contains_any_hint(self, text: str, hints: Sequence[str]) -> bool:
        normalized = (text or "").lower()
        if not normalized:
            return False
        return any(hint in normalized for hint in hints)

    def _infer_official_domains(self, text: str) -> List[str]:
        normalized = (text or "").lower()
        domains: List[str] = []
        seen: set[str] = set()

        for url in self._extract_urls(text):
            parsed = urlparse(url)
            host = (parsed.netloc or "").strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host and host not in seen:
                seen.add(host)
                domains.append(host)

        for token, domain_values in self.OFFICIAL_DOMAIN_HINTS.items():
            if re.search(rf"\b{re.escape(token)}\b", normalized):
                for domain in domain_values:
                    lowered = domain.lower().strip()
                    if lowered and lowered not in seen:
                        seen.add(lowered)
                        domains.append(lowered)
        return domains[:4]

    def _should_prefer_official_sources(self, text: str, intent: SearchIntent, mode: SearchMode) -> bool:
        if mode == SearchMode.DEEP_VERIFY or intent == SearchIntent.FACTUAL_CHECK:
            return True
        return self._contains_any_hint(text, self.DOCS_HINTS) or self._contains_any_hint(
            text,
            ("official", "announcement", "press release", "nguồn chính thức", "thông báo chính thức"),
        )

    def _infer_preferred_domains(
        self,
        text: str,
        intent: SearchIntent,
        needs_official_sources: bool,
        official_domains: Sequence[str] | None = None,
    ) -> List[str]:
        domains: List[str] = []
        seen: set[str] = set()

        def _add_domains(values: Sequence[str]) -> None:
            for value in values:
                normalized = (value or "").strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                domains.append(normalized)

        explicit_github = self._contains_any_hint(text, self.GITHUB_HINTS)
        explicit_reddit = self._contains_any_hint(text, self.REDDIT_HINTS)
        community_context = self._contains_any_hint(text, self.COMMUNITY_HINTS)
        troubleshooting_context = self._contains_any_hint(text, self.TROUBLESHOOT_HINTS)

        if explicit_github:
            _add_domains(self.CONTEXT_DOMAIN_HINTS["github"])
        if explicit_reddit:
            _add_domains(self.CONTEXT_DOMAIN_HINTS["reddit"])
        if community_context:
            _add_domains(self.CONTEXT_DOMAIN_HINTS["community"])
        if troubleshooting_context:
            _add_domains(self.CONTEXT_DOMAIN_HINTS["troubleshooting"])

        if needs_official_sources:
            _add_domains(official_domains or [])

        if (
            not explicit_github
            and not explicit_reddit
            and not community_context
            and troubleshooting_context
            and not needs_official_sources
            and intent in (SearchIntent.DEEP_RESEARCH, SearchIntent.NO_SEARCH)
        ):
            _add_domains(("github.com", "stackoverflow.com"))

        return domains[:5]

    def _matches_preferred_domain(self, domain: str, preferred_domains: Sequence[str]) -> bool:
        normalized_domain = (domain or "").strip().lower()
        if not normalized_domain:
            return False
        for preferred in preferred_domains:
            normalized_preferred = (preferred or "").strip().lower()
            if not normalized_preferred:
                continue
            if normalized_domain == normalized_preferred or normalized_domain.endswith(f".{normalized_preferred}"):
                return True
        return False

    def _extract_claim_candidates(self, text: str, max_claims: int) -> List[str]:
        base = self._strip_urls(text)
        if not base:
            return []

        separators = re.compile(r"[;\n]|(?:\.\s+)|(?:\?\s+)|(?:!\s+)")
        chunks = [chunk.strip(" ,.-") for chunk in separators.split(base) if chunk.strip()]

        if not chunks:
            chunks = [base]

        claims: List[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            normalized = " ".join(chunk.split())
            lowered = normalized.lower()
            if len(normalized) < 8 or lowered in seen:
                continue
            seen.add(lowered)
            claims.append(normalized)
            if len(claims) >= max_claims:
                break

        if not claims:
            claims.append(base[:240])

        return claims

    def _plan_queries(
        self,
        text: str,
        intent: SearchIntent,
        mode: SearchMode,
        preferred_domains: Sequence[str] | None = None,
        needs_official_sources: bool = False,
    ) -> List[SearchQuery]:
        base = self._strip_urls(text)
        if not base:
            return []

        price_query = self._is_price_query(base)
        base_topic = self._query_topic_for_intent(base, intent, mode)
        candidates: List[SearchQuery] = [
            SearchQuery(
                query=base,
                topic=base_topic,
                priority=1,
                freshness_days=7 if price_query and mode == SearchMode.REALTIME else None,
            ),
        ]

        if price_query:
            lowered = base.lower()
            is_vietnamese = self._looks_vietnamese_query(base)
            if "hôm nay" not in lowered and "hom nay" not in lowered and "today" not in lowered and "current" not in lowered:
                candidates.append(
                    SearchQuery(
                        query=f"{base} {'hôm nay' if is_vietnamese else 'today'}",
                        topic="general",
                        priority=2,
                        freshness_days=7,
                    )
                )
            if "giá" not in lowered and re.search(r"\bgia\b", lowered) is None and "price" not in lowered:
                candidates.append(
                    SearchQuery(
                        query=f"{base} {'giá' if is_vietnamese else 'price'}",
                        topic="general",
                        priority=3,
                        freshness_days=30,
                    )
                )
            candidates.append(
                SearchQuery(
                    query=f"{base} {'cập nhật giá' if is_vietnamese else 'latest price update'}",
                    topic="news",
                    priority=4,
                    freshness_days=7,
                )
            )
            if needs_official_sources:
                candidates.append(
                    SearchQuery(
                        query=f"{base} {'giá chính thức' if is_vietnamese else 'official price'}",
                        topic="general",
                        priority=5,
                        freshness_days=30,
                    )
                )
            for index, domain in enumerate((preferred_domains or [])[:3], start=1):
                candidates.append(
                    SearchQuery(
                        query=f"{base} site:{domain}",
                        topic="general",
                        priority=5 + index,
                        freshness_days=30,
                    )
                )
        elif mode == SearchMode.REALTIME or intent == SearchIntent.REALTIME_INFO:
            candidates.append(
                SearchQuery(
                    query=f"{base} latest update",
                    topic="news",
                    priority=2,
                    freshness_days=7,
                )
            )
            if needs_official_sources:
                candidates.append(
                    SearchQuery(
                        query=f"{base} official source",
                        topic="news",
                        priority=3,
                        freshness_days=30,
                    )
                )
            for index, domain in enumerate((preferred_domains or [])[:3], start=1):
                candidates.append(
                    SearchQuery(
                        query=f"{base} site:{domain}",
                        topic="news",
                        priority=3 + index,
                        freshness_days=30,
                    )
                )
        elif intent == SearchIntent.DEEP_RESEARCH:
            candidates.append(SearchQuery(query=f"{base} comparison", priority=2))
            candidates.append(SearchQuery(query=f"{base} benchmark", priority=3))
            for index, domain in enumerate((preferred_domains or [])[:3], start=1):
                candidates.append(
                    SearchQuery(
                        query=f"{base} site:{domain}",
                        priority=3 + index,
                    )
                )
        elif intent == SearchIntent.FACTUAL_CHECK:
            if needs_official_sources:
                candidates.append(SearchQuery(query=f"{base} official statement", priority=2))
            candidates.append(SearchQuery(query=f"{base} fact check", priority=3))
            for index, domain in enumerate((preferred_domains or [])[:3], start=1):
                candidates.append(
                    SearchQuery(
                        query=f"{base} site:{domain}",
                        topic="news",
                        priority=3 + index,
                        freshness_days=60,
                    )
                )

        deduped: List[SearchQuery] = []
        seen_queries: set[str] = set()
        for candidate in candidates:
            normalized = " ".join(candidate.query.split())
            lowered = normalized.lower()
            if not normalized or lowered in seen_queries:
                continue
            seen_queries.add(lowered)
            candidate.query = normalized
            deduped.append(candidate)
            if len(deduped) >= 6:
                break
        return deduped

    def _plan_verify_queries(
        self,
        text: str,
        claims: Sequence[str],
        preferred_domains: Sequence[str] | None = None,
        needs_official_sources: bool = False,
    ) -> List[SearchQuery]:
        base = self._strip_urls(text)
        candidates: List[SearchQuery] = []

        if base:
            candidates.append(SearchQuery(query=base, topic="news", priority=1, freshness_days=30))
            if needs_official_sources:
                candidates.append(SearchQuery(query=f"{base} official source", topic="news", priority=2, freshness_days=45))

        priority = 3
        for claim in claims:
            normalized_claim = " ".join(claim.split())
            if not normalized_claim:
                continue
            candidates.append(
                SearchQuery(
                    query=normalized_claim,
                    topic="news",
                    priority=priority,
                    freshness_days=30,
                    claim=normalized_claim,
                )
            )
            priority += 1
            candidates.append(
                SearchQuery(
                    query=f"{normalized_claim} official source",
                    topic="news",
                    priority=priority,
                    freshness_days=60,
                    claim=normalized_claim,
                )
            )
            priority += 1
            candidates.append(
                SearchQuery(
                    query=f"{normalized_claim} fact check",
                    topic="news",
                    priority=priority,
                    freshness_days=90,
                    claim=normalized_claim,
                )
            )
            priority += 1
            for domain in (preferred_domains or [])[:2]:
                candidates.append(
                    SearchQuery(
                        query=f"{normalized_claim} site:{domain}",
                        topic="news",
                        priority=priority,
                        freshness_days=60,
                        claim=normalized_claim,
                    )
                )
                priority += 1

        deduped: List[SearchQuery] = []
        seen: set[str] = set()
        for candidate in candidates:
            lowered = candidate.query.lower().strip()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(candidate)
            if len(deduped) >= self._verify_max_queries:
                break
        return deduped

    async def _search_query(self, query: SearchQuery, max_results: int) -> List[SearchResult]:
        topic = query.topic or "general"
        cache_key = f"{query.query.lower()}::{topic}::{max_results}::{query.freshness_days or 0}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        if not await self._allow_rate_limited_call():
            logger.warning("Web search rate limit reached. query=%s", query.query)
            return []

        try:
            results = await asyncio.wait_for(
                self.search_broker.search(
                    query=query.query,
                    topic=topic,
                    max_results=max_results,
                    freshness_days=query.freshness_days,
                ),
                timeout=self._search_query_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Web search query timed out query=%s timeout_seconds=%s",
                query.query,
                self._search_query_timeout_seconds,
            )
            raise SearchQueryTimeoutError(query.query) from None
        await self._cache_set(cache_key, results)
        return self._clone_results(results)

    async def search_web(
        self,
        query: str,
        *,
        topic: str = "general",
        max_results: int | None = None,
        freshness_days: int | None = None,
    ) -> List[SearchResult]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        limit = max(1, min(10, int(max_results or self._default_max_results)))
        return await self._search_query(
            SearchQuery(
                query=normalized_query,
                topic=topic,
                freshness_days=freshness_days,
            ),
            max_results=limit,
        )

    async def read_url_content(self, url: str) -> SearchResult | None:
        return await self._read_url(url)

    async def browser_subagent(self, url: str) -> SearchResult | None:
        return await self.browser_subagent_provider.read(url)

    async def _cache_get(self, key: str) -> List[SearchResult] | None:
        if self._redis is not None:
            try:
                cached_payload = await self._redis.get(self._redis_cache_key(key))
                if cached_payload:
                    redis_results = self._deserialize_results(cached_payload)
                    if redis_results:
                        return self._clone_results(redis_results)
            except Exception as e:
                logger.debug("Redis cache get failed: %s", e)

        now = perf_counter()
        async with self._cache_lock:
            record = self._cache.get(key)
            if not record:
                return None
            created_at, results = record
            if now - created_at > self._cache_ttl_seconds:
                self._cache.pop(key, None)
                return None
            return self._clone_results(results)

    async def _cache_set(self, key: str, results: List[SearchResult]) -> None:
        if not results:
            return

        if self._redis is not None:
            try:
                await self._redis.setex(
                    self._redis_cache_key(key),
                    self._cache_ttl_seconds,
                    self._serialize_results(results),
                )
            except Exception as e:
                logger.debug("Redis cache set failed: %s", e)

        async with self._cache_lock:
            self._cache[key] = (perf_counter(), self._clone_results(results))
            # Keep cache bounded.
            if len(self._cache) > 256:
                oldest_key = min(self._cache.items(), key=lambda item: item[1][0])[0]
                self._cache.pop(oldest_key, None)

    async def _allow_rate_limited_call(self) -> bool:
        if self._redis is not None:
            try:
                bucket = int(time.time() // 60)
                rate_key = f"{self._redis_rate_limit_prefix}:{bucket}"
                count = await self._redis.incr(rate_key)
                if count == 1:
                    await self._redis.expire(rate_key, 61)
                return int(count) <= self._rate_limit_per_minute
            except Exception as e:
                logger.debug("Redis rate-limit check failed: %s", e)

        now = perf_counter()
        async with self._rate_lock:
            while self._recent_search_calls and now - self._recent_search_calls[0] > 60.0:
                self._recent_search_calls.popleft()
            if len(self._recent_search_calls) >= self._rate_limit_per_minute:
                return False
            self._recent_search_calls.append(now)
            return True

    async def _run_url_reads(self, urls: List[str]) -> List[SearchResult]:
        tasks = [asyncio.create_task(self.read_url_content(url)) for url in urls[: self._max_deep_reads]]
        if not tasks:
            return []
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
        results: List[SearchResult] = []
        for output in outputs:
            if isinstance(output, Exception):
                logger.warning("URL deep-read failed: %s", output)
                continue
            if output:
                results.append(output)
        return results

    async def _deep_read_top_results(self, results: List[SearchResult]) -> None:
        candidates = [result for result in results if (result.url and not result.full_content)]
        candidates = candidates[: self._max_deep_reads]
        if not candidates:
            return

        semaphore = asyncio.Semaphore(2)

        async def _deep_read(result: SearchResult) -> None:
            async with semaphore:
                enriched = await self.read_url_content(result.url)
                if not enriched:
                    return
                result.full_content = enriched.full_content or enriched.snippet
                if enriched.snippet:
                    result.snippet = enriched.snippet
                if enriched.title and (
                    enriched.source_provider == "github_api" or not result.title or result.title.startswith("http")
                ):
                    result.title = enriched.title
                if enriched.source_provider:
                    result.source_provider = enriched.source_provider
                if enriched.domain:
                    result.domain = enriched.domain
                if enriched.published_at:
                    result.published_at = enriched.published_at

        await asyncio.gather(
            *[asyncio.create_task(_deep_read(result)) for result in candidates],
            return_exceptions=True,
        )

    def _dedupe_results(self, results: Iterable[SearchResult]) -> List[SearchResult]:
        by_url: Dict[str, SearchResult] = {}
        for result in results:
            url = (result.url or "").strip()
            if not url:
                continue
            if url not in by_url:
                by_url[url] = result
                continue

            existing = by_url[url]
            existing_score = compute_result_score(existing, SearchMode.AUTO)
            candidate_score = compute_result_score(result, SearchMode.AUTO)
            if candidate_score > existing_score:
                by_url[url] = result

        return list(by_url.values())

    def _compute_context_confidence(self, citations: Sequence[Dict], mode: SearchMode) -> float:
        if not citations:
            return 0.0
        scores: List[float] = []
        for cite in citations:
            if not isinstance(cite, dict):
                continue
            relevance = float(cite.get("relevance_score") or 0.0)
            credibility = float(cite.get("credibility_score") or 0.0)
            recency = float(cite.get("recency_score") or 0.0)
            if mode == SearchMode.REALTIME:
                score = (0.35 * relevance) + (0.30 * credibility) + (0.35 * recency)
            elif mode == SearchMode.DEEP_VERIFY:
                score = (0.35 * relevance) + (0.50 * credibility) + (0.15 * recency)
            else:
                score = (0.4 * relevance) + (0.4 * credibility) + (0.2 * recency)
            scores.append(max(0.0, min(1.0, score)))

        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 3)

    def _clone_results(self, results: Iterable[SearchResult]) -> List[SearchResult]:
        return [replace(result) for result in results]

    def _url_read_chars_per_result(self, results: Sequence[SearchResult]) -> int:
        count = max(1, len(results))
        return max(900, min(self._url_read_max_snippet_chars, self._url_read_max_snippet_chars // count))

    def _build_url_read_result(self, url: str, content: str, source_provider: str) -> SearchResult:
        parsed = urlparse(url)
        title = parsed.netloc or url
        for line in content.splitlines():
            candidate = line.strip().strip("#").strip()
            if len(candidate) >= 8:
                title = candidate[:140]
                break
        return SearchResult(
            title=title,
            url=url,
            snippet=content[:400],
            full_content=content,
            source_provider=source_provider,
            domain=(parsed.netloc or "").replace("www.", ""),
        )

    def _should_browser_fallback(self, content: str) -> bool:
        normalized = " ".join((content or "").lower().split())
        if not normalized:
            return True
        if len(normalized) < self._browser_min_content_chars:
            return True
        return any(hint in normalized for hint in self.BROWSER_FALLBACK_HINTS)

    async def _read_url(self, url: str) -> SearchResult | None:
        normalized_url = (url or "").strip()
        if not normalized_url:
            return None

        if self.github_reader.supports_url(normalized_url):
            github_result = await self.github_reader.read(normalized_url)
            if github_result:
                return github_result

        content = await self.jina_reader.read(normalized_url)
        browser_result: SearchResult | None = None
        if not content or self._should_browser_fallback(content):
            browser_result = await self.browser_subagent(normalized_url)
            if browser_result:
                return browser_result

        if not content:
            return None
        return self._build_url_read_result(normalized_url, content, "jina_reader")
