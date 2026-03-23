import asyncio
import unittest

from app.config import Settings
from app.search.models import SearchIntent, SearchMode, SearchQuery, SearchResult
from app.search.providers.broker import SearchBroker
from app.search.search_coordinator import SearchCoordinator


class SearchCoordinatorTests(unittest.TestCase):
    def test_deep_research_does_not_force_verify_mode_without_verify_flag(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        resolved = coordinator._resolve_mode(
            mode="auto",
            intent=SearchIntent.DEEP_RESEARCH,
            deep_read=True,
            deep_verify=False,
        )

        self.assertEqual(resolved, SearchMode.AUTO)

    def test_empty_results_are_not_cached(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def scenario() -> None:
            await coordinator._cache_set("empty-case", [])
            cached = await coordinator._cache_get("empty-case")
            self.assertIsNone(cached)

        asyncio.run(scenario())

    def test_run_warns_when_search_returns_no_results(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return []

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="OpenAI latest news today",
                force=True,
                mode="realtime",
            )
            self.assertEqual(context.status_hint, "complete")
            self.assertEqual(context.raw_results_count, 0)
            self.assertIn("Web search did not return any usable source results.", context.warnings)

        asyncio.run(scenario())

    def test_run_warns_when_search_provider_times_out(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))
        coordinator._search_query_timeout_seconds = 0.05

        async def hanging_search(*args, **kwargs):
            await asyncio.sleep(60)
            return []

        coordinator.search_broker.search = hanging_search  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="OpenAI latest news today",
                force=True,
                mode="realtime",
            )
            self.assertEqual(context.status_hint, "timeout")
            self.assertEqual(context.raw_results_count, 0)
            self.assertTrue(any("timed out" in warning.lower() for warning in context.warnings))

        asyncio.run(scenario())

    def test_run_keeps_partial_results_when_total_time_budget_expires(self) -> None:
        coordinator = SearchCoordinator(
            Settings(
                redis_url="",
                web_search_tavily_api_key="test-key",
                web_search_duckduckgo_enabled=False,
            )
        )

        async def fake_search_query(query: SearchQuery, max_results: int = 5):
            if "latest update" in query.query:
                await asyncio.sleep(0.2)
                return [
                    SearchResult(
                        title="Slow source",
                        url="https://example.com/slow",
                        snippet="This result arrived too late.",
                    )
                ]
            await asyncio.sleep(0.01)
            return [
                SearchResult(
                    title="Fast source",
                    url="https://example.com/fast",
                    snippet="Gold traded at 2,351 USD/oz on 2026-03-23.",
                    relevance_score=0.8,
                    source_provider="tavily",
                    published_at="2026-03-23",
                )
            ]

        coordinator._plan_queries = lambda *args, **kwargs: [  # type: ignore[assignment]
            SearchQuery(query="OpenAI latest news today", topic="news", priority=1),
            SearchQuery(query="OpenAI latest news today latest update", topic="news", priority=2),
        ]
        coordinator._search_query = fake_search_query  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="OpenAI latest news today",
                force=True,
                mode="realtime",
                total_timeout_seconds=0.05,
            )
            self.assertEqual(context.status_hint, "timeout")
            self.assertGreaterEqual(context.raw_results_count, 1)
            self.assertTrue(any(citation["url"] == "https://example.com/fast" for citation in context.citations))
            self.assertFalse(any(citation["url"] == "https://example.com/slow" for citation in context.citations))
            self.assertTrue(any("time budget" in warning.lower() or "timed out" in warning.lower() for warning in context.warnings))

        asyncio.run(scenario())

    def test_infer_preferred_domains_detects_known_official_domains_when_context_requires_it(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        domains = coordinator._infer_preferred_domains(
            "Verify the latest OpenAI and NVIDIA announcements with official sources",
            intent=SearchIntent.FACTUAL_CHECK,
            needs_official_sources=True,
            official_domains=coordinator._infer_official_domains(
                "Verify the latest OpenAI and NVIDIA announcements with official sources"
            ),
        )

        self.assertIn("openai.com", domains)
        self.assertIn("nvidia.com", domains)

    def test_infer_preferred_domains_prefers_github_for_repo_issue_queries(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        domains = coordinator._infer_preferred_domains(
            "Find GitHub issues for the React Router SSR bug",
            intent=SearchIntent.DEEP_RESEARCH,
            needs_official_sources=False,
            official_domains=[],
        )

        self.assertEqual(domains[0], "github.com")

    def test_infer_preferred_domains_prefers_reddit_for_community_queries(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        domains = coordinator._infer_preferred_domains(
            "What are people saying on Reddit about Windsurf vs Cursor?",
            intent=SearchIntent.DEEP_RESEARCH,
            needs_official_sources=False,
            official_domains=[],
        )

        self.assertEqual(domains[0], "reddit.com")

    def test_infer_preferred_domains_prefers_market_domains_for_gold_price_queries(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        domains = coordinator._infer_preferred_domains(
            "Giá vàng hôm nay bao nhiêu?",
            intent=SearchIntent.REALTIME_INFO,
            needs_official_sources=False,
            official_domains=[],
        )

        self.assertIn("pnj.com.vn", domains[:3])
        self.assertIn("sjc.com.vn", domains[:3])
        self.assertIn("vn.investing.com", domains)

    def test_infer_preferred_domains_prefers_market_domains_for_bitcoin_price_queries(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        domains = coordinator._infer_preferred_domains(
            "Giá bitcoin hôm nay bao nhiêu?",
            intent=SearchIntent.REALTIME_INFO,
            needs_official_sources=False,
            official_domains=[],
        )

        self.assertIn("coingecko.com", domains[:3])
        self.assertIn("coinmarketcap.com", domains[:4])
        self.assertIn("binance.com", domains[:5])

    def test_provider_chain_prefers_tavily_before_ddg(self) -> None:
        coordinator = SearchCoordinator(
            Settings(
                redis_url="",
                web_search_tavily_api_key="tavily-key",
                web_search_duckduckgo_enabled=True,
            )
        )

        provider_names = [name for name, _ in coordinator.search_broker.providers]

        self.assertEqual(
            provider_names[:2],
            ["tavily", "duckduckgo"],
        )

    def test_plan_queries_skips_official_query_when_not_requested(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        queries = coordinator._plan_queries(
            "OpenAI latest news today",
            intent=SearchIntent.REALTIME_INFO,
            mode=SearchMode.REALTIME,
            preferred_domains=[],
            needs_official_sources=False,
        )

        self.assertFalse(any("official source" in item.query for item in queries))

    def test_price_queries_use_general_topic_and_price_variants(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        queries = coordinator._plan_queries(
            "Giá vàng hôm nay bao nhiêu?",
            intent=SearchIntent.REALTIME_INFO,
            mode=SearchMode.REALTIME,
            preferred_domains=["pnj.com.vn", "sjc.com.vn", "vn.investing.com"],
            needs_official_sources=False,
        )

        self.assertEqual(queries[0].topic, "general")
        self.assertFalse(any("cập nhật giá" in item.query for item in queries))
        self.assertTrue(any("XAU/USD" in item.query for item in queries))
        self.assertTrue(any("site:pnj.com.vn" in item.query for item in queries))

    def test_bitcoin_price_queries_add_direct_market_symbol_query(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        queries = coordinator._plan_queries(
            "Giá bitcoin hôm nay bao nhiêu?",
            intent=SearchIntent.REALTIME_INFO,
            mode=SearchMode.REALTIME,
            preferred_domains=["coingecko.com", "coinmarketcap.com", "binance.com"],
            needs_official_sources=False,
        )

        self.assertTrue(any("BTC/USD" in item.query for item in queries))
        self.assertTrue(any("site:coingecko.com" in item.query for item in queries))

    def test_gold_price_query_filters_irrelevant_fuel_results(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="Gas prices jump as oil spikes",
                    url="https://example.com/gas",
                    snippet="Gas prices and crude oil moved sharply higher today.",
                    relevance_score=0.98,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="Giá vàng hôm nay 23/3/2026 tăng mạnh",
                    url="https://example.com/news/gia-vang-hom-nay-23-3-2026.html",
                    snippet="Bài báo tổng hợp diễn biến giá vàng trong ngày.",
                    relevance_score=0.9,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="Giá vàng hôm nay sáng 23/3/2026 - YouTube",
                    url="https://www.youtube.com/watch?v=demo",
                    snippet="Video cập nhật giá vàng hôm nay.",
                    relevance_score=0.93,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="Bảng giá vàng PNJ hôm nay",
                    url="https://pnj.com.vn/gia-vang",
                    snippet="Giá vàng 24K PNJ mua vào 7,70 triệu/chỉ, bán ra 7,84 triệu/chỉ.",
                    relevance_score=0.42,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="XAU USD | Tỷ giá vàng hôm nay",
                    url="https://vn.investing.com/currencies/xau-usd",
                    snippet="XAU/USD giao dịch quanh 4.140 USD/ounce trong phiên hôm nay.",
                    relevance_score=0.45,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="Giá vàng hôm nay",
                    url="https://24h.com.vn/gia-vang-hom-nay-c425.html",
                    snippet="Giá vàng hôm nay được cập nhật theo thị trường trong nước.",
                    relevance_score=0.87,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
            ]

        async def fake_deep_read(*args, **kwargs):
            return False

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]
        coordinator._deep_read_top_results = fake_deep_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Giá vàng hôm nay bao nhiêu?",
                force=True,
                mode="fast",
                total_timeout_seconds=0.5,
            )
            urls = {citation["url"] for citation in context.citations}
            self.assertNotIn("https://example.com/gas", urls)
            self.assertNotIn("https://example.com/news/gia-vang-hom-nay-23-3-2026.html", urls)
            self.assertNotIn("https://www.youtube.com/watch?v=demo", urls)
            self.assertNotIn("https://24h.com.vn/gia-vang-hom-nay-c425.html", urls)
            self.assertIn("https://pnj.com.vn/gia-vang", urls)
            self.assertIn("https://vn.investing.com/currencies/xau-usd", urls)
            self.assertTrue(any("off-topic" in warning for warning in context.warnings))
            self.assertTrue(any("low-value price" in warning for warning in context.warnings))
            self.assertEqual(context.citations[0]["domain"], "pnj.com.vn")
            self.assertIn(context.citations[0]["result_type"], {"quote_page", "listing_page"})
            self.assertGreater(context.citations[0]["evidence_quality_score"], 0.6)

        asyncio.run(scenario())

    def test_price_query_warns_when_direct_numeric_evidence_is_insufficient(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="Bitcoin investors await Fed decision",
                    url="https://example.com/news/bitcoin-investors-await-fed-decision",
                    snippet="Analysts discussed whether bitcoin could move higher if rates are cut.",
                    relevance_score=0.84,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="Crypto market morning roundup",
                    url="https://example.com/news/crypto-market-morning-roundup",
                    snippet="The report summarized sentiment without quoting a live BTC/USD number.",
                    relevance_score=0.8,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
            ]

        async def fake_deep_read(*args, **kwargs):
            return False

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]
        coordinator._deep_read_top_results = fake_deep_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Gia bitcoin hom nay bao nhieu?",
                force=True,
                mode="fast",
                total_timeout_seconds=0.5,
            )
            self.assertTrue(any("did not produce enough direct numeric evidence" in warning for warning in context.warnings))
            self.assertTrue(any("Do not answer with a specific current number" in item for item in context.answer_guidance))
            self.assertLessEqual(context.confidence_score, 0.42)

        asyncio.run(scenario())

    def test_price_query_warns_when_only_one_domain_family_has_direct_numeric_evidence(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="Bảng giá vàng PNJ hôm nay",
                    url="https://pnj.com.vn/site/gia-vang",
                    snippet="Giá vàng 24K PNJ mua vào 7,70 triệu/chỉ, bán ra 7,84 triệu/chỉ.",
                    relevance_score=0.88,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
            ]

        async def fake_deep_read(*args, **kwargs):
            return False

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]
        coordinator._deep_read_top_results = fake_deep_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Giá vàng hôm nay bao nhiêu?",
                force=True,
                mode="fast",
                total_timeout_seconds=0.5,
            )
            self.assertTrue(any("did not produce enough direct numeric evidence" in warning for warning in context.warnings))
            self.assertLessEqual(context.confidence_score, 0.42)

        asyncio.run(scenario())

    def test_diversify_price_results_keeps_one_result_per_domain_family(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        results = [
            SearchResult(
                title="Bảng giá vàng PNJ hôm nay",
                url="https://pnj.com.vn/site/gia-vang",
                snippet="PNJ gold board.",
                domain="pnj.com.vn",
                result_type="quote_page",
            ),
            SearchResult(
                title="Bảng giá vàng PNJ cache-busted",
                url="https://pnj.com.vn/site/gia-vang?r=1728703561644",
                snippet="PNJ gold board.",
                domain="pnj.com.vn",
                result_type="quote_page",
            ),
            SearchResult(
                title="Giá vàng PNJ mirror",
                url="https://giavang.pnj.com.vn/",
                snippet="PNJ mirror.",
                domain="giavang.pnj.com.vn",
                result_type="listing_page",
            ),
            SearchResult(
                title="XAU/USD",
                url="https://vn.investing.com/currencies/xau-usd",
                snippet="Gold quote.",
                domain="vn.investing.com",
                result_type="quote_page",
            ),
            SearchResult(
                title="XAU/USD mirror",
                url="https://investing.com/currencies/xau-usd",
                snippet="Gold quote.",
                domain="investing.com",
                result_type="quote_page",
            ),
        ]

        diversified = coordinator._diversify_price_results(results)

        urls = [item.url for item in diversified]
        self.assertEqual(urls[0], "https://pnj.com.vn/site/gia-vang")
        self.assertIn("https://vn.investing.com/currencies/xau-usd", urls)
        self.assertNotIn("https://pnj.com.vn/site/gia-vang?r=1728703561644", urls)
        self.assertNotIn("https://giavang.pnj.com.vn/", urls)
        self.assertNotIn("https://investing.com/currencies/xau-usd", urls)

    def test_fx_pair_queries_prefer_exact_pair_order_over_inverse_pair(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="VND/USD",
                    url="https://vn.investing.com/currencies/vnd-usd",
                    snippet="VND/USD traded at 0.00004 today.",
                    relevance_score=0.91,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
                SearchResult(
                    title="USD/VND",
                    url="https://xe.com/currencyconverter/convert?Amount=1&From=USD&To=VND",
                    snippet="USD/VND traded around 25,430 today.",
                    relevance_score=0.83,
                    source_provider="tavily",
                    published_at="2026-03-23",
                ),
            ]

        async def fake_deep_read(*args, **kwargs):
            return False

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]
        coordinator._deep_read_top_results = fake_deep_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Ty gia USD VND hom nay bao nhieu?",
                force=True,
                mode="fast",
                total_timeout_seconds=0.5,
            )
            self.assertEqual(
                context.citations[0]["url"],
                "https://xe.com/currencyconverter/convert?Amount=1&From=USD&To=VND",
            )

        asyncio.run(scenario())

    def test_price_query_guidance_and_fact_focus_are_numeric(self) -> None:
        coordinator = SearchCoordinator(Settings(web_search_tavily_api_key="test-key", redis_url=""))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="Gold Market Snapshot",
                    url="https://example.com/gold",
                    snippet=(
                        "Market commentary stayed mixed today. "
                        "Spot gold traded at 2,351 USD/oz on 2026-03-23, up 0.4% from yesterday."
                    ),
                    relevance_score=0.8,
                    source_provider="tavily",
                    published_at="2026-03-23",
                )
            ]

        async def fake_read(url: str):
            return SearchResult(
                title="Gold Market Snapshot",
                url=url,
                snippet="Spot gold traded at 2,351 USD/oz on 2026-03-23.",
                full_content=(
                    "Opening summary.\n"
                    "Spot gold traded at 2,351 USD/oz on 2026-03-23 in London.\n"
                    "Analysts expect moderate volatility."
                ),
                relevance_score=0.9,
                source_provider="browser_subagent",
                published_at="2026-03-23",
            )

        async def fake_jina_read(url: str):
            return (
                "Opening summary.\n"
                "Spot gold traded at 2,351 USD/oz on 2026-03-23 in London.\n"
                "Analysts expect moderate volatility."
            )

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]
        coordinator.browser_subagent_provider.read = fake_read  # type: ignore[assignment]
        coordinator.jina_reader.read = fake_jina_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Giá vàng hôm nay bao nhiêu?",
                force=True,
                mode="auto",
            )
            self.assertEqual(context.query_focus, "price")
            self.assertTrue(any("exact number" in item for item in context.answer_guidance))
            self.assertIn("2,351 USD/oz", context.facts[0])

        asyncio.run(scenario())

    def test_url_read_uses_github_reader_and_preserves_code_formatting(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        async def fake_github_read(url: str) -> SearchResult | None:
            return SearchResult(
                title="openai/openai-python:src/openai/__init__.py",
                url=url,
                snippet="openai/openai-python file src/openai/__init__.py",
                full_content="# GitHub Repository: openai/openai-python\n\n## File: src/openai/__init__.py\n```python\nfrom openai import OpenAI\n```",
                source_provider="github_api",
                domain="github.com",
            )

        async def fake_jina_read(url: str) -> str:
            raise AssertionError("Jina reader should not be used for GitHub URLs")

        coordinator.github_reader.read = fake_github_read  # type: ignore[assignment]
        coordinator.jina_reader.read = fake_jina_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Read this repo file https://github.com/openai/openai-python/blob/main/src/openai/__init__.py",
                force=True,
                mode="url_read",
            )
            self.assertEqual(context.mode, SearchMode.URL_READ)
            self.assertEqual(context.raw_results_count, 1)
            self.assertEqual(context.citations[0]["source_provider"], "github_api")
            self.assertIn("```python", context.facts[0])
            self.assertIn("from openai import OpenAI", context.facts[0])

        asyncio.run(scenario())

    def test_url_read_warns_when_github_falls_back_to_generic_reader(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        async def fake_github_read(url: str) -> SearchResult | None:
            return None

        async def fake_jina_read(url: str) -> str:
            return "# GitHub page\nFallback HTML content"

        coordinator.github_reader.read = fake_github_read  # type: ignore[assignment]
        coordinator.jina_reader.read = fake_jina_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Read this repo https://github.com/openai/openai-python",
                force=True,
                mode="url_read",
            )
            self.assertEqual(context.citations[0]["source_provider"], "jina_reader")
            self.assertTrue(any("Structured GitHub read was unavailable" in warning for warning in context.warnings))

        asyncio.run(scenario())

    def test_url_read_uses_browser_subagent_when_generic_reader_returns_dynamic_shell(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        async def fake_github_read(url: str) -> SearchResult | None:
            return None

        async def fake_jina_read(url: str) -> str:
            return "Enable JavaScript to run this app."

        async def fake_browser_read(url: str) -> SearchResult | None:
            return SearchResult(
                title="Dynamic article",
                url=url,
                snippet="Rendered article body",
                full_content="Rendered article body with the real page content.",
                source_provider="browser_subagent",
                domain="example.com",
            )

        coordinator.github_reader.read = fake_github_read  # type: ignore[assignment]
        coordinator.jina_reader.read = fake_jina_read  # type: ignore[assignment]
        coordinator.browser_subagent_provider.read = fake_browser_read  # type: ignore[assignment]

        async def scenario() -> None:
            context = await coordinator.run(
                user_message="Read this page https://example.com/dynamic-app",
                force=True,
                mode="url_read",
            )
            self.assertEqual(context.citations[0]["source_provider"], "browser_subagent")
            self.assertIn("Rendered article body", context.facts[0])

        asyncio.run(scenario())

    def test_search_web_tool_uses_search_broker(self) -> None:
        coordinator = SearchCoordinator(Settings(redis_url="", web_search_duckduckgo_enabled=False))

        async def fake_search(*args, **kwargs):
            return [
                SearchResult(
                    title="OpenAI update",
                    url="https://example.com/openai-update",
                    snippet="OpenAI released an update.",
                )
            ]

        coordinator.search_broker.search = fake_search  # type: ignore[assignment]

        async def scenario() -> None:
            results = await coordinator.search_web("OpenAI latest update")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "OpenAI update")

        asyncio.run(scenario())


class SearchBrokerTests(unittest.TestCase):
    def test_broker_falls_back_when_primary_provider_returns_no_results(self) -> None:
        class EmptyProvider:
            is_enabled = True

            async def search(self, **kwargs):
                return []

        class ResultProvider:
            is_enabled = True

            async def search(self, **kwargs):
                return [
                    SearchResult(
                        title="Fallback hit",
                        url="https://fallback.example.com",
                        snippet="fallback snippet",
                    )
                ]

        broker = SearchBroker(
            providers=[("empty", EmptyProvider()), ("fallback", ResultProvider())],
            min_results_to_stop=1,
        )

        async def scenario() -> None:
            results = await broker.search("query")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Fallback hit")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
