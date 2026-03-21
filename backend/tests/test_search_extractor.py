import unittest

from app.search.extractor import compute_recency_score, enrich_and_rank_results, extract_facts_and_citations
from app.search.models import SearchMode, SearchResult


class SearchExtractorTests(unittest.TestCase):
    def test_rfc2822_dates_match_iso_recency_score(self) -> None:
        iso_score = compute_recency_score("2026-01-17T02:15:30+00:00", SearchMode.REALTIME)
        rfc2822_score = compute_recency_score("Sat, 17 Jan 2026 02:15:30 GMT", SearchMode.REALTIME)

        self.assertEqual(rfc2822_score, iso_score)

    def test_preferred_domain_boost_ranks_official_result_first(self) -> None:
        results = [
            SearchResult(
                title="Secondary source",
                url="https://example.com/openai-launch",
                snippet="OpenAI launch summary",
                relevance_score=0.7,
            ),
            SearchResult(
                title="Official source",
                url="https://openai.com/index/gpt-5-4/",
                snippet="Official OpenAI GPT-5.4 announcement",
                relevance_score=0.62,
            ),
        ]

        ranked = enrich_and_rank_results(
            results,
            mode=SearchMode.DEEP_VERIFY,
            preferred_domains=["openai.com"],
        )

        self.assertEqual(ranked[0].title, "Official source")

    def test_preserve_formatting_keeps_code_blocks_for_url_read(self) -> None:
        results = [
            SearchResult(
                title="GitHub file",
                url="https://github.com/example/repo/blob/main/app.py",
                snippet="",
                full_content="## File: app.py\n```python\n\ndef hello():\n    return 'hi'\n```",
                source_provider="github_api",
            )
        ]

        extracted_facts, extracted_citations = extract_facts_and_citations(
            results,
            mode=SearchMode.URL_READ,
            max_snippet_chars=500,
            preserve_formatting=True,
        )

        self.assertIn("```python", extracted_facts[0])
        self.assertIn("\ndef hello():", extracted_facts[0])
        self.assertEqual(extracted_citations[0]["source_provider"], "github_api")


if __name__ == "__main__":
    unittest.main()
