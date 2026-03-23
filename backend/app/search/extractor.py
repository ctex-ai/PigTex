"""
Result extraction helpers for web search.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urlparse

from .models import ClaimVerdict, ClaimVerification, SearchMode, SearchResult

_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "with", "by",
    "la", "is", "are", "was", "were", "be", "been", "this", "that", "it", "as", "from",
    "cua", "la", "va", "voi", "cho", "nhung", "nhat", "moi", "hien", "tai", "thong", "tin",
}

_HIGH_TRUST_DOMAINS = {
    "reuters.com": 0.95,
    "apnews.com": 0.93,
    "bloomberg.com": 0.92,
    "ft.com": 0.91,
    "wsj.com": 0.91,
    "nytimes.com": 0.89,
    "bbc.com": 0.90,
    "wikipedia.org": 0.82,
}

_MEDIUM_TRUST_HINTS = ("gov", "edu", "org", "who.int", "imf.org", "worldbank.org", "oecd.org")
_LOW_TRUST_HINTS = ("blogspot.", "wordpress.", "tumblr.", "medium.com", "reddit.com")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_PRICE_KEYWORD_RE = re.compile(
    r"(?i)(?:\b(?:price|pricing|cost|quote|quoted|rate|fee|fees|how much|bao nhieu|gia|phi|ty gia)\b|giá|phí|tỷ giá)"
)
_PRICE_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"[$€£¥₫]\s?\d[\d,.\s]{0,24}"
    r"|"
    r"\d[\d,.\s]{0,24}\s?(?:usd|eur|gbp|vnd|vnđ|đ|₫|jpy|cny|btc|eth|triệu|trieu|nghìn|nghin|k|m|b|\/kg|\/g|\/oz)"
    r"|"
    r"\d[\d,.\s]{0,18}(?:\s?[-–]\s?\d[\d,.\s]{0,18})\s?(?:usd|eur|gbp|vnd|vnđ|đ|₫|triệu|trieu|nghìn|nghin)"
    r")"
)
_DATE_VALUE_RE = re.compile(r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]20\d{2})\b")

_NEGATION_HINTS = (
    "not true", "false", "incorrect", "misleading", "debunk", "rumor",
    "khong", "không", "sai", "chua", "chưa", "tin don", "tin đồn",
)


def _normalize_text(text: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", (text or "").strip())
    return normalized.strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max_chars - 3].rstrip()
    return f"{clipped}..."


def _normalize_multiline_text(text: str) -> str:
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines: List[str] = []
    blank_streak = 0
    for raw_line in raw_lines:
        line = raw_line.rstrip()
        if not line.strip():
            blank_streak += 1
            if blank_streak > 1:
                continue
            cleaned_lines.append("")
            continue
        blank_streak = 0
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _is_price_focus(focus: str | None) -> bool:
    return (focus or "").strip().lower() == "price"


def _format_excerpt(text: str, *, preserve_formatting: bool) -> str:
    if preserve_formatting:
        return _normalize_multiline_text(text)
    return _normalize_text(text)


def _extract_price_excerpt(raw_text: str, *, max_chars: int, preserve_formatting: bool) -> str:
    segments: List[Tuple[int, str]] = []
    for raw_segment in _SENTENCE_SPLIT_RE.split(raw_text or ""):
        candidate = raw_segment.strip(" \t\r\n-•")
        if len(candidate) < 8:
            continue
        score = 0
        if _PRICE_VALUE_RE.search(candidate):
            score += 5
        if _PRICE_KEYWORD_RE.search(candidate):
            score += 3
        if _DATE_VALUE_RE.search(candidate):
            score += 1
        if score > 0:
            segments.append((score, candidate))

    if not segments:
        return _truncate(_format_excerpt(raw_text, preserve_formatting=preserve_formatting), max_chars)

    segments.sort(key=lambda item: (item[0], len(item[1])), reverse=True)

    chosen: List[str] = []
    seen: set[str] = set()
    total_chars = 0
    joiner = "\n" if preserve_formatting else " "
    for _, segment in segments:
        normalized = _format_excerpt(segment, preserve_formatting=preserve_formatting)
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        projected = total_chars + len(normalized) + (len(joiner) if chosen else 0)
        if chosen and projected > max_chars:
            continue
        seen.add(lowered)
        chosen.append(normalized)
        total_chars = projected
        if len(chosen) >= 2 or total_chars >= int(max_chars * 0.75):
            break

    if not chosen:
        return _truncate(_format_excerpt(raw_text, preserve_formatting=preserve_formatting), max_chars)

    return _truncate(joiner.join(chosen), max_chars)


def _tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in _TOKEN_RE.findall(text or "")}
    return {token for token in tokens if token and token not in _STOPWORDS and len(token) >= 2}


def infer_domain(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower().strip()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _parse_timestamp(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass

    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            continue
    return None


def compute_domain_credibility(domain: str) -> float:
    normalized = (domain or "").lower().strip()
    if not normalized:
        return 0.35

    for trusted_domain, score in _HIGH_TRUST_DOMAINS.items():
        if normalized == trusted_domain or normalized.endswith(f".{trusted_domain}"):
            return score

    if normalized.endswith(".gov") or normalized.endswith(".edu"):
        return 0.92

    if any(hint in normalized for hint in _LOW_TRUST_HINTS):
        return 0.48

    if any(hint in normalized for hint in _MEDIUM_TRUST_HINTS):
        return 0.74

    return 0.62


def compute_recency_score(published_at: str | None, mode: SearchMode) -> float:
    if not published_at:
        return 0.55 if mode == SearchMode.REALTIME else 0.45

    published_dt = _parse_timestamp(published_at)
    if not published_dt:
        return 0.5

    age_days = max(0.0, (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400.0)

    if age_days <= 1:
        score = 1.0
    elif age_days <= 7:
        score = 0.92
    elif age_days <= 30:
        score = 0.78
    elif age_days <= 90:
        score = 0.62
    elif age_days <= 365:
        score = 0.42
    else:
        score = 0.25

    if mode == SearchMode.DEEP_VERIFY:
        # Deep verify still cares about recency, but source quality matters more.
        score = 0.2 + (score * 0.8)

    return max(0.0, min(1.0, score))


def _normalize_relevance(raw_score: float) -> float:
    if raw_score <= 0:
        return 0.0
    if raw_score <= 1.0:
        return float(raw_score)
    # Some providers return >1 scores. Normalize gently.
    return max(0.0, min(1.0, float(raw_score) / 10.0))


def _matches_preferred_domain(domain: str, preferred_domains: Sequence[str] | None) -> bool:
    normalized_domain = (domain or "").strip().lower()
    if not normalized_domain or not preferred_domains:
        return False
    for preferred in preferred_domains:
        normalized_preferred = (preferred or "").strip().lower()
        if not normalized_preferred:
            continue
        if normalized_domain == normalized_preferred or normalized_domain.endswith(f".{normalized_preferred}"):
            return True
    return False


def _preferred_domain_boost(domain: str, preferred_domains: Sequence[str] | None) -> float:
    normalized_domain = (domain or "").strip().lower()
    if not normalized_domain or not preferred_domains:
        return 0.0

    ordered_boosts = (0.18, 0.13, 0.09, 0.06, 0.04)
    for index, preferred in enumerate(preferred_domains):
        normalized_preferred = (preferred or "").strip().lower()
        if not normalized_preferred:
            continue
        if normalized_domain == normalized_preferred or normalized_domain.endswith(f".{normalized_preferred}"):
            return ordered_boosts[min(index, len(ordered_boosts) - 1)]
    return 0.0


def compute_result_score(
    result: SearchResult,
    mode: SearchMode,
    preferred_domains: Sequence[str] | None = None,
) -> float:
    relevance = _normalize_relevance(result.relevance_score)
    credibility = result.credibility_score or compute_domain_credibility(result.domain)
    recency = result.recency_score or compute_recency_score(result.published_at, mode)

    if mode == SearchMode.REALTIME:
        score = (0.45 * relevance) + (0.25 * credibility) + (0.30 * recency)
    elif mode == SearchMode.DEEP_VERIFY:
        score = (0.40 * relevance) + (0.45 * credibility) + (0.15 * recency)
    else:
        score = (0.45 * relevance) + (0.35 * credibility) + (0.20 * recency)

    preference_boost = _preferred_domain_boost(result.domain, preferred_domains)
    if preference_boost > 0:
        score += preference_boost * (1.05 if mode == SearchMode.DEEP_VERIFY else 1.0)

    return max(0.0, min(1.0, score))


def enrich_and_rank_results(
    results: Iterable[SearchResult],
    mode: SearchMode,
    preferred_domains: Sequence[str] | None = None,
) -> List[SearchResult]:
    prepared: List[Tuple[float, SearchResult]] = []
    for result in results:
        domain = infer_domain(result.url)
        result.domain = domain
        result.credibility_score = compute_domain_credibility(domain)
        result.recency_score = compute_recency_score(result.published_at, mode)
        prepared.append((compute_result_score(result, mode, preferred_domains=preferred_domains), result))

    prepared.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in prepared]


def extract_facts_and_citations(
    results: Iterable[SearchResult],
    max_facts: int = 6,
    max_snippet_chars: int = 280,
    mode: SearchMode = SearchMode.AUTO,
    preferred_domains: Sequence[str] | None = None,
    preserve_formatting: bool = False,
    focus: str | None = None,
) -> Tuple[List[str], List[Dict]]:
    """
    Convert raw results into concise factual lines + structured citations.
    """
    facts: List[str] = []
    citations: List[Dict] = []
    seen_urls: set[str] = set()

    ranked_results = enrich_and_rank_results(results, mode=mode, preferred_domains=preferred_domains)

    for result in ranked_results:
        if len(facts) >= max(1, int(max_facts)):
            break
        url = (result.url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        raw_snippet = result.full_content or result.snippet or ""
        if _is_price_focus(focus):
            snippet = _extract_price_excerpt(
                raw_snippet,
                max_chars=max(120, int(max_snippet_chars)),
                preserve_formatting=preserve_formatting,
            )
        elif preserve_formatting:
            snippet = _truncate(_normalize_multiline_text(raw_snippet), max(120, int(max_snippet_chars)))
        else:
            snippet = _truncate(_normalize_text(raw_snippet), max(80, int(max_snippet_chars)))
        if not snippet:
            continue

        title = (result.title or "Untitled source").strip()
        domain = result.domain or infer_domain(url)
        published_at = (result.published_at or "").strip()

        source_label = title
        if domain:
            source_label = f"{source_label} ({domain})"
        if published_at:
            source_label = f"{source_label} [{published_at}]"

        facts.append(f"{source_label}: {snippet}")
        citations.append(
            {
                "index": len(citations) + 1,
                "title": title,
                "url": url,
                "domain": domain or None,
                "published_at": published_at or None,
                "snippet": snippet,
                "source_provider": result.source_provider,
                "relevance_score": round(_normalize_relevance(result.relevance_score), 3),
                "credibility_score": round(max(0.0, min(1.0, result.credibility_score)), 3),
                "recency_score": round(max(0.0, min(1.0, result.recency_score)), 3),
                "preferred_domain_match": _matches_preferred_domain(domain or "", preferred_domains),
            }
        )

    return facts, citations


def _is_likely_contradiction(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _NEGATION_HINTS)


def _build_claim_summary(
    verdict: ClaimVerdict,
    support_count: int,
    contradict_count: int,
) -> str:
    if verdict == ClaimVerdict.SUPPORTED:
        return f"Supported by {support_count} source(s)."
    if verdict == ClaimVerdict.CONTRADICTED:
        return f"Contradicted by {contradict_count} source(s)."
    if verdict == ClaimVerdict.MIXED:
        return f"Conflicting evidence ({support_count} support / {contradict_count} contradict)."
    return "Insufficient corroborated evidence."


def _safe_unique_domains(citations: Sequence[Dict], indexes: Sequence[int]) -> set[str]:
    by_index = {int(item.get("index")): item for item in citations if isinstance(item, dict)}
    domains: set[str] = set()
    for index in indexes:
        item = by_index.get(int(index))
        if not item:
            continue
        domain = str(item.get("domain") or "").strip().lower()
        if domain:
            domains.add(domain)
    return domains


def build_claim_verification(
    claims: Sequence[str],
    citations: Sequence[Dict],
    min_sources_per_claim: int = 2,
) -> Tuple[List[ClaimVerification], float, int]:
    """
    Build per-claim verification summary from extracted citations.
    """
    outputs: List[ClaimVerification] = []
    if not claims:
        return outputs, 0.0, 0

    conflicts_count = 0

    for claim in claims:
        normalized_claim = _normalize_text(claim)
        if not normalized_claim:
            continue

        claim_tokens = _tokenize(normalized_claim)
        support_indexes: List[int] = []
        contradict_indexes: List[int] = []
        matched_indexes: List[int] = []

        for cite in citations:
            if not isinstance(cite, dict):
                continue
            index = int(cite.get("index") or 0)
            if index <= 0:
                continue
            text = _normalize_text(
                f"{cite.get('title') or ''} {cite.get('snippet') or ''}"
            ).lower()
            if not text:
                continue

            cite_tokens = _tokenize(text)
            if claim_tokens:
                overlap = len(claim_tokens.intersection(cite_tokens))
                overlap_ratio = overlap / max(1, len(claim_tokens))
            else:
                overlap_ratio = 0.0

            if overlap_ratio < 0.2 and normalized_claim.lower() not in text:
                continue

            matched_indexes.append(index)
            if _is_likely_contradiction(text):
                contradict_indexes.append(index)
            else:
                support_indexes.append(index)

        support_count = len(set(support_indexes))
        contradict_count = len(set(contradict_indexes))
        evidence_count = len(set(matched_indexes))

        if support_count > 0 and contradict_count > 0:
            verdict = ClaimVerdict.MIXED
            conflicts_count += 1
        elif contradict_count > 0 and support_count == 0:
            verdict = ClaimVerdict.CONTRADICTED
        elif support_count > 0:
            verdict = ClaimVerdict.SUPPORTED
        else:
            verdict = ClaimVerdict.INSUFFICIENT

        unique_support_domains = _safe_unique_domains(citations, support_indexes)
        unique_contradict_domains = _safe_unique_domains(citations, contradict_indexes)

        if verdict == ClaimVerdict.SUPPORTED:
            confidence = 0.5 + min(0.25, support_count * 0.1)
            confidence += min(0.15, max(0, len(unique_support_domains) - 1) * 0.08)
            if support_count < max(1, min_sources_per_claim):
                confidence -= 0.1
        elif verdict == ClaimVerdict.CONTRADICTED:
            confidence = 0.5 + min(0.25, contradict_count * 0.1)
            confidence += min(0.15, max(0, len(unique_contradict_domains) - 1) * 0.08)
        elif verdict == ClaimVerdict.MIXED:
            confidence = 0.35 + min(0.2, evidence_count * 0.05)
        else:
            confidence = 0.15 if evidence_count == 0 else 0.25

        confidence = max(0.0, min(1.0, confidence))

        outputs.append(
            ClaimVerification(
                claim=normalized_claim,
                verdict=verdict,
                confidence=confidence,
                evidence_count=evidence_count,
                supporting_sources=sorted(set(support_indexes)),
                contradicting_sources=sorted(set(contradict_indexes)),
                summary=_build_claim_summary(verdict, support_count, contradict_count),
            )
        )

    if not outputs:
        return outputs, 0.0, conflicts_count

    overall_confidence = sum(item.confidence for item in outputs) / len(outputs)
    return outputs, round(max(0.0, min(1.0, overall_confidence)), 3), conflicts_count
