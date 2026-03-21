"""
Data models for PigTex Agentic Search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SearchIntent(Enum):
    """Why the system decided to search."""
    REALTIME_INFO = "realtime"       # News, prices, weather, scores
    FACTUAL_CHECK = "factual"        # Verify facts, stats with recency
    DEEP_RESEARCH = "research"       # Compare, review, benchmark
    URL_READ = "url_read"            # User pasted a link to read
    NO_SEARCH = "none"               # No search needed


class SearchMode(Enum):
    """Search execution mode requested by caller."""
    AUTO = "auto"
    REALTIME = "realtime"
    DEEP_VERIFY = "deep_verify"
    URL_READ = "url_read"


class ClaimVerdict(Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    MIXED = "mixed"
    INSUFFICIENT = "insufficient"


@dataclass
class SearchQuery:
    """A single search query to execute."""
    query: str
    topic: str = "general"           # general | news
    priority: int = 1                # 1 = highest
    freshness_days: Optional[int] = None
    claim: Optional[str] = None


@dataclass
class SearchResult:
    """A single search result from a provider."""
    title: str
    url: str
    snippet: str                     # Short summary / extracted content
    full_content: Optional[str] = None  # Full page markdown (if deep-read)
    relevance_score: float = 0.0
    source_provider: str = "tavily"
    published_at: Optional[str] = None
    domain: str = ""
    recency_score: float = 0.0
    credibility_score: float = 0.0


@dataclass
class ClaimVerification:
    """Verification output for a single claim."""
    claim: str
    verdict: ClaimVerdict = ClaimVerdict.INSUFFICIENT
    confidence: float = 0.0
    evidence_count: int = 0
    supporting_sources: List[int] = field(default_factory=list)
    contradicting_sources: List[int] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict:
        return {
            "claim": self.claim,
            "verdict": self.verdict.value,
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 3),
            "evidence_count": int(self.evidence_count),
            "supporting_sources": self.supporting_sources,
            "contradicting_sources": self.contradicting_sources,
            "summary": self.summary,
        }


@dataclass
class SearchContext:
    """
    Fully processed search output, ready for injection into LLM context.
    """

    facts: List[str] = field(default_factory=list)
    citations: List[Dict] = field(default_factory=list)  # [{index, title, url, ...}]
    raw_results_count: int = 0
    search_queries: List[str] = field(default_factory=list)
    search_intent: SearchIntent = SearchIntent.NO_SEARCH
    total_search_time_ms: int = 0

    mode: SearchMode = SearchMode.AUTO
    checked_at_utc: str = field(default_factory=_utc_now_iso)
    confidence_score: float = 0.0
    conflicts_count: int = 0
    claims_verified_count: int = 0
    claim_verification: List[ClaimVerification] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_results(self) -> bool:
        return bool(self.facts or self.citations)

    def to_prompt_section(self) -> str:
        """Format as a text block to inject into the system prompt."""
        if not self.has_results and not self.warnings:
            return ""

        lines = ["## Web Search Evidence\n"]
        lines.append(
            f"Checked at (UTC): {self.checked_at_utc}\n"
            f"Mode: {self.mode.value}\n"
            "Treat evidence as factual observations from sources. "
            "Clearly separate factual statements from your inference. "
            "If sources conflict, mention the conflict instead of forcing a single conclusion."
        )

        if self.warnings:
            lines.append("\nImportant notes:")
            for warning in self.warnings:
                lines.append(f"- {warning}")

        if self.facts:
            lines.append("\nEvidence snippets:")
            for i, fact in enumerate(self.facts, 1):
                lines.append(f"[{i}] {fact}")

        if self.claim_verification:
            lines.append("\nClaim verification summary:")
            for item in self.claim_verification:
                lines.append(
                    f"- {item.claim} -> {item.verdict.value} "
                    f"(confidence={max(0.0, min(1.0, item.confidence)):.2f})"
                )

        if self.citations:
            lines.append("\nSources:")
            for cite in self.citations:
                title = str(cite.get("title") or "Untitled source").strip()
                url = str(cite.get("url") or "").strip()
                if not url:
                    continue
                lines.append(f"[{cite.get('index')}] {title} - {url}")

        lines.append("\nAlways cite sources with [number] notation where applicable.")

        return "\n".join(lines)

    def to_citations_list(self) -> Optional[List[Dict]]:
        """Return citations for the API response body."""
        return self.citations if self.citations else None
