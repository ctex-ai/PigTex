"""
PigTex Agentic Search — realtime web search + deep verification pipeline.

Automatically detects when a user question needs web search,
plans queries, searches, deep-reads pages, verifies claims across
sources, and injects evidence into AI context with citations.
"""

from .search_coordinator import SearchCoordinator
from .models import SearchContext, SearchIntent

__all__ = ["SearchCoordinator", "SearchContext", "SearchIntent"]
