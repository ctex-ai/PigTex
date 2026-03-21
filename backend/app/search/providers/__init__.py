"""Search providers — pluggable backends for web search and scraping."""

from .broker import SearchBroker
from .duckduckgo import DuckDuckGoSearchProvider
from .github_reader import GitHubReaderProvider
from .jina_reader import JinaReaderProvider
from .tavily import TavilySearchProvider

__all__ = [
    "DuckDuckGoSearchProvider",
    "GitHubReaderProvider",
    "JinaReaderProvider",
    "SearchBroker",
    "TavilySearchProvider",
]
